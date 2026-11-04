from __future__ import annotations

import asyncio
import inspect
import logging
import os
import signal
import sys
import time
from typing import Optional

import websockets
import websockets.exceptions
from aiohttp import web

from src.api import create_api_app
from src.auth_key import AuthKey, refused_status
from src.client import ChargePoint
from src.debug_tools import DebugTools, install_log_buffer
from src.app_settings import AppSettings
from src.config import load_config, starting_current_amps
from src.gui_data import GuiSources, Health, PowerHistory, sample_loop
from src.ha_history import ChartHistory
from src.automation import AUTO_SOURCE, CHARGE_NOW_SOURCE, Automation, auto_plug_charge, automation_loop, SLOT_LEAD_MIN
from src.ha_link import HaLink
from src import auto_schedule, log_filters, migrate, schedule_notify, suppliers
from src.persistence import Persistence
from src.shared_state import SharedState

logger = logging.getLogger("ocpp_charge_proxy")

SAVING_READ_S = 300  # the saving sessions calendar is read this often
SAVING_TICK_S = 30  # and the schedule checked against it this often
# Plug-ins and unplugs done by hand: they still happen in read only mode (Settings)
HAND_SOURCES = ("web page", "Home Assistant", "charge now", "charge now cancelled")
AUTO_READ_S = 120  # the automatic schedule's rates are read this often
AUTO_TICK_S = 15  # and it's checked (planned again if they or the schedule changed) this often
AUTO_WAIT_SUPPLIER_S = 180  # after a start, how long it waits for the supplier's integration before planning without it
# No User-Agent on the connection: the library's own ("Python/3.x websockets/x")
# isn't what a charger sends, and chargers usually send none. Only passed if this
# websockets version has the option (otherwise it couldn't connect at all).
_NO_USER_AGENT: dict = {}
try:
    if "user_agent_header" in inspect.signature(websockets.connect).parameters:
        _NO_USER_AGENT["user_agent_header"] = None
except (TypeError, ValueError):
    pass
BACKOFF_STEPS = [5, 10, 30, 60]  # then every 60 s: back soon after an outage
# The simulated car: wait after StartTransaction, then ramp up (Settings tab)
DEFAULT_START_DELAY_S = 3.0
DEFAULT_RAMP_UP_S = 5.0

# s6-overlay gives services ~3s after SIGTERM before SIGKILL, so the goodbye
# messages to the server must fit comfortably inside that window.
SHUTDOWN_STOP_TX_TIMEOUT = 1.5
SHUTDOWN_STATUS_TIMEOUT = 1.0


async def _graceful_shutdown(cp: ChargePoint) -> None:
    """Tell the server we're going away, while the socket is still open."""
    from ocpp.v16.enums import ChargePointStatus as CPS, Reason

    if cp.suspend_for_restart():
        # Like a charger losing its connection: no StopTransaction and no
        # Unavailable, so the server can carry on with the session after
        # the restart (src/client.py)
        return
    online = cp.is_online
    logger.info(
        "Shutting down: %s",
        "notifying OCPP server" if online
        else "offline, StopTransaction will be held and sent on next connection",
    )
    if cp._transaction_id is not None:
        try:
            await asyncio.wait_for(
                cp._do_stop_transaction(
                    final_state=CPS.unavailable, reason=Reason.reboot,
                ),
                SHUTDOWN_STOP_TX_TIMEOUT,
            )
        except Exception:
            logger.warning("Could not stop transaction during shutdown", exc_info=True)
    if not cp.is_online:
        return
    try:
        await asyncio.wait_for(cp.send_status_unavailable(), SHUTDOWN_STATUS_TIMEOUT)
        logger.info("Sent Unavailable status to server")
    except Exception:
        logger.warning("Could not send Unavailable status during shutdown", exc_info=True)


async def _cancel_all(tasks) -> None:
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


async def run() -> None:
    config = load_config()

    data_dir = os.environ.get("IO_DATA_DIR", "/data")
    os.makedirs(data_dir, exist_ok=True)
    # Settings imported from another install (Diagnostics › Move to another install), used
    # before anything reads /data (src/migrate.py); logged once logging is set up, below
    imported = migrate.apply_pending(data_dir, suppliers.OVERRIDE_PATH)
    if imported:
        suppliers.refresh(force=True)
    persistence = Persistence(data_dir=data_dir)
    # Log level and continue session: set on the web page, no longer add-on options
    app_settings = AppSettings(persistence)

    log_level = getattr(logging, app_settings.log_level.upper(), logging.INFO)
    log_format = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"

    logging.basicConfig(
        level=log_level,
        format=log_format,
        stream=sys.stdout,
    )
    log_filters.install()  # Heartbeat and periodic MeterValues lines at DEBUG, not INFO
    for line in imported:
        logger.info("Settings from another install: %s", line)
    log_buffer = install_log_buffer(log_format)  # recent lines for Diagnostics › Debug

    logger.info("Starting OCPP Charge Proxy, connecting to %s", config.redacted_url)
    # The supplier defaults, to copy your changes from (src/suppliers.py)
    if suppliers.write_example():
        logger.debug("Supplier defaults copied to %s/suppliers.example.json", os.path.dirname(suppliers.OVERRIDE_PATH))

    # Seed energy register if initial_energy_wh is set (cleared by s6 run script)
    if config.initial_energy_wh > 0:
        if persistence.seed_energy_register_wh(config.initial_energy_wh):
            logger.info("Energy register set to %d Wh from config", config.initial_energy_wh)
        else:
            logger.warning(
                "initial_energy_wh=%d ignored: stored energy register is already "
                "%d Wh and the meter must never go backwards.",
                config.initial_energy_wh, persistence.load_energy_register_wh(),
            )

    shared_state = SharedState()
    # Report the stored energy register from the start. The API comes up
    # before the first meter reading; serving the default 0 meant HA's
    # total_increasing Energy sensor saw stored -> 0 -> stored on every restart
    # and recorded the whole register as new consumption.
    shared_state.energy_kwh = persistence.load_energy_register_wh() / 1000.0

    # SIGTERM/SIGINT are handled inside the event loop so shutdown runs as
    # normal async code (a plain signal.signal handler raising SystemExit
    # fires outside our coroutines and kills the process before we can
    # notify the server).
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    def _on_signal(sig: signal.Signals) -> None:
        logger.info("%s received", sig.name)
        stop_event.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _on_signal, sig)
        except (NotImplementedError, RuntimeError):
            pass  # e.g. Windows dev runs — fall back to default behaviour

    stop_task = asyncio.create_task(stop_event.wait())

    # --- Command callbacks for the API ---
    def read_only(action: str, source: str) -> bool:
        """Settings › Read only: nothing plugs in or unplugs by itself (the schedule, auto
        re-plug, auto plug-in, the car plugged in sensor, off-peak times...). What you do by
        hand (the page, the Plugged In helper, Charge now) still does. Logged, not done."""
        if source in HAND_SOURCES or not ha_link.settings.get("read_only"):
            return False
        logger.info("Read only: would %s now (%s), but doesn't", action, source)
        return True

    async def ready_time_guarded(unplug):
        """Your supplier's ready time: not changed in read only mode."""
        if ha_link.settings.get("read_only"):
            logger.info("Read only: would set the ready time to %s, but doesn't", unplug.strftime("%a %H:%M"))
            return {"unplug": unplug.isoformat(timespec="minutes"), "error": "Read only: not set", "permanent": True}
        return await ha_link.set_ready_time(unplug)

    async def do_plug(source: str = "web page"):
        from ocpp.v16.enums import ChargePointStatus as CPS
        if cp is None or read_only("plug in", source):
            return
        # Already plugged in (or charging): nothing to do
        if cp.state in (CPS.preparing, CPS.charging, CPS.suspended_ev, CPS.suspended_evse):
            return
        cp.state = CPS.preparing
        shared_state.state = cp.state
        cp.set_plugged_in(True, source)
        try:
            await cp.send_status()
        except Exception:
            logger.warning("Failed to send status after plug", exc_info=True)

    async def do_unplug(source: str = "web page"):
        from ocpp.v16.enums import ChargePointStatus as CPS
        if cp is None or read_only("unplug", source):
            return
        # Unplugged straight away, so the page and Home Assistant see it
        cp.set_plugged_in(False, source)
        shared_state.state = CPS.available
        if cp._transaction_id is not None:
            # final_state, so the stop doesn't put the connector back to Preparing
            from ocpp.v16.enums import Reason
            await cp._do_stop_transaction(
                final_state=CPS.available, reason=Reason.ev_disconnected,
            )
        cp.state = CPS.available
        shared_state.state = cp.state
        try:
            await cp.send_status()
        except Exception:
            logger.warning("Failed to send status after unplug", exc_info=True)

    async def do_set_current(amps: int):
        if cp is not None:
            cp.set_max_current(amps)

    async def do_set_power(power_kw: float | None):
        if cp is not None:
            cp.set_power_override(power_kw)

    async def do_set_soc(soc: float | None):
        if cp is not None:
            cp.set_soc(soc)
            try:
                await cp._apply_profile_state()  # car full / no longer full
            except Exception:
                logger.warning("Failed to apply SoC change", exc_info=True)

    # One ChargePoint for the life of the process: like a real charger it
    # keeps charging (and its transaction) across a dropped connection, and
    # holds transaction messages until it can send them.
    cp = ChargePoint(
        id=config.chargepoint_id,
        connection=None,
        persistence=persistence,
        current_amps=starting_current_amps(persistence),
        resume_on_restart=app_settings.continue_session,
        shared_state=shared_state,
        start_delay_s=DEFAULT_START_DELAY_S,  # until set on the Settings tab
        ramp_up_s=DEFAULT_RAMP_UP_S,
    )
    # The connection password, as your supplier may change it (src/auth_key.py)
    auth = AuthKey(persistence, config.password)
    cp.on_auth_key = auth.set_new
    cp.auth_status = lambda: {"rotated": auth.rotated}

    # Something new from your supplier: a notification, if they're on (Settings › Notifications)
    def announce_news(item: dict) -> None:
        if not (ha_link.settings.get("notify_supplier") and ha_link.settings.get("notify_service")):
            return
        text = f"{item['what']}: {item['name']}" + (f" ({item['detail']})" if item.get("detail") else "")
        asyncio.get_running_loop().create_task(
            ha_link.notify("Something new from your supplier", text + "\nDiagnostics › Health › Supplier", force=True))
    cp.news.on_new = announce_news
    health = Health()
    history = PowerHistory()  # the chart's last hour; older comes from HA's history
    automation = Automation(data_dir)  # the schedule and auto re-plug: Automations tab
    automation.publish(shared_state)
    # Your HA sensors (power, SoC, car plugged in: Settings tab; auto
    # plug-in: Automations tab) and the add-on's own HA entities (Plugged In helper, Power, Energy,
    # Current)
    ha_link = HaLink(
        data_dir, shared_state,
        set_power=cp.set_power_override,
        set_soc=do_set_soc,
        plug=do_plug,
        unplug=do_unplug,
    )
    cp.set_voltage_source(ha_link.voltage)  # Settings › Voltage: a sensor, a set value, or 230 V
    # Settings › Next slot sensors: each slot dropped this long before it starts
    automation.slot_lead = lambda: int(ha_link.settings.get("slot_lead_min", SLOT_LEAD_MIN))

    def _health_info() -> dict:
        info = health.snapshot()
        info["held_messages"] = cp.held_messages_info()
        info["last_heartbeat"] = shared_state.last_heartbeat
        info["connected_to_server"] = shared_state.connected_to_server
        info["server"] = config.server_hostname
        info["chargepoint_id"] = config.chargepoint_id
        info["home_assistant"] = {"available": ha_link.available, "connected": ha_link.connected, "error": ha_link.error}
        info["smart_charging"] = ha_link.smart_charging_health()
        return info

    charts = ChartHistory(ha_link, soc_entity=lambda: ha_link.settings.get("soc_entity") or None, recent=history)
    gui = GuiSources(
        message_log=cp.message_log,
        history=charts,
        sessions=cp.sessions_info,
        provider=cp.provider_info,
        health=_health_info,
    )
    async def plan_charge(hours: float, source: str = AUTO_SOURCE, dry_run: bool = False) -> dict:
        sc = ha_link.smart_charging()
        automation.limit_reset = sc.get("limit_reset")  # e.g. Octopus: 12:00
        plan = await auto_plug_charge(
            automation, hours,
            set_ready_time=ready_time_guarded if sc.get("found") else None,
            ready_times=ha_link.ready_times,
            # Your supplier's daily limit (Octopus: 6 hours, or as set on the
            # Settings tab): kept to only while Force schedule on supplier is
            # on, as for the schedule
            cap_min=(sc.get("limit_min") if automation.ready_time and not automation.override_guards else None),
            provider=sc.get("provider") or "your supplier",
            source=source,
            dry_run=dry_run,
        )
        if not dry_run:
            automation.publish(shared_state)
        return plan

    async def charge(hours: float, source: str, plug_source: str, confirm: bool = True) -> dict:
        """Plug in and add the charge to the schedule (auto plug-in, the car
        plugged in sensor, Charge now). Not plugged in during an off-peak
        time: it plugs in when that ends, if the charge still has time left.
        Charge now asks first (confirm) if it would go over the daily limit."""
        preview = await plan_charge(hours, source, dry_run=True)
        over = preview.get("over")
        if not confirm and over and over["added"] > 0:
            return {"needs_confirm": True, "plan": preview}
        if preview.get("plug_now"):
            await do_plug(source=plug_source)
        elif shared_state.plugged_in:
            await do_unplug(source="off-peak time")
        return {"plan": await plan_charge(hours, source)}

    async def on_auto_plug(hours: float, source: str = AUTO_SOURCE, plug_source: str = "auto plug-in (low SoC)") -> None:
        await charge(hours, source, plug_source)

    async def charge_now(hours: float, confirm: bool = False) -> dict:
        """The Charge now button: as auto plug-in, but asks first if it would
        take any 24 hours over the daily limit (the page shows what'd happen)."""
        return await charge(hours, CHARGE_NOW_SOURCE, "charge now", confirm)

    async def cancel_charge_now() -> dict:
        """Charge now's Cancel: out of the schedule, and unplugged unless a slot (or
        another charge) has the car plugged in now."""
        removed = automation.cancel_charge(CHARGE_NOW_SOURCE)
        unplugged = False
        if removed and shared_state.plugged_in and automation.in_plug_window() is None \
                and automation.in_charge_window() is None:
            await do_unplug(source="charge now cancelled")
            unplugged = True
        automation.publish(shared_state)
        return {"removed": removed, "unplugged": unplugged}

    ha_link.on_auto_plug = on_auto_plug

    async def saving_sessions_loop() -> None:
        """Saving sessions (Automations tab): read the calendar every few
        minutes (and straight after a setting changes), keep the schedule
        clear of them, and add a charge before each to the schedule if that's on."""
        last_read, sessions, last_settings = 0.0, None, None
        while True:
            try:
                s = ha_link.settings
                if not s.get("saving_on"):
                    automation.saving_past = []
                    if automation.clear_saving_sessions():
                        automation.publish(shared_state)
                    sessions, last_settings = None, None
                else:
                    wanted = (s.get("saving_entity"), s.get("saving_block"), s.get("saving_plug"), s.get("saving_plug_hours"))
                    if wanted != last_settings or time.monotonic() - last_read >= SAVING_READ_S:
                        found = await ha_link.read_saving_sessions()
                        last_read, last_settings = time.monotonic(), wanted
                        if found is not None:
                            sessions = found
                            automation.saving_past = ha_link.saving_past
                    sc = ha_link.smart_charging()
                    automation.limit_reset = sc.get("limit_reset")
                    cap = sc.get("limit_min") if automation.ready_time and not automation.override_guards else None
                    plug_hours = float(s.get("saving_plug_hours") or 2) if s.get("saving_plug") else None
                    if automation.sync_saving_sessions(sessions, bool(s.get("saving_block")), ha_link.ready_times,
                                                       cap, sc.get("provider") or "your supplier", plug_hours):
                        automation.publish(shared_state)
            except Exception:
                logger.warning("Saving sessions cycle failed", exc_info=True)
            await asyncio.sleep(SAVING_TICK_S)
    async def auto_schedule_loop() -> None:
        """The automatic schedule (Automations tab): read the import and export rates
        every few minutes (and straight after a setting changes), and plan again when
        they, the number of half hours or the rest of the schedule change."""
        last_read, last_settings, imp, exp, info = 0.0, None, [], [], {}
        started = time.monotonic()
        while True:
            try:
                s = ha_link.settings
                on = bool(s.get("auto_sched_on"))
                if not on and automation.clear_auto_schedule():
                    automation.publish(shared_state)
                # (the rates are read on or off: off, the page shows what it would do)
                wanted = (s.get("auto_import_entity"), s.get("auto_export_entity"))
                if wanted != last_settings or time.monotonic() - last_read >= AUTO_READ_S:
                    states = await ha_link.all_states()
                    if states:
                        found = auto_schedule.find_rate_entities(states)
                        pick = lambda key, kind: (auto_schedule.entities_for(s[key], states) if s.get(key)
                                                  else found[kind])
                        ids = {"import": pick("auto_import_entity", "import"), "export": pick("auto_export_entity", "export")}
                        by_id = {st.get("entity_id"): st for st in states}
                        imp = auto_schedule.merge(*(auto_schedule.parse_rates(by_id.get(e)) for e in ids["import"]))
                        exp = auto_schedule.merge(*(auto_schedule.parse_rates(by_id.get(e)) for e in ids["export"]))
                        info = {**ids, "source": None if (s.get("auto_import_entity") or s.get("auto_export_entity"))
                                else found["source"]}
                        last_read, last_settings = time.monotonic(), wanted
                sc = ha_link.smart_charging()
                if not sc.get("found") and time.monotonic() - started < AUTO_WAIT_SUPPLIER_S:
                    # (just started: not planned until your supplier's integration, and with it
                    # its daily limit, is found, or it's clear there isn't one)
                    await asyncio.sleep(AUTO_TICK_S)
                    continue
                automation.limit_reset = sc.get("limit_reset")
                # Your supplier's daily limit always applies to it (each limit day, e.g.
                # 12:00 to 12:00), whether or not Force schedule on supplier is on
                cap = sc.get("limit_min") if not automation.override_guards else None
                rates_changed = automation.set_export_rates(exp, info, int(s.get("auto_high_blocks") or 0))  # (shown on the schedule, on or off)
                plan = automation.sync_auto_schedule if on else automation.preview_auto_schedule
                if plan(imp, exp, int(s.get("auto_sched_blocks") or 12), cap,
                        sc.get("provider") or "your supplier", info,
                        "before" if s.get("auto_sched_mode") == "before" else "cheapest") or rates_changed:
                    automation.publish(shared_state)
            except Exception:
                logger.warning("Automatic schedule cycle failed", exc_info=True)
            await asyncio.sleep(AUTO_TICK_S)
    # A Charge now button pressed in HA: an automation can't answer "are you
    # sure?", so it goes ahead over the daily limit, as auto plug-in does
    ha_link.on_charge_now = lambda hours: charge_now(hours, confirm=True)

    async def supplier_marks_loop() -> None:
        """Mark how much of each session was in the supplier's dispatch slots."""
        while True:
            try:
                sc = ha_link.smart_charging()
                if sc.get("found"):
                    cp.sessions.mark_supplier(sc.get("periods") or [], sc.get("provider"))
            except Exception:
                logger.debug("Could not mark sessions against the supplier's slots", exc_info=True)
            await asyncio.sleep(60)

    # --- Diagnostics › Debug -------------------------------------------------
    offline_hold = {"until": 0.0}  # a dropped connection waits until then to reconnect
    debug_tasks: set = set()

    async def debug_drop(seconds: float) -> dict:
        ws = cp._connection
        if ws is None:
            raise ValueError("Not connected to the supplier's server")
        offline_hold["until"] = loop.time() + seconds
        logger.info("Debug: dropping the connection to the OCPP server, reconnecting in %d s (asked on the web page)",
                    max(round(seconds), BACKOFF_STEPS[0]))
        task = asyncio.create_task(ws.close())
        debug_tasks.add(task)
        task.add_done_callback(debug_tasks.discard)
        return {"seconds": seconds}

    async def debug_restart() -> dict:
        token = os.environ.get("SUPERVISOR_TOKEN")
        if not token:
            raise ValueError("Can't reach the Supervisor: restart the add-on from Home Assistant")

        async def restart_soon() -> None:
            await asyncio.sleep(1)  # the page gets its answer first
            import aiohttp
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post("http://supervisor/addons/self/restart",
                                            headers={"Authorization": f"Bearer {token}"},
                                            timeout=aiohttp.ClientTimeout(total=60)) as resp:
                        if resp.status >= 400:
                            logger.warning("Debug: the Supervisor refused the restart (HTTP %s): %s",
                                           resp.status, (await resp.text())[:200])
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("Debug: couldn't ask the Supervisor to restart the add-on", exc_info=True)

        logger.info("Debug: restarting the add-on (asked on the web page)")
        task = asyncio.create_task(restart_soon())
        debug_tasks.add(task)
        task.add_done_callback(debug_tasks.discard)
        return {"restarting": True}

    async def supervisor_call(method: str, path: str, body: Optional[dict] = None):
        """A Supervisor API call (src/migrate.py): its reply's data; raises if it fails."""
        token = os.environ.get("SUPERVISOR_TOKEN")
        if not token:
            raise RuntimeError("the Supervisor can't be reached")
        import aiohttp
        async with aiohttp.ClientSession() as session:
            async with session.request(method.upper(), f"http://supervisor{path}", json=body,
                                       headers={"Authorization": f"Bearer {token}"},
                                       timeout=aiohttp.ClientTimeout(total=30)) as resp:
                reply = await resp.json(content_type=None) if resp.content_length != 0 else {}
                if resp.status >= 400 or (reply or {}).get("result") == "error":
                    raise RuntimeError((reply or {}).get("message") or f"HTTP {resp.status}")
                return (reply or {}).get("data") or {}

    mover = migrate.Mover(data_dir, health.version, supervisor_call, debug_restart, suppliers.OVERRIDE_PATH,
                          in_session=lambda: shared_state.transaction_id is not None)

    def on_settings_change(changes: dict) -> None:
        if "log_level" in changes:
            app_settings.apply_log_level()
        if "continue_session" in changes:
            cp.resume_on_restart = changes["continue_session"]
            logger.info("Continue the session after a restart: %s", "on" if changes["continue_session"] else "off")

    app_settings.on_change = on_settings_change
    debug = DebugTools(send=cp.debug_send, drop=debug_drop, restart=debug_restart, config=config,
                       log_buffer=log_buffer, sessions=cp.sessions, messages=cp.message_log,
                       settings=app_settings)

    async def schedule_notify_loop() -> None:
        """Settings › Notifications: tell Home Assistant's notify service when the
        schedule changes, whatever changed it (src/schedule_notify.py)."""
        watcher = schedule_notify.Watcher()
        while True:
            try:
                if not (ha_link.settings.get("notify_on") and ha_link.settings.get("notify_service")):
                    watcher.reset()  # (turned on again: compared from then)
                else:
                    now = automation._now()
                    current = schedule_notify.upcoming(automation.windows(), now)
                    why = schedule_notify.causes(automation.entries, automation.saving_sessions, automation.saving_block)
                    text = watcher.tick(now, time.monotonic(), current, why)
                    if text:
                        await ha_link.notify("Charging schedule changed", text)
            except Exception:
                logger.warning("Schedule notifications cycle failed", exc_info=True)
            await asyncio.sleep(15)

    charger_tasks = [
        asyncio.create_task(schedule_notify_loop()),
        asyncio.create_task(supplier_marks_loop()),
        asyncio.create_task(saving_sessions_loop()),
        asyncio.create_task(auto_schedule_loop()),
        asyncio.create_task(cp.meter_values_loop()),
        asyncio.create_task(cp.clock_aligned_loop()),
        asyncio.create_task(sample_loop(
            history, shared_state, refresh=cp.refresh_live_power,
            on_sample=lambda s: cp.sessions.sample(s["power_kw"]),
        )),
        asyncio.create_task(automation_loop(automation, shared_state, do_plug, do_unplug,
                                            scheduled=ha_link.scheduled,
                                            slot_now=ha_link.slot_now,
                                            set_ready_time=ready_time_guarded,
                                            supplier_plan=ha_link.supplier_plan)),
        asyncio.create_task(ha_link.run()),
        asyncio.create_task(cp.message_log.save_loop()),
    ]
    notified_server = False

    api_app = create_api_app(
        shared_state, do_plug, do_unplug, do_set_current, do_set_power,
        on_refresh=cp.refresh_live_power,
        on_set_soc=do_set_soc,
        gui=gui,
        automation=automation,
        ha_link=ha_link,
        on_set_ramp=cp.set_ramp,
        on_charge_now=charge_now,
        debug=debug,
        on_cancel_charge=cancel_charge_now,
        mover=mover,
        app_settings=app_settings,
        news=cp.news,
    )
    runner = web.AppRunner(api_app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", 8099)
    await site.start()
    logger.info("API server started on port 8099")

    attempt = 0
    try:
      while not stop_event.is_set():
        try:
            async with websockets.connect(
                auth.url(config),
                subprotocols=["ocpp1.6"],
                **_NO_USER_AGENT,
            ) as ws:
                attempt = 0
                auth.connected()
                logger.info("Connected to OCPP server")
                health.connected()
                cp.attach(ws)
                try:
                    await _run_connection(
                        cp, config, persistence, stop_task,
                    )
                finally:
                    cp.detach()
                if stop_event.is_set():
                    notified_server = True
                    break
                # Message loop returned cleanly — server closed with 1000 (OK).
                # Treat as a lost connection and reconnect.
                raise websockets.exceptions.ConnectionClosedOK(None, None)

        except (
            websockets.exceptions.ConnectionClosed,
            websockets.exceptions.WebSocketException,
            OSError,
        ) as e:
            shared_state.connected_to_server = False
            if refused_status(e):
                auth.refused()  # (the password your supplier set: back to yours next time)
            health.disconnected(str(e) or type(e).__name__)
            backoff = BACKOFF_STEPS[min(attempt, len(BACKOFF_STEPS) - 1)]
            backoff = max(backoff, round(offline_hold["until"] - loop.time()))  # Debug: drop for N s
            logger.warning(
                "Connection lost (%s), reconnecting in %ds...", str(e) or type(e).__name__, backoff,
            )
            attempt += 1
            # Sleep for the backoff, but wake immediately on shutdown
            try:
                await asyncio.wait_for(asyncio.shield(stop_task), backoff)
            except asyncio.TimeoutError:
                pass
        except _BootCancelled:
            break
        except SystemExit as e:
            # e.g. BootNotification rejected by the server
            logger.info("Shutting down: %s", e)
            break
      if stop_event.is_set() and not notified_server:
          # Stopped while offline: hold the StopTransaction for next time
          await _graceful_shutdown(cp)
    finally:
        stop_task.cancel()
        try:
            # Power / Energy / Current show as unavailable while stopped
            await asyncio.wait_for(ha_link.shutdown(), 5)
        except Exception:
            logger.debug("Couldn't mark the sensors unavailable", exc_info=True)
        await _cancel_all(charger_tasks)
        cp.message_log.save()  # keep the messages (Diagnostics › Debug) across the restart
        await runner.cleanup()
        logger.info("OCPP Charge Proxy stopped")


class _BootCancelled(Exception):
    """Shutdown requested while waiting for BootNotification."""


async def _run_connection(cp, config, persistence, stop_task) -> None:
    """Boot and run one websocket connection until it ends or shutdown."""
    # Start message loop first so incoming messages are handled
    start_task = asyncio.create_task(cp.start())

    # Use configured serial, or generate/load a persistent one
    serial = config.charger_serial or persistence.load_serial_number()

    boot_task = asyncio.create_task(cp.send_boot_notification(
        model=config.charger_model,
        vendor=config.charger_vendor,
        serial_number=serial,
        firmware_version=config.firmware_version,
    ))
    await asyncio.wait(
        {boot_task, stop_task}, return_when=asyncio.FIRST_COMPLETED,
    )
    if not boot_task.done():
        # Shutdown requested while still booting
        await _cancel_all([boot_task, start_task])
        raise _BootCancelled()
    try:
        interval = boot_task.result()
    except BaseException:
        await _cancel_all([start_task])
        raise

    tasks = [
        start_task,
        asyncio.create_task(cp.heartbeat_loop(interval)),
    ]

    # Wait until ANY task ends (normally the message loop when the
    # socket closes), then cancel the rest. asyncio.gather() does
    # not cancel siblings on failure, which left the old
    # connection's heartbeat/meter loops running forever after a
    # reconnect ("Heartbeat cycle failed ... ConnectionClosedOK").
    try:
        done, _ = await asyncio.wait(
            [*tasks, stop_task], return_when=asyncio.FIRST_COMPLETED,
        )
        if stop_task in done:
            # Message loop is still running here, so the server's
            # replies to our goodbye messages can be received.
            await _graceful_shutdown(cp)
    finally:
        await _cancel_all(tasks)

    if stop_task in done:
        return

    for t in done:
        exc = t.exception()
        if exc is not None:
            raise exc


def main():
    asyncio.run(run())


if __name__ == "__main__":
    main()
