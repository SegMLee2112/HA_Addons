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
 * its attributes are each reading in kW (grid + importing, battery and car + charging)
 * and the kW along each line.
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
  .ico { fill: var(--primary-text-color); }
  .msg { color: var(--secondary-text-color); padding: 8px 0; }
  @media (prefers-reduced-motion: reduce) { animateMotion { display: none; } }
`;

// "M a C b c d" -> "M d C c b a"; "M a L b" -> "M b L a"
function reversePath(d) {
  const pts = d.match(/-?\d+(\.\d+)?,-?\d+(\.\d+)?/g);
  return d.includes('C') ? `M${pts[3]} C${pts[2]} ${pts[1]} ${pts[0]}` : `M${pts[1]} L${pts[0]}`;
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
    const f = st.attributes || {};
    const on = {}, dots = [];
    const car = f.car_kw || 0;
    for (const [key, kwv] of Object.entries(f.lines || {})) {
      const [line, dir, colour] = FLOWS[key] || [];
      if (!line) continue;
      on[line] = colour;
      dots.push([line, dir, colour, kwv]);
    }
    if (Math.abs(car) > 0.01) { on.car_home = '--f-car'; dots.push(['car_home', car < 0 ? 1 : -1, '--f-car', Math.abs(car)]); }
    // Only rebuild the lines and moving dots when the flows change, so the dots don't jump
    const sig = JSON.stringify(dots.map(([l, d, c, k]) => [l, d, c, Math.round(Math.log2(0.6 + k) * 4)]));
    let svg = '';
    for (const [name, d] of Object.entries(LINES)) {
      svg += `<path class="ln ${on[name] ? 'on' : ''}" d="${d}" style="stroke:var(${on[name] || LINE_COLOUR[name]})"/>`;
    }
    for (const [line, dir, colour, kwv] of dots) {
      const durS = Math.max(0.8, Math.min(4, 4 / (0.6 + kwv))).toFixed(2);
      const path = dir < 0 ? reversePath(LINES[line]) : LINES[line];
      for (const offset of [0, 0.5]) {
        svg += `<circle r="3.5" style="fill:var(${colour})"><animateMotion dur="${durS}s" repeatCount="indefinite" begin="-${(offset * durS).toFixed(2)}s" path="${path}"/></circle>`;
      }
    }
    // An optional reading above the icon, the flows below it
    const node = (key, colour, label, labelAbove, lines, top) => {
      const [x, y] = NODES[key], r = 38;
      let out = `<circle class="node" cx="${x}" cy="${y}" r="${r}" style="stroke:var(${colour})"/>`;
      out += `<text class="lbl" x="${x}" y="${labelAbove ? y - r - 8 : y + r + 17}">${label}</text>`;
      let iconTop = y - (lines.length > 1 ? 27 : 17);
      if (top) {
        const base = y - (lines.length > 1 ? 24 : 15);
        out += `<text x="${x}" y="${base}" class="big">${top}</text>`;
        iconTop = base + 3;
      }
      out += `<g transform="translate(${x - 10},${iconTop}) scale(0.84)"><path class="ico" d="${ICONS[key]}"/></g>`;
      lines.forEach(([text, c, big], n) => {
        out += `<text x="${x}" y="${iconTop + 33 + n * 13}" class="${big ? 'big' : ''}" ${c ? `style="fill:var(${c})"` : ''}>${text}</text>`;
      });
      return out;
    };
    const grid = f.grid_kw, batt = f.battery_kw;
    let nodes = node('solar', '--f-solar', 'Solar', true, [[kwShort(f.pv_kw), null, true]]);
    // ← sent to the grid, → taken from it
    nodes += node('grid', '--f-export', 'Grid', false, [
      [`← ${kwShort(grid != null && grid < 0 ? grid : 0)}`, '--f-export'], [`→ ${kwShort(grid != null && grid > 0 ? grid : 0)}`, '--f-import']]);
    nodes += node('home', '--f-home', 'Home', false, [[kwShort(f.house_kw), null, true]]);
    // ↓ into the battery, ↑ out of it
    nodes += node('battery', '--f-bout', 'Battery', false, [
      [`↓ ${kwShort(batt != null && batt > 0 ? batt : 0)}`, '--f-bin'], [`↑ ${kwShort(batt != null && batt < 0 ? batt : 0)}`, '--f-bout']],
      f.home_soc == null ? null : `${num(f.home_soc, 0)}%`);
    // The car sits above the house: ↓ discharging (down into the house), ↑ charging (up from it)
    nodes += node('car', '--f-car', 'Car', true, [[`${car < 0 ? '↓' : '↑'} ${kwShort(car)}`, '--f-car']],
      f.car_soc == null ? null : `${num(f.car_soc, 0)}%`);
    if (this._sig !== sig || !box.querySelector('.nodes')) {
      this._sig = sig;
      box.innerHTML = `<svg viewBox="0 -16 420 362" role="img" aria-label="Power flow between solar, the grid, the home battery, the house and the car">${svg}<g class="nodes">${nodes}</g></svg>`;
    } else {
      box.querySelector('.nodes').innerHTML = nodes;
    }
  }
}

if (!customElements.get('evdc-power-flow-card')) customElements.define('evdc-power-flow-card', EvdcPowerFlowCard);
window.customCards = window.customCards || [];
if (!window.customCards.some((c) => c.type === 'evdc-power-flow-card')) {
  window.customCards.push({type: 'evdc-power-flow-card', name: 'EVDC Power Flow',
    description: 'Solar, the grid, the home battery, the house and the car (from the SigEnergy EVDC SoC Range Manager add-on)',
    preview: true});
}
