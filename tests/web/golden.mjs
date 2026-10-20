// Byte-exact check of the web (JavaScript) driver ports against HeadsetControl.
// Twin of tests/test_drivers_golden.py: same golden file, same scripted replies, same rules.
//   node tests/web/golden.mjs [driver name filter]
import { readFileSync } from "node:fs";
import { gunzipSync } from "node:zlib";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

import { REGISTRY } from "../../web/js/drivers/index.js";
import { DriverError, setClock, setSleeper } from "../../web/js/drivers/base.js";

const HERE = dirname(fileURLToPath(import.meta.url));
const DATA = join(HERE, "..", "data");
const PATTERNS = ["zeros", "ramp", "ff", "echo"];
const MAX_OPS = 48;

function loadScripts() {
  const scripts = {};
  let rules = null;
  for (const raw of readFileSync(join(DATA, "golden_scripts.txt"), "utf8").split("\n")) {
    const line = raw.split("#", 1)[0].trim();
    if (!line) continue;
    if (line.startsWith("[")) {
      const close = line.indexOf("]");
      rules = [];
      (scripts[line.slice(1, close)] ??= []).push([line.slice(close + 1).trim(), rules]);
      continue;
    }
    const [head, rest = ""] = line.split("->");
    const [tail, mods = ""] = rest.split("|");
    const [op, ...matches] = head.trim().split(/\s+/);
    const match = matches.map((m) => m.split("=")).map(([i, v]) => [+i, parseInt(v, 16)]);
    const replies = tail.trim().split(/\s+/).filter(Boolean).map((t) => Buffer.from(t.replace(/_/g, ""), "hex"));
    const modifiers = mods.trim().split(/\s+/).filter(Boolean).map((m) => {
      const [kind, spec] = m.split("=");
      const [range, at] = spec.split("@");
      const [lo, hi] = range.split("-").map(Number);
      return [kind, lo, hi, at === undefined ? -1 : +at];
    });
    rules.push([op, match, replies, modifiers]);
  }
  return scripts;
}

class Recorder {
  constructor(pattern, rules = null) {
    Object.assign(this, { pattern, rules, queue: [], ops: [], dropped: 0, last: [], idleInputs: 0 });
  }

  _push(op) {
    if (this.ops.length < MAX_OPS) this.ops.push(op);
    else this.dropped++;
  }

  _rec(op, data) {
    this.idleInputs = 0;
    this._push({ op, data: Buffer.from(data).toString("hex") });
    this.last = Array.from(data);
    this._react(op, this.last);
  }

  _react(op, data) {
    if (!this.rules) return;
    for (const [rop, match, replies, mods] of this.rules) {
      if (rop === op && match.every(([i, v]) => i < data.length && data[i] === v)) {
        this.queue = replies.map((r) => Recorder._apply(Array.from(r), data, mods));
        return;
      }
    }
  }

  static _apply(out, written, mods) {
    for (const [kind, lo, hi, at] of mods) {
      if (kind === "copy") {
        for (let i = lo; i <= Math.min(hi, written.length - 1); i++) {
          while (out.length <= i) out.push(0);
          out[i] = written[i];
        }
      } else if (kind === "sum") {
        let total = 0;
        for (let i = lo; i <= hi; i++) total += out[i] ?? 0;
        while (out.length <= at) out.push(0);
        out[at] = total & 0xff;
      }
    }
    return out;
  }

  _pop(n) {
    const r = this.queue.shift();
    const out = new Array(n).fill(0);
    for (let i = 0; i < Math.min(n, r.length); i++) out[i] = r[i];
    return out;
  }

  _fill(n) {
    return Array.from({ length: n }, (_, i) =>
      this.pattern === "ramp" ? i & 0xff : this.pattern === "ff" ? 0xff
        : this.pattern === "echo" ? (i < this.last.length ? this.last[i] : (0x40 + i) & 0xff) : 0);
  }

  async write(data, size = null) {
    this._rec("write", size === null ? Array.from(data) : padTo(data, size));
  }

  async sendFeature(data, size = null) {
    this._rec("feature_set", size === null ? Array.from(data) : padTo(data, size));
  }

  async read(length, timeoutMs) {
    this._push({ op: "read", len: length, timeout: timeoutMs });
    if (timeoutMs >= 0 && timeoutMs <= 10) throw new DriverError("timeout", "empty");
    if (this.rules) {
      if (!this.queue.length) throw new DriverError("timeout", "no scripted reply");
      return this._pop(length);
    }
    return this._fill(length);
  }

  async getFeature(reportId, length) {
    this._push({ op: "feature_get", len: length, id: reportId });
    if (this.rules && this.queue.length) return this._pop(length);
    const buf = this._fill(length);
    if (length) buf[0] = reportId;
    return buf;
  }

  async getInput(reportId, length) {
    if (this.rules && this.queue.length) {
      this._push({ op: "input_get", len: length, id: reportId });
      return this._pop(length);
    }
    if (++this.idleInputs > 3) return [];
    this._push({ op: "input_get", len: length, id: reportId });
    const buf = this._fill(length);
    if (length) buf[0] = reportId;
    return buf;
  }
}

const padTo = (data, size) => Array.from({ length: size }, (_, i) => (i < data.length ? data[i] & 0xff : 0));

async function call(drv, h, name, arg) {
  switch (name) {
    case "battery": {
      const b = await drv.battery(h);
      return { level: b.level, status: b.status, mv: b.millivolts ?? -1 };
    }
    case "sidetone":
    case "get_sidetone": {
      const s = name === "sidetone" ? await drv.setSidetone(h, arg) : await drv.getSidetone(h);
      return { current: s.current, device: s.deviceLevel };
    }
    case "lights":
      return { enabled: !!(await drv.setLights(h, !!arg)) };
    case "inactive_time":
      return { minutes: await drv.setInactiveTime(h, arg) };
    case "chatmix": {
      const c = await drv.chatmix(h);
      return { level: c.level, game: c.game, chat: c.chat };
    }
  }
  const simple = {
    notification_sound: () => drv.notificationSound(h, arg),
    voice_prompts: () => drv.setVoicePrompts(h, !!arg),
    rotate_to_mute: () => drv.setRotateToMute(h, !!arg),
    eq_preset: () => drv.setEqPreset(h, arg),
    equalizer: () => drv.setEqualizer(h, arg),
    parametric_eq: () => drv.setParametricEq(h, arg.map(([frequency, gain, q, type]) => ({ frequency, gain, q, type }))),
    mic_led: () => drv.setMicMuteLed(h, arg),
    mic_volume: () => drv.setMicVolume(h, arg),
    volume_limiter: () => drv.setVolumeLimiter(h, !!arg),
    bt_when_powered_on: () => drv.setBtWhenPoweredOn(h, !!arg),
    bt_call_volume: () => drv.setBtCallVolume(h, arg),
    noise_filter: () => drv.setNoiseFilter(h, arg),
  };
  await simple[name]();
  return {};
}

// Python json.dumps(sort_keys=False) and ours differ in key order: compare canonically.
const canon = (v) => JSON.stringify(v, (_k, x) => (x && typeof x === "object" && !Array.isArray(x)
  ? Object.fromEntries(Object.entries(x).sort(([a], [b]) => (a < b ? -1 : 1))) : x));
const round4 = (v) => Math.round(v * 1e4) / 1e4;

function checkStatic(cls, dev, fail) {
  const same = (what, a, b) => canon(a) !== canon(b) && fail(`${what}: ${canon(a)} != ${canon(b)}`);
  same("vendor", cls.VENDOR, dev.vendor);
  same("pids", cls.PIDS, dev.pids);
  same("caps", cls.CAPS, Object.fromEntries(dev.caps.map((c) => [c.cap, [c.usagepage, c.usageid, c.interface]])));
  if (cls.EQ_FROM_DEVICE) same("eq (from device)", cls.EQ, null);
  else if (dev.eq) {
    same("eq", [cls.EQ.bands, cls.EQ.baseline, cls.EQ.min, cls.EQ.max], [dev.eq.bands, dev.eq.baseline, dev.eq.min, dev.eq.max]);
    if (Math.abs(cls.EQ.step - dev.eq.step) > 1e-5) fail(`eq step ${cls.EQ.step} != ${dev.eq.step}`);
  }
  same("preset count", cls.PRESET_COUNT, new Set(dev.calls.filter((c) => c.call === "eq_preset").map((c) => c.arg)).size);
  same("presets", cls.EQ_PRESETS.map(([n, v]) => [n, v.map(round4)]), dev.presets.map((p) => [p.name, p.values.map(round4)]));
}

async function main() {
  const filter = process.argv[2] ?? "";
  const golden = JSON.parse(gunzipSync(readFileSync(join(DATA, "headsetcontrol_golden.json.gz"))).toString());
  const scripts = loadScripts();
  setSleeper(async () => {});
  let tick = 0;
  setClock(() => tick++ / 1000);
  const byName = new Map(REGISTRY.map((c) => [c.NAME, c]));
  let failures = 0;
  let checked = 0;
  const missing = golden.map((d) => d.name).filter((n) => n !== "HeadsetControl Test device" && !byName.has(n));
  for (const dev of golden) {
    const cls = byName.get(dev.name);
    if (!cls || !dev.name.includes(filter)) continue;
    const errors = [];
    const fail = (msg) => errors.push(msg);
    checkStatic(cls, dev, fail);
    const drv = new cls();
    const groups = new Map();
    for (const c of dev.calls) {
      const key = JSON.stringify([c.call, c.arg]);
      if (!groups.has(key)) groups.set(key, {});
      groups.get(key)[c.pattern] = c;
    }
    const patterns = [...PATTERNS.map((p) => [p, null]), ...(scripts[dev.name] ?? []).map(([v, r]) => [`script:${v}`, r])];
    for (const [key, expected] of groups) {
      const [name, arg] = JSON.parse(key);
      for (const [pattern, rules] of patterns) {
        const rec = new Recorder(pattern, rules);
        let result;
        try {
          result = await call(drv, rec, name, arg);
        } catch (e) {
          if (!(e instanceof DriverError)) {
            fail(`${name}(${JSON.stringify(arg)}) [${pattern}] crashed: ${e.stack}`);
            continue;
          }
          result = { error: true };
        }
        const want = expected[pattern];
        if (!want) continue;
        checked++;
        const label = `${name}(${JSON.stringify(arg).slice(0, 60)}) [${pattern}]`;
        const strip = (ops) => ops.map(({ timeout: _t, ...rest }) => rest);
        if (cls.TIMING_EXACT) {
          if (canon(rec.ops) !== canon(want.ops)) fail(`${label} ops\n   got  ${canon(rec.ops).slice(0, 900)}\n   want ${canon(want.ops).slice(0, 900)}`);
          else if (rec.dropped !== want.dropped) fail(`${label} dropped ${rec.dropped} != ${want.dropped}`);
        } else {
          if (canon(strip(rec.ops)) !== canon(strip(want.ops))) fail(`${label} ops differ`);
          if ((rec.dropped > 0) !== (want.dropped > 0)) fail(`${label} dropped`);
        }
        if ("error" in want.result) {
          if (!("error" in result)) fail(`${label} expected an error, got ${canon(result)}`);
        } else if (canon(result) !== canon(want.result)) fail(`${label} result ${canon(result)} != ${canon(want.result)}`);
      }
    }
    if (errors.length) {
      failures++;
      console.log(`FAIL ${dev.name} (${errors.length})\n  ${errors.slice(0, 3).join("\n  ")}`);
    }
  }
  console.log(`${checked} calls checked, ${byName.size} drivers, ${failures} failing` +
    (missing.length ? `; not ported: ${missing.join(", ")}` : ""));
  process.exit(failures || (missing.length && !filter) ? 1 : 0);
}

main();
