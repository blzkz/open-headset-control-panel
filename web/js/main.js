// OpenLogi HUB Web: WebHID front-end for Logitech HID++ 2.0 headsets.
import { BatteryEstimator, Headset, HidppError, hoursLeft, hoursToFull } from "./device.js";
import { EqGraph } from "./eqgraph.js";
import { Feature, HidppTimeout, REPORT_LONG } from "./hidpp.js";
import { applyStatic, lang, tr } from "./i18n.js";

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => [...document.querySelectorAll(sel)];
const SWATCHES = ["#00b4f5", "#ffffff", "#ff2d55", "#ff6a00", "#ffd60a", "#32d74b", "#00e5c0", "#5e5ce6", "#bf5af2"];
const AUTO_OFF = [0, 5, 10, 15, 20, 30, 45, 60, 90, 120];
const STORE_KEY = "openlogi.web.v1";

let DATA = null; // models.json
let headset = null; // connected Headset
let estimator = null;
let batteryTimer = null;
let online = false;
let page = "eq";
let eqGraph = null;

// -- persistent settings (profiles) ---------------------------------------------------------
const defaultProfile = () => ({
  eqPreset: "flat",
  eqGains: [0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
  sidetone: 0,
  lighting: { mode: "static", color: "#00b4f5", brightness: 100 },
});
const store = loadStore();

function loadStore() {
  try {
    const s = JSON.parse(localStorage.getItem(STORE_KEY) || "null");
    if (s && s.profiles && Object.keys(s.profiles).length) return s;
  } catch {
    /* ignore */
  }
  return { active: "Default", profiles: { Default: defaultProfile() }, userPresets: {}, autoOff: null };
}
const save = () => {
  try {
    localStorage.setItem(STORE_KEY, JSON.stringify(store));
  } catch {
    /* private mode */
  }
};
const profile = () => {
  if (!store.profiles[store.active]) store.active = Object.keys(store.profiles)[0];
  return store.profiles[store.active];
};

// -- latest-wins writer: slider drags never queue up stale HID++ writes ---------------------------
const writes = new Map();
function write(key, fn) {
  const slot = writes.get(key) || { busy: false, next: null };
  writes.set(key, slot);
  slot.next = fn;
  if (slot.busy) return;
  (async () => {
    slot.busy = true;
    while (slot.next) {
      const job = slot.next;
      slot.next = null;
      try {
        if (headset && online) await job();
      } catch (e) {
        handleError(e);
      }
    }
    slot.busy = false;
  })();
}

// -- UI helpers ------------------------------------------------------------------------------
let toastTimer = null;
function toast(msg, error = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.toggle("error", error);
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (t.hidden = true), 4500);
}

function setFill(range) {
  const pct = ((range.value - range.min) / (range.max - range.min)) * 100;
  range.style.setProperty("--fill", `${pct}%`);
}

function askText(title, label, initial = "") {
  const dlg = $("#dialog");
  $("#dlgTitle").textContent = title;
  $("#dlgLabel").textContent = label;
  const input = $("#dlgInput");
  input.value = initial;
  return new Promise((resolve) => {
    dlg.onclose = () => resolve(dlg.returnValue === "ok" ? input.value.trim() : null);
    dlg.returnValue = "";
    dlg.showModal();
    input.select();
  });
}

function presetLabel(key) {
  if (key.startsWith("user:")) return key.slice(5);
  const p = DATA.presets.find((x) => x.key === key);
  return p ? tr(p.name) : tr("Custom");
}

function matchPreset(gains) {
  const same = (a) => a.length === gains.length && a.every((v, i) => v === gains[i]);
  const builtin = DATA.presets.find((p) => same(p.gains));
  if (builtin) return builtin.key;
  const user = Object.entries(store.userPresets).find(([, g]) => same(g));
  return user ? `user:${user[0]}` : "custom";
}

// -- landing -----------------------------------------------------------------------------------
function renderLanding() {
  const list = $("#modelList");
  list.innerHTML = "";
  for (const m of DATA.models) {
    const el = document.createElement("div");
    el.className = "model";
    el.innerHTML = `<img alt="" loading="lazy"><span></span><small></small>`;
    el.querySelector("img").src = m.image;
    el.querySelector("span").textContent = m.name;
    const small = el.querySelector("small");
    small.textContent = m.verified ? "✓ verified" : tr("experimental");
    small.classList.toggle("ok", m.verified);
    list.append(el);
  }
  $("#heroImg").src = DATA.models[0].image;
  $("#version").textContent = `v${DATA.version}`;
  const hid = "hid" in navigator;
  $("#noHid").hidden = hid;
  $("#connectBig").disabled = !hid;
  $("#connectBtn").disabled = !hid;
  $("#linuxNote").hidden = !/Linux/.test(navigator.userAgent) || /Android/.test(navigator.userAgent);
}

function showApp(on) {
  $("#landing").hidden = on;
  $("#app").hidden = !on;
  $("#profileBtn").hidden = !on;
  $("#connectBtn").hidden = on;
  $("#disconnectBtn").hidden = !on;
  if (on) requestAnimationFrame(() => moveIndicator(false));
}

// -- navigation ----------------------------------------------------------------------------------
function moveIndicator(animate = true) {
  const btn = $(`#rail button[data-page="${page}"]`);
  const ind = $("#railIndicator");
  if (!btn || btn.hidden) return;
  ind.style.transition = animate ? "" : "none";
  ind.style.transform = `translateY(${btn.offsetTop}px)`;
  if (!animate) requestAnimationFrame(() => (ind.style.transition = ""));
}

function showPage(name) {
  page = name;
  $$("#rail button").forEach((b) => b.classList.toggle("active", b.dataset.page === name));
  $$(".page").forEach((p) => p.classList.toggle("active", p.dataset.page === name));
  const btn = $(`#rail button[data-page="${name}"]`);
  $("#sectionName").textContent = btn ? tr(btn.dataset.i18nTitle) : "";
  moveIndicator();
  if (name === "eq") requestAnimationFrame(() => eqGraph.draw());
}

// -- profiles popup ------------------------------------------------------------------------------
const ICONS = {
  user: '<svg viewBox="0 0 24 24"><circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/></svg>',
  check: '<svg class="check" viewBox="0 0 24 24"><path d="M20 6L9 17l-5-5"/></svg>',
  plus: '<svg viewBox="0 0 24 24"><path d="M12 5v14M5 12h14"/></svg>',
  copy: '<svg viewBox="0 0 24 24"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>',
  edit: '<svg viewBox="0 0 24 24"><path d="M12 20h9M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z"/></svg>',
  trash: '<svg viewBox="0 0 24 24"><path d="M3 6h18M8 6V4h8v2M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/></svg>',
};

function closePopup() {
  $("#popup").hidden = true;
  $("#profileBtn").classList.remove("open");
}

function openProfilePopup() {
  const pop = $("#popup");
  if (!pop.hidden) return closePopup();
  pop.innerHTML = `<div class="h3">${tr("Profiles")}</div>`;
  const add = (html, cls, onClick, disabled = false) => {
    const b = document.createElement("button");
    b.innerHTML = html;
    if (cls) b.className = cls;
    b.disabled = disabled;
    b.onclick = () => {
      closePopup();
      onClick();
    };
    pop.append(b);
  };
  const esc = (s) => s.replace(/[&<>"]/g, (c) => `&#${c.charCodeAt(0)};`);
  for (const name of Object.keys(store.profiles)) {
    const active = name === store.active;
    add(`${ICONS.user}<span>${esc(name)}</span>${active ? ICONS.check : ""}`, active ? "active" : "", () => selectProfile(name));
  }
  pop.append(document.createElement("hr"));
  add(`${ICONS.plus}<span>${tr("New profile…")}</span>`, "", () => newProfile(false));
  add(`${ICONS.copy}<span>${tr("Duplicate profile…")}</span>`, "", () => newProfile(true));
  add(`${ICONS.edit}<span>${tr("Rename profile…")}</span>`, "", renameProfile);
  add(`${ICONS.trash}<span>${tr("Delete profile")}</span>`, "danger", deleteProfile, Object.keys(store.profiles).length < 2);
  const r = $("#profileBtn").getBoundingClientRect();
  pop.style.left = `${Math.max(8, r.left + r.width / 2 - 140)}px`;
  pop.style.top = `${r.bottom + 6}px`;
  pop.hidden = false;
  $("#profileBtn").classList.add("open");
}

async function newProfile(copy) {
  const name = await askText(copy ? tr("Duplicate profile…") : tr("New profile…"), tr("Profile name"));
  if (!name || store.profiles[name]) return;
  store.profiles[name] = copy ? structuredClone(profile()) : defaultProfile();
  selectProfile(name);
}

async function renameProfile() {
  const old = store.active;
  const name = await askText(tr("Rename profile…"), tr("Profile name"), old);
  if (!name || name === old || store.profiles[name]) return;
  store.profiles[name] = store.profiles[old];
  delete store.profiles[old];
  store.active = name;
  save();
  renderProfile();
}

function deleteProfile() {
  if (Object.keys(store.profiles).length < 2) return;
  if (!confirm(tr("Delete profile “{name}”?", { name: store.active }))) return;
  delete store.profiles[store.active];
  selectProfile(Object.keys(store.profiles)[0]);
}

function selectProfile(name) {
  store.active = name;
  save();
  renderProfile();
  applyProfileToDevice();
}

// -- rendering of the profile state --------------------------------------------------------------
function renderProfile() {
  const p = profile();
  $("#profileName").textContent = store.active;
  renderPresets();
  eqGraph.setGains(p.eqGains);
  const st = $("#sidetone");
  st.value = p.sidetone;
  $("#sidetoneVal").textContent = `${p.sidetone}%`;
  setFill(st);
  renderLighting();
}

function renderPresets() {
  const p = profile();
  const list = $("#presetList");
  list.innerHTML = "";
  const button = (key, label) => {
    const b = document.createElement("button");
    b.className = "preset" + (p.eqPreset === key ? " active" : "");
    b.textContent = label;
    b.onclick = () => setEq(key.startsWith("user:") ? store.userPresets[key.slice(5)] : DATA.presets.find((x) => x.key === key).gains, key);
    list.append(b);
  };
  DATA.presets.forEach((pr) => button(pr.key, tr(pr.name)));
  const users = Object.keys(store.userPresets);
  if (users.length) {
    const h = document.createElement("div");
    h.className = "h3";
    h.textContent = tr("My presets");
    list.append(h);
    users.sort().forEach((n) => button(`user:${n}`, n));
  }
  if (p.eqPreset === "custom") {
    const b = document.createElement("button");
    b.className = "preset active";
    b.textContent = tr("Custom");
    b.disabled = true;
    list.prepend(b);
  }
  $("#presetName").textContent = presetLabel(p.eqPreset);
  $("#deletePreset").hidden = !p.eqPreset.startsWith("user:");
}

function renderLighting() {
  const l = profile().lighting;
  $$("#lightModes button").forEach((b) => b.classList.toggle("active", b.dataset.mode === l.mode));
  $$("#swatches .swatch").forEach((s) => s.classList.toggle("active", s.dataset.color === l.color.toLowerCase()));
  $("#customColor").value = l.color;
  const br = $("#brightness");
  br.value = l.brightness;
  br.disabled = l.mode === "off";
  $("#brightVal").textContent = `${l.brightness}%`;
  setFill(br);
  $("#lightStage").style.setProperty("--tint", l.mode === "off" ? "#444" : l.color);
}

// -- actions (UI → profile → headset) ------------------------------------------------------------
function setEq(gains, key = null) {
  const p = profile();
  p.eqGains = gains.map((g) => Math.round(g));
  p.eqPreset = key ?? matchPreset(p.eqGains);
  save();
  eqGraph.setGains(p.eqGains);
  renderPresets();
  if (headset?.caps.equalizer) write("eq", () => headset.setEq(p.eqGains));
}

function setSidetone(level) {
  profile().sidetone = level;
  save();
  if (headset?.caps.sidetone) write("sidetone", () => headset.setSidetone(level));
}

function hexToRgb(hex) {
  const n = parseInt(hex.replace("#", ""), 16);
  return [n >> 16, (n >> 8) & 255, n & 255];
}

function updateLighting(changes) {
  const l = profile().lighting;
  Object.assign(l, changes);
  save();
  renderLighting();
  if (headset?.caps.lights) write("lights", () => headset.setLights(l.mode, hexToRgb(l.color), l.brightness));
}

function applyProfileToDevice() {
  if (!headset || !online) return;
  const p = profile();
  if (headset.caps.sidetone) write("sidetone", () => headset.setSidetone(p.sidetone));
  if (headset.caps.equalizer) write("eq", () => headset.setEq(p.eqGains));
  if (headset.caps.lights) write("lights", () => headset.setLights(p.lighting.mode, hexToRgb(p.lighting.color), p.lighting.brightness));
  if (headset.caps.autoOff && store.autoOff !== null) write("autoOff", () => headset.setAutoOff(store.autoOff));
}

// -- device rendering ------------------------------------------------------------------------------
function renderDevice() {
  const h = headset;
  const m = h.model;
  $("#devName").textContent = m.name;
  $("#expChip").hidden = m.verified;
  $("#expChip").title = tr("This model has not been tested on real hardware yet.");
  $('#rail button[data-page="light"]').hidden = !h.caps.lights;
  if (page === "light" && !h.caps.lights) showPage("eq");
  $("#noEq").hidden = h.caps.equalizer;
  if (h.eq) eqGraph.setBands(h.eq.freqs, h.eq.range);
  eqGraph.setEnabled(h.caps.equalizer);
  eqGraph.setGains(profile().eqGains, false);
  $("#noSidetone").hidden = h.caps.sidetone;
  $("#sidetone").disabled = !h.caps.sidetone;
  $("#powerCard").hidden = !h.caps.autoOff;
  $("#devImg").src = m.key === "pro-x-wireless" ? DATA.sideImage : m.image;
  $("#lightImg").src = m.image;
  const kv = $("#infoKv");
  kv.innerHTML = "";
  const pid = h.device.productId.toString(16).padStart(4, "0");
  for (const [k, v] of [
    [tr("Name"), h.identity.name],
    [tr("Model"), m.name],
    ...h.identity.firmware.map((f) => [tr("Firmware"), f]),
    [tr("HID++ protocol"), h.identity.protocol],
    [tr("USB ID"), `046d:${pid}`],
    ...(h.eq ? [[tr("Equalizer"), `${h.eq.bands} × ±${h.eq.range} dB`]] : []),
  ]) {
    kv.insertAdjacentHTML("beforeend", "<dt></dt><dd></dd>");
    kv.lastElementChild.previousElementSibling.textContent = k;
    kv.lastElementChild.textContent = v;
  }
  const fk = $("#featKv");
  fk.innerHTML = "";
  for (const f of h.featureList()) {
    fk.insertAdjacentHTML("beforeend", "<dt></dt><dd></dd>");
    fk.lastElementChild.previousElementSibling.textContent = `[${f.index}] 0x${f.id.toString(16).padStart(4, "0").toUpperCase()}`;
    fk.lastElementChild.textContent = f.name;
  }
}

function setOnline(on, text = "") {
  online = on;
  $("#devStage").classList.toggle("off", !on);
  $("#lightStage").classList.toggle("off", !on);
  const st = $("#statusText");
  st.textContent = text;
  st.classList.toggle("warn", !on);
  if (!on) renderBattery(null);
}

function renderBattery(b) {
  const icons = [$("#battIcon"), $("#battIconBig")];
  for (const ic of icons) {
    ic.querySelector("i").style.width = b ? `calc(${b.percent}% - 4px)` : "0";
    ic.classList.toggle("charging", !!b?.charging);
    ic.classList.toggle("low", !!b && !b.charging && b.percent <= 30 && b.percent > 15);
    ic.classList.toggle("crit", !!b && !b.charging && b.percent <= 15);
  }
  $("#battPct").textContent = b ? `${b.percent}%` : "—";
  $("#battBig").textContent = b ? `${b.percent}%` : "—";
  let detail = "";
  if (b?.full) detail = tr("Fully charged");
  else if (b?.charging) {
    const h = hoursToFull(b);
    detail = tr("Charging") + (h ? ` · ${tr("≈ {h} h to full", { h: h.toFixed(1) })}` : "");
  } else if (b) detail = tr("≈ {h} h remaining", { h: hoursLeft(b).toFixed(0) });
  $("#battDetail").textContent = detail;
  if (online) $("#statusText").textContent = b?.charging ? tr("Charging") : "";
  const kv = $("#battKv");
  kv.innerHTML = b?.mv ? `<dt>${tr("Battery voltage")}</dt><dd>${b.mv} mV</dd>` : "";
}

// -- battery polling -------------------------------------------------------------------------------
async function pollBattery() {
  clearTimeout(batteryTimer);
  let next = 10000;
  if (headset && online) {
    try {
      const r = await headset.readBattery();
      if (r) {
        const b = r.percent !== undefined ? r : estimator.update(r.mv, r.charging, r.full);
        if (r.percent === undefined && estimator.transitionPending) next = 3000;
        renderBattery(b);
      }
    } catch (e) {
      if (e instanceof HidppError && e.code === 5) next = 3000; // power switching: retry soon
      else handleError(e);
    }
  } else if (headset) {
    next = 2500;
    try {
      await headset.hidpp.ping();
      await bringOnline();
      next = 10000;
    } catch {
      /* still off */
    }
  }
  if (headset) batteryTimer = setTimeout(pollBattery, next);
}

function handleError(e) {
  if (e instanceof HidppTimeout || e?.unreachable) {
    setOnline(false, tr("Headset off"));
    return;
  }
  console.warn(e);
  toast(String(e.message || e), true);
}

// -- connection -------------------------------------------------------------------------------------
const filters = () =>
  DATA.pids.flatMap((productId) => [
    { vendorId: DATA.vendorId, productId, usagePage: 0xff43 },
    { vendorId: DATA.vendorId, productId, usagePage: 0xff00 },
    { vendorId: DATA.vendorId, productId },
  ]);

const hasHidpp = (d) => d.collections.some((c) => c.outputReports?.some((r) => r.reportId === REPORT_LONG));
const modelFor = (d) => DATA.models.find((m) => m.pids.includes(d.productId));

async function connect() {
  try {
    toast(tr("Select your headset in the browser dialog"));
    const devices = await navigator.hid.requestDevice({ filters: filters() });
    $("#toast").hidden = true;
    const dev = devices.find((d) => modelFor(d) && hasHidpp(d));
    if (dev) await attach(dev);
  } catch (e) {
    toast(`${tr("Could not open the headset")}: ${e.message || e}`, true);
  }
}

async function attach(dev) {
  if (headset) await detach();
  if (!dev.opened) await dev.open();
  headset = new Headset(dev, modelFor(dev));
  estimator = new BatteryEstimator(headset.model.curve, `openlogi.battery.${headset.model.key}`);
  headset.hidpp.onEvent = (ev) => onEvent(ev);
  showApp(true);
  showPage(page);
  $("#devName").textContent = headset.model.name;
  try {
    await bringOnline();
  } catch (e) {
    if (e instanceof HidppTimeout || e?.unreachable) setOnline(false, tr("Turn on your headset"));
    else handleError(e);
  }
  pollBattery();
}

async function bringOnline() {
  await headset.probe();
  setOnline(true, "");
  renderDevice();
  applyProfileToDevice();
  if (headset.caps.autoOff) {
    const v = await headset.getAutoOff();
    renderAutoOff(v);
  }
  renderProfile();
}

async function onEvent(ev) {
  if (!headset) return;
  if (!online) return pollBattery();
  try {
    if (ev.sw === 0 && ev.fn === 0 && ev.feature === (await headset.hidpp.featureIndex(Feature.ADC))) {
      const mv = (ev.payload[0] << 8) | ev.payload[1];
      if (mv) renderBattery(estimator.update(mv, !!(ev.payload[2] & 2), !!(ev.payload[2] & 4)));
    }
  } catch {
    /* not an ADC device */
  }
}

async function detach() {
  clearTimeout(batteryTimer);
  const h = headset;
  headset = null;
  online = false;
  if (h) await h.close();
  showApp(false);
}

function renderAutoOff(v) {
  const sel = $("#autoOff");
  if (![...sel.options].some((o) => +o.value === v)) sel.add(new Option(tr("{n} min", { n: v }), v));
  sel.value = String(v);
}

// -- boot ----------------------------------------------------------------------------------------------
async function boot() {
  applyStatic();
  DATA = await (await fetch("data/models.json")).json();
  eqGraph = new EqGraph($("#eqCanvas"));
  eqGraph.addEventListener("input", (e) => setEq(e.detail));
  eqGraph.addEventListener("change", (e) => setEq(e.detail));
  renderLanding();

  // static controls
  const sw = $("#swatches");
  for (const c of SWATCHES) {
    const b = document.createElement("button");
    b.className = "swatch";
    b.dataset.color = c;
    b.title = c.toUpperCase();
    b.innerHTML = `<i style="background:${c}"></i>`;
    b.onclick = () => updateLighting({ color: c });
    sw.append(b);
  }
  const custom = document.createElement("input");
  custom.type = "color";
  custom.id = "customColor";
  custom.oninput = () => updateLighting({ color: custom.value });
  sw.append(custom);
  $$("#lightModes button").forEach((b) => (b.onclick = () => updateLighting({ mode: b.dataset.mode })));
  const br = $("#brightness");
  br.oninput = () => {
    setFill(br);
    $("#brightVal").textContent = `${br.value}%`;
  };
  br.onchange = () => updateLighting({ brightness: +br.value });
  const st = $("#sidetone");
  st.oninput = () => {
    setFill(st);
    $("#sidetoneVal").textContent = `${st.value}%`;
    setSidetone(+st.value);
  };
  const ao = $("#autoOff");
  for (const v of AUTO_OFF) ao.add(new Option(v ? tr("{n} min", { n: v }) : tr("Never"), v));
  ao.onchange = () => {
    store.autoOff = +ao.value;
    save();
    write("autoOff", () => headset.setAutoOff(store.autoOff));
  };
  $("#resetEq").onclick = () => setEq(DATA.presets[0].gains, DATA.presets[0].key);
  $("#savePreset").onclick = async () => {
    const name = await askText(tr("Save as preset…"), tr("Preset name"));
    if (!name) return;
    store.userPresets[name] = profile().eqGains.slice();
    profile().eqPreset = `user:${name}`;
    save();
    renderPresets();
  };
  $("#deletePreset").onclick = () => {
    const key = profile().eqPreset;
    delete store.userPresets[key.slice(5)];
    profile().eqPreset = matchPreset(profile().eqGains);
    save();
    renderPresets();
  };
  $$("#rail button").forEach((b) => (b.onclick = () => showPage(b.dataset.page)));
  $("#profileBtn").onclick = (e) => {
    e.stopPropagation();
    openProfilePopup();
  };
  document.addEventListener("click", (e) => {
    if (!$("#popup").contains(e.target)) closePopup();
  });
  document.addEventListener("keydown", (e) => e.key === "Escape" && closePopup());
  window.addEventListener("resize", () => moveIndicator(false));
  $("#connectBtn").onclick = connect;
  $("#connectBig").onclick = connect;
  $("#disconnectBtn").onclick = detach;
  renderProfile();

  if (!("hid" in navigator)) return;
  navigator.hid.addEventListener("disconnect", (e) => {
    if (headset && e.device === headset.device) {
      detach();
    }
  });
  navigator.hid.addEventListener("connect", (e) => {
    if (!headset && modelFor(e.device) && hasHidpp(e.device)) attach(e.device);
  });
  // re-open a headset the user already granted on a previous visit
  const granted = (await navigator.hid.getDevices()).find((d) => modelFor(d) && hasHidpp(d));
  if (granted) attach(granted).catch(handleError);
}

boot().catch((e) => {
  console.error(e);
  document.body.insertAdjacentHTML("beforeend", `<p class="banner" style="margin:20px">${e}</p>`);
});

export { lang };
