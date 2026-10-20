// Golden-packet harness: runs every HeadsetControl driver against a recording HID
// interface and dumps what it writes (and how it parses canned replies) as JSON.
// The OpenLogi Python ports must reproduce these bytes exactly.
#include "device_registry.hpp"
#include "devices/hid_device.hpp"
#include "devices/hid_interface.hpp"

#include <cstdio>
#include <sstream>
#include <string>
#include <vector>

using namespace headsetcontrol;

namespace headsetcontrol { HIDInterface* g_golden_hid = nullptr; }

static std::string hex(std::span<const uint8_t> d)
{
    static const char* H = "0123456789abcdef";
    std::string s;
    for (auto b : d) {
        s += H[b >> 4];
        s += H[b & 15];
    }
    return s;
}

static std::string jstr(std::string_view v)
{
    std::string o = "\"";
    for (char c : v) {
        if (c == '"' || c == '\\') o += '\\';
        if (static_cast<unsigned char>(c) < 0x20) continue;
        o += c;
    }
    return o + "\"";
}

static constexpr size_t MAX_OPS = 48;  // drivers that poll in loops would otherwise log millions

struct Recorder : HIDInterface {
    std::vector<std::string> ops;
    size_t dropped = 0;
    void push(std::string op)
    {
        if (ops.size() < MAX_OPS) ops.push_back(std::move(op));
        else dropped++;
    }
    std::string pattern = "zeros";
    std::vector<uint8_t> last;
    int idle_inputs = 0;  // input-report polls since the last write

    void fill(std::span<uint8_t> out)
    {
        for (size_t i = 0; i < out.size(); i++) {
            uint8_t v = 0;
            if (pattern == "ramp") v = static_cast<uint8_t>(i);
            else if (pattern == "ff") v = 0xff;
            else if (pattern == "echo") v = i < last.size() ? last[i] : static_cast<uint8_t>(0x40 + i);
            out[i] = v;
        }
    }
    void rec(const char* op, std::span<const uint8_t> d)
    {
        idle_inputs = 0;
        push(std::string("{\"op\":\"") + op + "\",\"data\":\"" + hex(d) + "\"}");
        last.assign(d.begin(), d.end());
    }
    auto write(hid_device*, std::span<const uint8_t> d) -> Result<void> override
    {
        rec("write", d);
        return {};
    }
    auto write(hid_device*, std::span<const uint8_t> d, size_t size) -> Result<void> override
    {
        std::vector<uint8_t> p(size, 0);
        std::copy_n(d.begin(), std::min(d.size(), size), p.begin());
        rec("write", p);
        return {};
    }
    auto readTimeout(hid_device*, std::span<uint8_t> d, int ms) -> Result<size_t> override
    {
        push("{\"op\":\"read\",\"len\":" + std::to_string(d.size()) + ",\"timeout\":" + std::to_string(ms) + "}");
        // a short "flush" read on a real device returns a timeout once the queue is empty
        if (ms >= 0 && ms <= 10) return DeviceError::timeout("empty");
        fill(d);
        return d.size();
    }
    auto sendFeatureReport(hid_device*, std::span<const uint8_t> d) -> Result<void> override
    {
        rec("feature_set", d);
        return {};
    }
    auto sendFeatureReport(hid_device*, std::span<const uint8_t> d, size_t size) -> Result<void> override
    {
        std::vector<uint8_t> p(size, 0);
        std::copy_n(d.begin(), std::min(d.size(), size), p.begin());
        rec("feature_set", p);
        return {};
    }
    auto getFeatureReport(hid_device*, std::span<uint8_t> d) -> Result<size_t> override
    {
        push("{\"op\":\"feature_get\",\"len\":" + std::to_string(d.size()) + ",\"id\":" + std::to_string(d.empty() ? 0 : d[0]) + "}");
        uint8_t id = d.empty() ? 0 : d[0];
        fill(d);
        if (!d.empty()) d[0] = id;
        return d.size();
    }
    auto getInputReport(hid_device*, std::span<uint8_t> d) -> Result<size_t> override
    {
        // a polled dongle returns empty reports until the headset answers
        if (++idle_inputs > 3) return size_t(0);
        push("{\"op\":\"input_get\",\"len\":" + std::to_string(d.size()) + ",\"id\":" + std::to_string(d.empty() ? 0 : d[0]) + "}");
        uint8_t id = d.empty() ? 0 : d[0];
        fill(d);
        if (!d.empty()) d[0] = id;
        return d.size();
    }
};

static Recorder R;

template <typename T, typename F>
static std::string result(const Result<T>& r, F&& fmt)
{
    if (!r) return "{\"error\":" + jstr(r.error().message) + "}";
    return fmt(r.value());
}

static std::string run(const std::string& name, const std::string& arg, const std::string& res)
{
    std::ostringstream o;
    o << "{\"call\":" << jstr(name) << ",\"arg\":" << arg << ",\"pattern\":" << jstr(R.pattern) << ",\"ops\":[";
    for (size_t i = 0; i < R.ops.size(); i++) o << (i ? "," : "") << R.ops[i];
    o << "],\"dropped\":" << R.dropped << ",\"result\":" << res << "}";
    return o.str();
}

static const char* PATTERNS[] = { "zeros", "ramp", "ff", "echo" };

int main()
{
    g_golden_hid = &R;
    auto& reg = DeviceRegistry::instance();
    reg.initialize();
    std::printf("[\n");
    bool firstDev = true;
    for (const auto& up : reg.getAllDevices()) {
        HIDDevice* d = up.get();
        std::vector<std::string> calls;
        int caps = d->getCapabilities();
        auto has = [&](capabilities c) { return (caps & (1 << c)) != 0; };
        auto each = [&](const std::string& name, const std::string& arg, auto&& fn) {
            std::string prev_ops, prev_res;
            for (auto p : PATTERNS) {
                R.ops.clear();
                R.dropped = 0;
                R.last.clear();
                R.idle_inputs = 0;
                R.pattern = p;
                std::string res = fn();
                std::string ops_key;
                for (auto& op : R.ops) ops_key += op;
                if (ops_key == prev_ops && res == prev_res) continue;  // same as previous pattern
                prev_ops = ops_key;
                prev_res = res;
                calls.push_back(run(name, arg, res));
            }
        };
        auto fsd = [](const SidetoneResult& v) { return "{\"current\":" + std::to_string(v.current_level) + ",\"device\":" + std::to_string(v.device_level) + "}"; };

        if (has(CAP_BATTERY_STATUS))
            each("battery", "null", [&] { return result(d->getBattery(nullptr), [](const BatteryResult& b) {
                return "{\"level\":" + std::to_string(b.level_percent) + ",\"status\":" + std::to_string(int(b.status)) + ",\"mv\":" + std::to_string(b.voltage_mv.value_or(-1)) + "}"; }); });
        if (has(CAP_SIDETONE))
            for (int lv : { 0, 1, 64, 127, 128 })
                each("sidetone", std::to_string(lv), [&] { return result(d->setSidetone(nullptr, uint8_t(lv)), fsd); });
        if (has(CAP_SIDETONE_STATUS))
            each("get_sidetone", "null", [&] { return result(d->getSidetone(nullptr), fsd); });
        if (has(CAP_NOTIFICATION_SOUND))
            for (int s : { 0, 1 })
                each("notification_sound", std::to_string(s), [&] { return result(d->notificationSound(nullptr, uint8_t(s)), [](auto&) { return std::string("{}"); }); });
        if (has(CAP_LIGHTS))
            for (int on : { 0, 1 })
                each("lights", std::to_string(on), [&] { return result(d->setLights(nullptr, on), [](const LightsResult& l) { return "{\"enabled\":" + std::string(l.enabled ? "true" : "false") + "}"; }); });
        if (has(CAP_INACTIVE_TIME))
            for (int m : { 0, 1, 5, 10, 30, 45, 90 })
                each("inactive_time", std::to_string(m), [&] { return result(d->setInactiveTime(nullptr, uint8_t(m)), [](const InactiveTimeResult& r) { return "{\"minutes\":" + std::to_string(r.minutes) + "}"; }); });
        if (has(CAP_CHATMIX_STATUS))
            each("chatmix", "null", [&] { return result(d->getChatmix(nullptr), [](const ChatmixResult& c) {
                return "{\"level\":" + std::to_string(c.level) + ",\"game\":" + std::to_string(c.game_volume_percent) + ",\"chat\":" + std::to_string(c.chat_volume_percent) + "}"; }); });
        if (has(CAP_VOICE_PROMPTS))
            for (int on : { 0, 1 })
                each("voice_prompts", std::to_string(on), [&] { return result(d->setVoicePrompts(nullptr, on), [](auto&) { return std::string("{}"); }); });
        if (has(CAP_ROTATE_TO_MUTE))
            for (int on : { 0, 1 })
                each("rotate_to_mute", std::to_string(on), [&] { return result(d->setRotateToMute(nullptr, on), [](auto&) { return std::string("{}"); }); });
        if (has(CAP_EQUALIZER_PRESET))
            for (int p = 0; p < d->getEqualizerPresetsCount(); p++)
                each("eq_preset", std::to_string(p), [&] { return result(d->setEqualizerPreset(nullptr, uint8_t(p)), [](auto&) { return std::string("{}"); }); });
        std::string eqinfo = "null";
        if (auto info = d->getEqualizerInfo()) {
            eqinfo = "{\"bands\":" + std::to_string(info->bands_count) + ",\"baseline\":" + std::to_string(info->bands_baseline) + ",\"step\":" + std::to_string(info->bands_step) + ",\"min\":" + std::to_string(info->bands_min) + ",\"max\":" + std::to_string(info->bands_max) + "}";
            if (has(CAP_EQUALIZER)) {
                std::vector<std::vector<float>> sets;
                std::vector<float> a, b, c;
                for (int i = 0; i < info->bands_count; i++) {
                    a.push_back(float(info->bands_baseline));
                    b.push_back(i % 2 ? float(info->bands_max) : float(info->bands_min));
                    c.push_back(float(info->bands_baseline) + info->bands_step * float((i % 5) - 2));
                }
                for (auto& s : { a, b, c }) {
                    std::string arg = "[";
                    for (size_t i = 0; i < s.size(); i++) arg += (i ? "," : "") + std::to_string(s[i]);
                    arg += "]";
                    each("equalizer", arg, [&] { return result(d->setEqualizer(nullptr, EqualizerSettings(s)), [](auto&) { return std::string("{}"); }); });
                }
            }
        }
        std::string peqinfo = "null";
        if (auto pi = d->getParametricEqualizerInfo()) {
            peqinfo = "{\"bands\":" + std::to_string(pi->bands_count) + ",\"gain_base\":" + std::to_string(pi->gain_base) + ",\"gain_step\":" + std::to_string(pi->gain_step) + ",\"gain_min\":" + std::to_string(pi->gain_min) + ",\"gain_max\":" + std::to_string(pi->gain_max) + ",\"q_min\":" + std::to_string(pi->q_factor_min) + ",\"q_max\":" + std::to_string(pi->q_factor_max) + ",\"freq_min\":" + std::to_string(pi->freq_min) + ",\"freq_max\":" + std::to_string(pi->freq_max) + ",\"filter_types\":" + std::to_string(pi->filter_types) + "}";
            if (has(CAP_PARAMETRIC_EQUALIZER)) {
                std::vector<ParametricEqualizerBand> bands;
                for (int i = 0; i < pi->bands_count; i++) {
                    ParametricEqualizerBand bd;
                    bd.frequency = float(pi->freq_min) * float(i + 1) * 3.0f;
                    if (bd.frequency > pi->freq_max) bd.frequency = float(pi->freq_max);
                    bd.gain = i % 2 ? pi->gain_max : pi->gain_min + pi->gain_step;
                    bd.q_factor = pi->q_factor_min + (pi->q_factor_max - pi->q_factor_min) / 4.0f;
                    bd.type = EqualizerFilterType::Peaking;
                    bands.push_back(bd);
                }
                std::string arg = "[";
                for (size_t i = 0; i < bands.size(); i++)
                    arg += std::string(i ? "," : "") + "[" + std::to_string(bands[i].frequency) + "," + std::to_string(bands[i].gain) + "," + std::to_string(bands[i].q_factor) + ",2]";
                arg += "]";
                each("parametric_eq", arg, [&] { return result(d->setParametricEqualizer(nullptr, ParametricEqualizerSettings(bands)), [](auto&) { return std::string("{}"); }); });
            }
        }
        if (has(CAP_MICROPHONE_MUTE_LED_BRIGHTNESS))
            for (int v : { 0, 1, 2, 3 })
                each("mic_led", std::to_string(v), [&] { return result(d->setMicMuteLedBrightness(nullptr, uint8_t(v)), [](auto&) { return std::string("{}"); }); });
        if (has(CAP_MICROPHONE_VOLUME))
            for (int v : { 0, 1, 64, 128 })
                each("mic_volume", std::to_string(v), [&] { return result(d->setMicVolume(nullptr, uint8_t(v)), [](auto&) { return std::string("{}"); }); });
        if (has(CAP_VOLUME_LIMITER))
            for (int on : { 0, 1 })
                each("volume_limiter", std::to_string(on), [&] { return result(d->setVolumeLimiter(nullptr, on), [](auto&) { return std::string("{}"); }); });
        if (has(CAP_BT_WHEN_POWERED_ON))
            for (int on : { 0, 1 })
                each("bt_when_powered_on", std::to_string(on), [&] { return result(d->setBluetoothWhenPoweredOn(nullptr, on), [](auto&) { return std::string("{}"); }); });
        if (has(CAP_BT_CALL_VOLUME))
            for (int v : { 0, 1, 2 })
                each("bt_call_volume", std::to_string(v), [&] { return result(d->setBluetoothCallVolume(nullptr, uint8_t(v)), [](auto&) { return std::string("{}"); }); });
        if (has(CAP_NOISE_FILTER))
            for (int v : { 0, 1, 2, 3 })
                each("noise_filter", std::to_string(v), [&] { return result(d->setNoiseFilter(nullptr, uint8_t(v)), [](auto&) { return std::string("{}"); }); });

        std::ostringstream o;
        o << (firstDev ? "" : ",\n") << "{\"name\":" << jstr(d->getDeviceName()) << ",\"vendor\":" << d->getVendorId() << ",\"pids\":[";
        auto pids = d->getProductIds();
        for (size_t i = 0; i < pids.size(); i++) o << (i ? "," : "") << pids[i];
        o << "],\"caps\":[";
        bool f = true;
        for (int c = 0; c < NUM_CAPABILITIES; c++)
            if (has(capabilities(c))) {
                auto det = d->getCapabilityDetail(capabilities(c));
                o << (f ? "" : ",") << "{\"cap\":" << jstr(capability_to_enum_string(capabilities(c))) << ",\"usagepage\":" << det.usagepage << ",\"usageid\":" << det.usageid << ",\"interface\":" << det.interface_id << "}";
                f = false;
            }
        o << "],\"eq\":" << eqinfo << ",\"peq\":" << peqinfo << ",\"presets\":[";
        if (auto pr = d->getEqualizerPresets()) {
            for (size_t i = 0; i < pr->presets.size(); i++) {
                o << (i ? "," : "") << "{\"name\":" << jstr(pr->presets[i].name) << ",\"values\":[";
                for (size_t j = 0; j < pr->presets[i].values.size(); j++) o << (j ? "," : "") << pr->presets[i].values[j];
                o << "]}";
            }
        }
        o << "],\"calls\":[";
        for (size_t i = 0; i < calls.size(); i++) o << (i ? ",\n" : "\n") << calls[i];
        o << "]}";
        std::printf("%s", o.str().c_str());
        std::fflush(stdout);
        firstDev = false;
    }
    std::printf("\n]\n");
}
