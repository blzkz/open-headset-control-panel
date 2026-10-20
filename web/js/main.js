// Open Headset Control Panel · Web: WebHID front-end for gaming headsets.
import { BatteryEstimator, Headset, HidppError, hoursLeft, hoursToFull } from "./device.js";
import { EqGraph } from "./eqgraph.js";
import { EqLayout, STANDARD_FREQS, fitGains } from "./eqlayout.js";
import { Feature, HidppTimeout, REPORT_LONG } from "./hidpp.js";
import { applyStatic, lang, tr } from "./i18n.js";
import { ACTIONS, DriverHeadset, VENDOR_NAMES, driverFor, isTimeout, webDrivers } from "./webhid.js";
import { REGISTRY } from "./drivers/index.js";

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => [...document.querySelectorAll(sel)];
const SWATCHES = ["#00b4f5", "#ffffff", "#ff2d55", "#ff6a00", "#ffd60a", "#32d74b", "#00e5c0", "#5e5ce6", "#bf5af2"];
const AUTO_OFF = [0, 5, 10, 15, 20, 30, 45, 60, 90, 120];
const STORE_KEY = "openheadset.web.v1";

let DATA = null; // models.json
let headset = null; // connected Headset
let estimator = null;
let batteryTimer = null;
let mixTimer = null;
let layout = new EqLayout(); // band layout of the connected headset's equalizer
let online = false;
let page = "eq";
let eqGraph = null;

// -- persistent settings (profiles) ---------------------------------------------------------
const defaultProfile = () => ({
  eqPreset: "flat",
  eqGains: [0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
  eqFreqs: [...STANDARD_FREQS],
  sidetone: 0,
  lighting: { mode: "static", color: "#00b4f5", brightness: 100 },
  controls: {},
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
  const p = store.profiles[store.active];
  p.controls ??= {};
  p.eqFreqs ??= [...STANDARD_FREQS];
  return p;
};

/** Express a profile's EQ in the connected headset's bands (presets are 10-band curves). */
function fitProfile(p) {
  const same = p.eqFreqs.length === layout.freqs.length && p.eqFreqs.every((f, i) => f === layout.freqs[i]);
  const gains = fitGains(p.eqGains, layout, same ? null : p.eqFreqs);
  if (!same || gains.some((g, i) => g !== p.eqGains[i])) {
    p.eqGains = gains;
    p.eqFreqs = [...layout.freqs];
    p.eqPreset = matchPreset(gains);
  }
}

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
  const same = (a) => {
    const f = fitGains(a, layout);
    return f.length === gains.length && f.every((v, i) => v === gains[i]);
  };
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
    small.textContent = m.verified ? `✓ ${tr("verified")}` : tr("experimental");
    small.classList.toggle("ok", m.verified);
    list.append(el);
  }
  const groups = {};
  for (const cls of REGISTRY) {
    const vendor = VENDOR_NAMES[cls.VENDOR] || cls.VENDOR.toString(16);
    (groups[vendor] ??= []).push(cls);
  }
  const brands = $("#brandList");
  brands.innerHTML = "";
  for (const vendor of Object.keys(groups).sort()) {
    const box = document.createElement("div");
    box.innerHTML = "<h4></h4><ul></ul>";
    box.querySelector("h4").textContent = vendor;
    for (const cls of groups[vendor].sort((a, b) => a.NAME.localeCompare(b.NAME))) {
      const li = document.createElement("li");
      li.textContent = cls.NAME;
      if (!cls.WEB) li.insertAdjacentHTML("beforeend", `<small>${tr("desktop app only")}</small>`);
      box.querySelector("ul").append(li);
    }
    brands.append(box);
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
  renderControls();
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
    b.onclick = () => setEq(fitGains(key.startsWith("user:") ? store.userPresets[key.slice(5)] : DATA.presets.find((x) => x.key === key).gains, layout), key);
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
  p.eqGains = fitGains(gains, layout);
  p.eqFreqs = [...layout.freqs];
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
  for (const [name, value] of Object.entries(p.controls)) {
    if (headset.controls?.includes(name) && !ACTIONS.has(name)) write(`ctl:${name}`, () => headset.setControl(name, value));
  }
}

// -- headset controls (HeadsetControl-port models) ---------------------------------------------------
const SWITCHES = [
  ["voice_prompts", "Voice prompts", "Spoken announcements and tones from the headset"],
  ["lights", "Lights", "LEDs on the headset"],
  ["volume_limiter", "Volume limiter", "Caps the maximum output level to protect your hearing"],
  ["rotate_to_mute", "Rotate to mute", "Mutes the microphone when the boom is raised"],
  ["bt_when_powered_on", "Bluetooth at power-on", "Turn Bluetooth on together with the headset"],
];

function setControl(name, value) {
  if (!ACTIONS.has(name)) {
    profile().controls[name] = value;
    save();
  }
  if (name === "eq_preset") profile().devicePreset = value;
  renderControls();
  if (headset?.controls?.includes(name)) write(`ctl:${name}`, () => headset.setControl(name, value));
}

function renderControls() {
  const h = headset;
  const c = new Set(h?.controls || []);
  const vals = profile().controls;
  $("#devPresetsCard").hidden = !h?.presets?.length;
  const box = $("#devPresets");
  if (h?.presets && box.dataset.model !== h.model.key) {
    box.dataset.model = h.model.key;
    box.innerHTML = "";
    h.presets.forEach((name, i) => {
      const b = document.createElement("button");
      const m = /^Preset (\d+)$/.exec(name);
      b.textContent = m ? tr("Preset {n}", { n: m[1] }) : tr(name);
      b.onclick = () => setControl("eq_preset", i);
      box.append(b);
    });
  }
  [...box.children].forEach((b, i) => b.classList.toggle("active", profile().devicePreset === i));
  $("#mixCard").hidden = !h?.caps.chatmix;
  $("#micVolumeRow").hidden = !c.has("mic_volume");
  $("#noiseRow").hidden = !c.has("noise_filter");
  $("#ledRow").hidden = !c.has("mic_mute_led");
  $("#micCard").hidden = !["mic_volume", "noise_filter", "mic_mute_led"].some((n) => c.has(n));
  const mv = $("#micVolume");
  if ("mic_volume" in vals && document.activeElement !== mv) {
    mv.value = Math.round((vals.mic_volume * 100) / 128);
    $("#micVolumeVal").textContent = `${mv.value}%`;
    setFill(mv);
  }
  if ("noise_filter" in vals) $("#noiseFilter").value = String(vals.noise_filter);
  if ("mic_mute_led" in vals) $("#muteLed").value = String(vals.mic_mute_led);
  if ("bt_call_volume" in vals) $("#btCall").value = String(vals.bt_call_volume);
  const sw = $("#switches");
  sw.innerHTML = "";
  for (const [name, title, desc] of SWITCHES) {
    if (!c.has(name)) continue;
    const row = document.createElement("div");
    row.className = "switch-row";
    const text = document.createElement("div");
    const label = document.createElement("div");
    const hint = document.createElement("div");
    label.textContent = tr(title);
    hint.className = "faint";
    hint.textContent = tr(desc);
    text.append(label, hint);
    const btn = document.createElement("button");
    btn.className = "switch";
    btn.setAttribute("role", "switch");
    btn.setAttribute("aria-label", tr(title));
    row.append(text, btn);
    btn.setAttribute("aria-checked", String(!!vals[name]));
    btn.onclick = () => setControl(name, !vals[name]);
    sw.append(row);
  }
  $("#btCallRow").hidden = !c.has("bt_call_volume");
  $("#soundRow").hidden = !c.has("notification_sound");
  $("#optsCard").hidden = ![...SWITCHES.map(([n]) => n), "bt_call_volume", "notification_sound"].some((n) => c.has(n));
  for (const id of ["#devPresetsCard", "#micCard", "#optsCard"]) $(id).toggleAttribute("disabled", !online);
}

function renderChatmix(c) {
  $("#mixKnob").style.left = `${(c.level / 128) * 100}%`;
  $("#mixGame").textContent = tr("Game {p}%", { p: c.game });
  $("#mixChat").textContent = tr("Chat {p}%", { p: c.chat });
}

async function pollChatmix() {
  clearTimeout(mixTimer);
  if (!headset?.caps.chatmix) return;
  if (online) {
    try {
      renderChatmix(await headset.chatmix());
    } catch (e) {
      handleError(e);
    }
  }
  mixTimer = setTimeout(pollChatmix, page === "headset" ? 1000 : 4000);
}

// -- device rendering ------------------------------------------------------------------------------
function renderDevice() {
  const h = headset;
  const m = h.model;
  $("#devName").textContent = m.name;
  $("#expChip").hidden = m.verified;
  $("#expChip").title = tr("This model has not been tested on real hardware yet.");
  const hasControls = !!(h.controls?.length || h.caps.chatmix || h.presets?.length);
  $('#rail button[data-page="light"]').hidden = !h.caps.lights;
  $('#rail button[data-page="headset"]').hidden = !hasControls;
  if ((page === "light" && !h.caps.lights) || (page === "headset" && !hasControls)) showPage("eq");
  $("#noEq").hidden = h.caps.equalizer;
  if (h.eq) {
    const l = h.layout || new EqLayout(h.eq.freqs, -h.eq.range, h.eq.range, 1);
    if (!l.equals(layout)) {
      layout = l;
      Object.values(store.profiles).forEach(fitProfile);
      save();
      renderPresets();
    }
    eqGraph.setBands(h.eq.freqs, h.eq.range, h.eq.step || 1);
    $("#eqSubtitle").textContent = `${tr("Onboard {n}-band equalizer", { n: h.eq.bands })} · ${tr("stored in the headset")}`;
  }
  eqGraph.setEnabled(h.caps.equalizer);
  eqGraph.setGains(profile().eqGains, false);
  moveIndicator(false);
  $("#noSidetone").hidden = h.caps.sidetone;
  $("#sidetone").disabled = !h.caps.sidetone;
  $("#powerCard").hidden = !h.caps.autoOff;
  $("#devImg").src = m.key === "pro-x-wireless" ? DATA.sideImage : m.image;
  $("#lightImg").src = m.image;
  const kv = $("#infoKv");
  kv.innerHTML = "";
  const pid = h.device.productId.toString(16).padStart(4, "0");
  const vid = h.device.vendorId.toString(16).padStart(4, "0");
  const hidpp = h.backend !== "headsetcontrol";
  for (const [k, v] of [
    [tr("Name"), h.identity.name],
    [tr("Model"), m.name],
    ...(hidpp ? [] : [[tr("Vendor"), m.vendor]]),
    ...h.identity.firmware.map((f) => [tr("Firmware"), f]),
    [tr("Protocol"), hidpp ? `HID++ ${h.identity.protocol}` : h.identity.protocol],
    [tr("USB ID"), `${vid}:${pid}`],
    ...(h.eq ? [[tr("Equalizer"), `${h.eq.bands} × ±${h.eq.range} dB`]] : []),
  ]) {
    kv.insertAdjacentHTML("beforeend", "<dt></dt><dd></dd>");
    kv.lastElementChild.previousElementSibling.textContent = k;
    kv.lastElementChild.textContent = v;
  }
  $("#featCard").hidden = !h.features.length;
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
  if (b && b.percent < 0) b = { ...b, unknown: true };
  for (const ic of icons) {
    ic.querySelector("i").style.width = b && !b.unknown ? `calc(${b.percent}% - 4px)` : "0";
    ic.classList.toggle("charging", !!b?.charging);
    ic.classList.toggle("low", !!b && !b.charging && b.percent <= 30 && b.percent > 15);
    ic.classList.toggle("crit", !!b && !b.charging && b.percent <= 15);
  }
  $("#battPct").textContent = b && !b.unknown ? `${b.percent}%` : "—";
  $("#battBig").textContent = b && !b.unknown ? `${b.percent}%` : "—";
  const estimates = b && !b.unknown && b.estimates !== false;
  let detail = "";
  if (b?.full) detail = tr("Fully charged");
  else if (b?.charging) {
    const h = estimates ? hoursToFull(b) : null;
    detail = tr("Charging") + (h ? ` · ${tr("≈ {h} h to full", { h: h.toFixed(1) })}` : "");
  } else if (estimates) detail = tr("≈ {h} h remaining", { h: hoursLeft(b).toFixed(0) });
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
      await headset.ping();
      await bringOnline();
      next = 10000;
    } catch {
      /* still off */
    }
  }
  if (headset) batteryTimer = setTimeout(pollBattery, next);
}

function handleError(e) {
  if (e instanceof HidppTimeout || e?.unreachable || isTimeout(e)) {
    setOnline(false, tr("Headset off"));
    return;
  }
  console.warn(e);
  toast(String(e.message || e), true);
}

// -- connection -------------------------------------------------------------------------------------
const filters = () => [
  ...DATA.pids.flatMap((productId) => [
    { vendorId: DATA.vendorId, productId, usagePage: 0xff43 },
    { vendorId: DATA.vendorId, productId, usagePage: 0xff00 },
    { vendorId: DATA.vendorId, productId },
  ]),
  ...webDrivers().flatMap((cls) => cls.PIDS.map((productId) => ({ vendorId: cls.VENDOR, productId }))),
];

const hasHidpp = (d) => d.collections.some((c) => c.outputReports?.some((r) => r.reportId === REPORT_LONG));
const modelFor = (d) => (d.vendorId === DATA.vendorId ? DATA.models.find((m) => m.pids.includes(d.productId)) : null);
const usable = (d) => (modelFor(d) && hasHidpp(d)) || (!modelFor(d) && driverFor(d));

/** Pick the headset among granted HID interfaces: native HID++ first, else a HeadsetControl port. */
async function attachFrom(devices) {
  const hid = devices.find((d) => modelFor(d) && hasHidpp(d));
  if (hid) return attach(hid);
  const first = devices.find((d) => !modelFor(d) && driverFor(d));
  if (!first) return;
  const group = devices.filter((d) => d.vendorId === first.vendorId && d.productId === first.productId);
  return attachDriver(group, driverFor(first));
}

async function connect() {
  try {
    toast(tr("Select your headset in the browser dialog"));
    const devices = await navigator.hid.requestDevice({ filters: filters() });
    $("#toast").hidden = true;
    await attachFrom(devices);
  } catch (e) {
    toast(`${tr("Could not open the headset")}: ${e.message || e}`, true);
  }
}

async function attach(dev) {
  if (headset) await detach();
  if (!dev.opened) await dev.open();
  headset = new Headset(dev, modelFor(dev));
  estimator = new BatteryEstimator(headset.model.curve, `openheadset.battery.${headset.model.key}`);
  headset.ping = () => headset.hidpp.ping();
  headset.hidpp.onEvent = (ev) => onEvent(ev);
  await start();
}

async function attachDriver(devices, cls) {
  if (headset) await detach();
  for (const d of devices) if (!d.opened) await d.open();
  headset = new DriverHeadset(devices, cls, DATA.images[cls.IMAGE] || DATA.images["headset-generic.png"]);
  estimator = null;
  await start();
}

async function start() {
  showApp(true);
  showPage(page);
  $("#devName").textContent = headset.model.name;
  renderControls();
  try {
    await bringOnline();
  } catch (e) {
    if (e instanceof HidppTimeout || e?.unreachable || isTimeout(e)) setOnline(false, tr("Turn on your headset"));
    else handleError(e);
  }
  pollBattery();
  pollChatmix();
}

async function bringOnline() {
  await headset.probe();
  setOnline(true, "");
  renderDevice();
  applyProfileToDevice();
  if (headset.caps.autoOff) {
    if (headset.getAutoOff) renderAutoOff(await headset.getAutoOff());
    else if (store.autoOff !== null) renderAutoOff(store.autoOff);
  }
  if (headset.backend === "headsetcontrol" && headset.caps.battery !== "none") {
    try {
      renderBattery(await headset.readBattery());
    } catch (e) {
      handleError(e);
    }
  }
  renderProfile();
}

async function onEvent(ev) {
  if (!headset?.hidpp) return;
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
  clearTimeout(mixTimer);
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
  $("#resetEq").onclick = () => setEq(fitGains(DATA.presets[0].gains, layout), DATA.presets[0].key);
  const mv = $("#micVolume");
  mv.oninput = () => {
    setFill(mv);
    $("#micVolumeVal").textContent = `${mv.value}%`;
  };
  mv.onchange = () => setControl("mic_volume", Math.round((+mv.value * 128) / 100));
  setFill(mv);
  $("#noiseFilter").onchange = (e) => setControl("noise_filter", +e.target.value);
  $("#muteLed").onchange = (e) => setControl("mic_mute_led", +e.target.value);
  $("#btCall").onchange = (e) => setControl("bt_call_volume", +e.target.value);
  $$("#soundRow button").forEach((b) => (b.onclick = () => setControl("notification_sound", +b.dataset.sound)));
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
  navigator.hid.addEventListener("disconnect", (e) => {
    if (headset?.devices?.includes(e.device)) detach();
  });
  navigator.hid.addEventListener("connect", async (e) => {
    if (!headset && usable(e.device)) attachFrom(await navigator.hid.getDevices()).catch(handleError);
  });
  // re-open a headset the user already granted on a previous visit
  const granted = (await navigator.hid.getDevices()).filter(usable);
  if (granted.length) attachFrom(granted).catch(handleError);
}

boot().catch((e) => {
  console.error(e);
  document.body.insertAdjacentHTML("beforeend", `<p class="banner" style="margin:20px">${e}</p>`);
});

export { lang };
