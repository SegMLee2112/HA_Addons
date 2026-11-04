/*
 * EVDC Power Flow card: the SigEnergy EVDC SoC Range Manager add-on's power flow
 * diagram (solar, the grid, the home battery, the house and the car) for any dashboard.
 *
 *   type: custom:evdc-power-flow-card
 *   entity: sensor.evdc_soc_range_power_flow   # the default
 *   title: Power                                # optional
 *
 * The add-on copies this file into Home Assistant's www folder and adds it to the
 * dashboards' resources itself (src/card.py). It draws the add-on's power flow sensor:
 * its attributes are each reading in kW (grid + importing, battery and car + charging),
 * the kW along each line, and the devices off the house picked on the add-on's Settings tab.
 */
const ICONS = {
  solar: 'M4,2H20A2,2 0 0,1 22,4V14A2,2 0 0,1 20,16H15V20H18V22H13V16H11V22H6V20H9V16H4A2,2 0 0,1 2,14V4A2,2 0 0,1 4,2M4,4V8H11V4H4M4,14H11V10H4V14M20,14V10H13V14H20M20,4H13V8H20V4Z',
  grid: 'M8.28,5.45L6.5,4.55L7.76,2H16.23L17.5,4.55L15.72,5.44L15,4H9L8.28,5.45M18.62,8H14.09L13.3,5H10.7L9.91,8H5.38L4.1,10.55L5.89,11.44L6.62,10H17.38L18.1,11.45L19.89,10.56L18.62,8M17.77,22H15.7L15.46,21.1L12,15.9L8.53,21.1L8.3,22H6.23L9.12,11H11.19L10.83,12.35L12,14.1L13.16,12.35L12.81,11H14.88L17.77,22M11.4,15L10.5,13.65L9.32,18.13L11.4,15M14.68,18.12L13.5,13.64L12.6,15L14.68,18.12Z',
  home: 'M10,20V14H14V20H19V12H22L12,3L2,12H5V20H10Z',
  battery: 'M16,20H8V6H16M16.67,4H15V2H9V4H7.33A1.33,1.33 0 0,0 6,5.33V20.67C6,21.4 6.6,22 7.33,22H16.67A1.33,1.33 0 0,0 18,20.67V5.33C18,4.6 17.4,4 16.67,4Z',
  car: 'M5,11L6.5,6.5H17.5L19,11M17.5,16A1.5,1.5 0 0,1 16,14.5A1.5,1.5 0 0,1 17.5,13A1.5,1.5 0 0,1 19,14.5A1.5,1.5 0 0,1 17.5,16M6.5,16A1.5,1.5 0 0,1 5,14.5A1.5,1.5 0 0,1 6.5,13A1.5,1.5 0 0,1 8,14.5A1.5,1.5 0 0,1 6.5,16M18.92,6C18.72,5.42 18.16,5 17.5,5H6.5C5.84,5 5.28,5.42 5.08,6L3,12V20A1,1 0 0,0 4,21H5A1,1 0 0,0 6,20V19H18V20A1,1 0 0,0 19,21H20A1,1 0 0,0 21,20V12L18.92,6Z',
};
// Node centres in a 420 x 330 box, and the lines between them (drawn from the first named node)
const NODES = {solar: [210, 62], grid: [62, 172], home: [358, 172], battery: [210, 282], car: [358, 50]};
const LINES = {
  solar_home: 'M226,98 C226,160 262,166 320,166', solar_grid: 'M194,98 C194,160 158,166 100,166',
  solar_battery: 'M210,100 L210,244', grid_home: 'M100,172 L320,172',
  grid_battery: 'M100,178 C158,178 194,184 194,246', battery_home: 'M226,246 C226,184 262,178 320,178',
  car_home: 'M358,88 L358,134',
};
// Each flow: the line it travels, which way, and its colour
const FLOWS = {
  solar_house: ['solar_home', 1, '--f-solar'], solar_battery: ['solar_battery', 1, '--f-solar'], solar_grid: ['solar_grid', 1, '--f-solar'],
  grid_house: ['grid_home', 1, '--f-import'], grid_battery: ['grid_battery', 1, '--f-import'],
  battery_house: ['battery_home', 1, '--f-bout'], battery_grid: ['grid_battery', -1, '--f-export'],
  house_grid: ['grid_home', -1, '--f-export'], house_battery: ['battery_home', -1, '--f-bin'],
};
const LINE_COLOUR = {solar_home: '--f-solar', solar_grid: '--f-solar', solar_battery: '--f-solar', grid_home: '--f-import',
  grid_battery: '--f-export', battery_home: '--f-bout', car_home: '--f-car'};

const STYLE = `
  :host { --f-solar: #ff9800; --f-import: #488fc2; --f-export: #8353d1; --f-bin: #f06292; --f-bout: #4db6ac;
    --f-home: #5ec9b6; --f-car: var(--primary-color, #03a9f4); }
  ha-card { padding: 12px 16px 16px; }
  .title { font-size: 20px; font-weight: 400; padding: 4px 0 8px; color: var(--primary-text-color); }
  svg { display: block; width: 100%; height: auto; max-width: 460px; margin: 0 auto; }
  .ln { fill: none; stroke-width: 1.5; opacity: .22; }
  .ln.on { opacity: 1; }
  .node { fill: var(--card-background-color, var(--ha-card-background, #fff)); stroke-width: 3; }
  text { fill: var(--primary-text-color); font-size: 12px; text-anchor: middle; font-family: var(--paper-font-body1_-_font-family, inherit); }
  text.lbl { fill: var(--secondary-text-color); font-size: 12.5px; }
  text.big { font-size: 13px; font-weight: 500; }
  text.sm { fill: var(--secondary-text-color); font-size: 10.5px; }
  .ico { fill: var(--primary-text-color); }
  .msg { color: var(--secondary-text-color); padding: 8px 0; }
  @media (prefers-reduced-motion: reduce) { animateMotion { display: none; } }
`;

// "M a C b c d" -> "M d C c b a"; "M a L b" -> "M b L a"
function reversePath(d) {
  const pts = d.match(/-?\d+(\.\d+)?,-?\d+(\.\d+)?/g);
  return d.includes('C') ? `M${pts[3]} C${pts[2]} ${pts[1]} ${pts[0]}` : `M${pts[1]} L${pts[0]}`;
}
// ---- The power flow drawing: the same in the add-on's page and the dashboard card ----
// Off the house, in a column to its right: the car on the DC charger first, then your devices
// (a car charger, a heat pump...)
const DEV_ICONS = {
  car: 'M5,11L6.5,6.5H17.5L19,11M17.5,16A1.5,1.5 0 0,1 16,14.5A1.5,1.5 0 0,1 17.5,13A1.5,1.5 0 0,1 19,14.5A1.5,1.5 0 0,1 17.5,16M6.5,16A1.5,1.5 0 0,1 5,14.5A1.5,1.5 0 0,1 6.5,13A1.5,1.5 0 0,1 8,14.5A1.5,1.5 0 0,1 6.5,16M18.92,6C18.72,5.42 18.16,5 17.5,5H6.5C5.84,5 5.28,5.42 5.08,6L3,12V20A1,1 0 0,0 4,21H5A1,1 0 0,0 6,20V19H18V20A1,1 0 0,0 19,21H20A1,1 0 0,0 21,20V12L18.92,6Z',
  heatpump: 'M12,11A1,1 0 0,0 11,12A1,1 0 0,0 12,13A1,1 0 0,0 13,12A1,1 0 0,0 12,11M12.5,2C17,2 17.11,5.57 14.75,6.75C13.76,7.24 13.32,8.29 13.13,9.22C13.61,9.42 14.03,9.73 14.35,10.13C18.05,8.13 22.03,8.92 22.03,12.5C22.03,17 18.46,17.1 17.28,14.73C16.78,13.74 15.72,13.3 14.79,13.11C14.59,13.59 14.28,14 13.88,14.34C15.87,18.03 15.08,22 11.5,22C7,22 6.91,18.42 9.27,17.24C10.25,16.75 10.69,15.71 10.89,14.79C10.4,14.59 9.97,14.27 9.65,13.87C5.96,15.85 2,15.07 2,11.5C2,7 5.56,6.89 6.74,9.26C7.24,10.25 8.29,10.68 9.22,10.87C9.41,10.39 9.73,9.97 10.14,9.65C8.15,5.96 8.94,2 12.5,2Z',
  plug: 'M16,7V3H14V7H10V3H8V7H8C7,7 6,8 6,9V14.5L9.5,18V21H14.5V18L18,14.5V9C18,8 17,7 16,7Z',
  flash: 'M7,2V13H10V22L17,10H13L17,2H7Z',
  water: 'M12,20A6,6 0 0,1 6,14C6,10 12,3.25 12,3.25C12,3.25 18,10 18,14A6,6 0 0,1 12,20Z',
};
const DEV_COLOURS = ['#ec407a', '#ff7043', '#9ccc65', '#ab47bc', '#ffca28', '#78909c'];
const colourOf = (c) => c.startsWith('--') ? `var(${c})` : c;
// Everything to draw: {vb (the viewBox), lines (the lines and moving dots), nodes, sig (changes
// when the lines or the dots' speeds do, so the dots don't jump on every update)}
function flowParts(f) {
  const nodesAt = {...NODES}, lines = {...LINES}, lineColour = {...LINE_COLOUR}, flowsOf = {...FLOWS};
  delete lines.car_home; delete lineColour.car_home;  // the car joins the column
  const car = f.car_kw || 0;
  // (the car only while it's plugged in: car_plugged false hides it)
  const devs = [...(f.car_plugged === false ? [] : [{car: true, name: 'Car', icon: 'car', kw: Math.abs(car)}]),
    ...(f.devices || []).slice(0, 6)], n = devs.length;
  // The same size as the others, and the drawing as narrow as it can be (so it's as big as it
  // can be on a phone): the car above the house and one device below it fit the usual square.
  // With more, a column to the right of the house; more than 3 zigzag between two columns, so
  // no line passes behind another circle.
  const tight = n <= 2, zig = n > 3, r = 38;
  const gapY = n > 1 ? Math.min(zig ? 110 : 100, (zig ? 370 : 200) / (n - 1)) : 0;
  const above = {};
  devs.forEach((d, k) => {
    const key = 'dev' + k;
    if (tight) {
      nodesAt[key] = k ? [358, 282] : [358, 50];
      above[key] = !k;
      lines[`home_${key}`] = k ? 'M358,210 L358,244' : 'M358,134 L358,88';
    } else {
      const x = zig ? (k % 2 ? 610 : 500) : 500, y = Math.round(172 + (k - (n - 1) / 2) * gapY);
      nodesAt[key] = [x, y];
      // Out of the house, turning at x 430 (left of every device), then straight in: no line crosses a device
      lines[`home_${key}`] = `M396,172 C430,172 430,${y} ${x - r},${y}`;
    }
    lineColour[`home_${key}`] = d.car ? '--f-car' : DEV_COLOURS[d.n % DEV_COLOURS.length];
  });
  const on = {}, dots = [];
  for (const [key, kwv] of Object.entries(f.lines || {})) {
    const [line, dir, colour] = flowsOf[key] || [];
    if (!line) continue;
    on[line] = colour;
    dots.push([line, dir, colour, kwv]);
  }
  // Out of the house to each one; the car discharging comes back into it
  devs.forEach((d, k) => {
    if ((d.kw || 0) > 0.01) {
      on[`home_dev${k}`] = lineColour[`home_dev${k}`];
      dots.push([`home_dev${k}`, d.car && car < 0 ? -1 : 1, lineColour[`home_dev${k}`], d.kw]);
    }
  });
  const sig = JSON.stringify([n, dots.map(([l, d, c, k]) => [l, d, c, Math.round(Math.log2(0.6 + k) * 4)])]);
  let svg = '';
  for (const [name, d] of Object.entries(lines)) {
    svg += `<path class="ln ${on[name] ? 'on' : ''}" d="${d}" style="stroke:${colourOf(on[name] || lineColour[name])}"/>`;
  }
  for (const [line, dir, colour, kwv] of dots) {
    const durS = Math.max(0.8, Math.min(4, 4 / (0.6 + kwv))).toFixed(2);
    // Against the line's drawn direction: travel a reversed copy of it (works in every browser)
    const path = dir < 0 ? reversePath(lines[line]) : lines[line];
    for (const offset of [0, 0.5]) {
      svg += `<circle r="3.5" style="fill:${colourOf(colour)}"><animateMotion dur="${durS}s" repeatCount="indefinite" begin="-${(offset * durS).toFixed(2)}s" path="${path}"/></circle>`;
    }
  }
  // An optional reading above the icon, the flows below it
  const node = (key, colour, label, labelAbove, rows2, top) => {
    const [x, y] = nodesAt[key], rr = 38;
    let out = `<circle class="node" cx="${x}" cy="${y}" r="${rr}" style="stroke:var(${colour})"/>`;
    out += `<text class="lbl" x="${x}" y="${labelAbove ? y - rr - 8 : y + rr + 17}">${label}</text>`;
    let iconTop = y - (rows2.length > 1 ? 27 : 17);
    if (top) {
      const base = y - (rows2.length > 1 ? 24 : 15);
      out += `<text x="${x}" y="${base}" class="big">${top}</text>`;
      iconTop = base + 3;
    }
    out += `<g transform="translate(${x - 10},${iconTop}) scale(0.84)"><path class="ico" d="${ICONS[key]}"/></g>`;
    rows2.forEach(([text, c, cls], k) => {
      out += `<text x="${x}" y="${iconTop + 33 + k * 13}" class="${cls === true ? 'big' : cls || ''}" ${c ? `style="fill:var(${c})"` : ''}>${text}</text>`;
    });
    return out;
  };
  const grid = f.grid_kw, batt = f.battery_kw;
  let nodes = node('solar', '--f-solar', 'Solar', true, [[kwShort(f.pv_kw), null, true]]);
  // ← sent to the grid, → taken from it
  // (and its phase A voltage above the icon, when there's a reading)
  nodes += node('grid', '--f-export', 'Grid', false, [
    [`← ${kwShort(grid != null && grid < 0 ? grid : 0)}`, '--f-export'], [`→ ${kwShort(grid != null && grid > 0 ? grid : 0)}`, '--f-import']],
    f.grid_v == null ? null : `${num(f.grid_v, 0)} V`);
  // With devices off it, the house shows what's left, and the whole below
  const homeLabel = tight && n === 2 ? '' : 'Home';
  nodes += f.home_kw != null
    ? node('home', '--f-home', homeLabel, false, [[kwShort(f.home_kw), null, true], [`of ${kwShort(f.house_kw)}`, null, 'sm']])
    : node('home', '--f-home', homeLabel, false, [[kwShort(f.house_kw), null, true]]);
  // A device below the house: its name goes below to the left, clear of the line down
  if (!homeLabel) nodes += '<text class="lbl" x="326" y="226" style="text-anchor:end">Home</text>';
  // ↓ into the battery, ↑ out of it
  nodes += node('battery', '--f-bout', 'Battery', false, [
    [`↓ ${kwShort(batt != null && batt > 0 ? batt : 0)}`, '--f-bin'], [`↑ ${kwShort(batt != null && batt < 0 ? batt : 0)}`, '--f-bout']],
    f.home_soc == null ? null : `${num(f.home_soc, 0)}%`);
  // Each one like the others: its icon and power in a circle in its colour, its name below. The
  // car shows its SoC above the icon, and ← into the house while discharging, → while charging.
  devs.forEach((d, k) => {
    const [x, y] = nodesAt['dev' + k], c = colourOf(lineColour['home_dev' + k]);
    const max = zig ? 12 : 16, name = String(d.name || '').length > max ? String(d.name).slice(0, max - 1) + '…' : String(d.name || '');
    const esc2 = (s) => s.replace(/[&<>"]/g, (ch) => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'})[ch]);
    const soc = d.car && f.car_soc != null, iconTop = soc ? y - 12 : y - 17;
    nodes += `<circle class="node" cx="${x}" cy="${y}" r="${r}" style="stroke:${c}"/>`
      + (soc ? `<text x="${x}" y="${y - 15}" class="big">${num(f.car_soc, 0)}%</text>` : '')
      + `<g transform="translate(${x - 10},${iconTop}) scale(0.84)"><path d="${DEV_ICONS[d.icon] || DEV_ICONS.plug}" style="fill:${c}"/></g>`
      + `<text x="${x}" y="${iconTop + 33}" class="${d.car ? '' : 'big'}" ${d.car ? `style="fill:${c}"` : ''}>${d.car ? `${car < 0 ? '←' : '→'} ` : ''}${kwShort(d.kw)}</text>`
      + `<text class="lbl" x="${x}" y="${above['dev' + k] ? y - r - 8 : y + r + 17}">${esc2(name)}</text>`;
  });
  const vb = tight ? '0 -16 420 362' : zig ? '0 -50 680 470' : '0 -16 568 362';
  return {vb, lines: svg, nodes, sig};
}
const num = (v, d) => Number(v).toLocaleString(undefined, {minimumFractionDigits: d, maximumFractionDigits: d});
const kwShort = (v) => v == null ? '—' : Math.abs(v) < 1 ? `${Math.round(Math.abs(v) * 1000)} W` : `${num(Math.abs(v), 1)} kW`;

class EvdcPowerFlowCard extends HTMLElement {
  static getStubConfig() { return {entity: 'sensor.evdc_soc_range_power_flow'}; }
  // The card editor: pick the sensor, and an optional title
  static getConfigForm() {
    return {schema: [
      {name: 'entity', required: true, selector: {entity: {domain: 'sensor'}}},
      {name: 'title', selector: {text: {}}},
    ]};
  }

  setConfig(config) {
    this._config = {entity: 'sensor.evdc_soc_range_power_flow', ...(config || {})};
    if (!this.shadowRoot) this.attachShadow({mode: 'open'});
    this.shadowRoot.innerHTML = `<style>${STYLE}</style><ha-card>${this._config.title ? `<div class="title"></div>` : ''}<div class="box"></div></ha-card>`;
    if (this._config.title) this.shadowRoot.querySelector('.title').textContent = this._config.title;
    this._sig = null;
    if (this._hass) this._draw();
  }

  set hass(hass) {
    const st = hass.states[this._config && this._config.entity];
    const changed = !this._hass || !this._state || st !== this._state;
    this._hass = hass;
    this._state = st;
    if (changed) this._draw();
  }

  getCardSize() { return 5; }
  getGridOptions() { return {columns: 12, min_columns: 6, rows: 6, min_rows: 4}; }

  _draw() {
    const box = this.shadowRoot && this.shadowRoot.querySelector('.box');
    if (!box) return;
    const st = this._state;
    if (!st) {
      this._sig = null;
      box.innerHTML = `<div class="msg">${this._config.entity} isn't there: is the SigEnergy EVDC SoC Range Manager add-on running?</div>`;
      return;
    }
    if (st.state === 'unavailable') {
      this._sig = null;
      box.innerHTML = '<div class="msg">The SigEnergy EVDC SoC Range Manager add-on is stopped.</div>';
      return;
    }
    const {vb, lines: svg, nodes, sig} = flowParts(st.attributes || {});
    if (this._sig !== sig || !box.querySelector('.nodes')) {
      this._sig = sig;
      box.innerHTML = `<svg viewBox="${vb}" style="max-width:${Math.round(460 * Number(vb.split(' ')[2]) / 420)}px" role="img" aria-label="Power flow between solar, the grid, the home battery, the house and the car">${svg}<g class="nodes">${nodes}</g></svg>`;
    } else {
      box.querySelector('.nodes').innerHTML = nodes;
    }
  }
}

if (!customElements.get('evdc-power-flow-card')) customElements.define('evdc-power-flow-card', EvdcPowerFlowCard);
window.customCards = window.customCards || [];
if (!window.customCards.some((c) => c.type === 'evdc-power-flow-card')) {
  window.customCards.push({type: 'evdc-power-flow-card', name: 'EVDC Power Flow',
    description: 'Solar, the grid, the home battery, the house, the car and your devices off the house (from the SigEnergy EVDC SoC Range Manager add-on)',
    preview: true});
}
