# Golden packets from HeadsetControl

`golden_harness.cpp` runs every HeadsetControl driver against a recording HID interface
and prints, per capability call, the packets written and how canned replies were parsed.
`tests/test_drivers_golden.py` replays the same calls on the Python ports and requires
byte-identical output.

To regenerate (needs cmake, ninja, g++ and hidapi headers):

1. Clone https://github.com/Sapd/HeadsetControl into `hc/`.
2. In `lib/devices/hid_interface.hpp` declare `extern HIDInterface* g_golden_hid;`
   (inside the namespace) and make `HIDDevice::getHIDInterface()` return it when set.
3. Turn waits into no-ops (`sleep_ms`, `std::this_thread::sleep_for`) and shorten the
   Audeze `response_timeout_`: they only affect timing, never the bytes.
4. Build `headsetcontrol_lib`, compile the harness against it, and run it with a memory
   and file-size limit, writing to a disk-backed directory (not tmpfs):
   `prlimit --as=1500000000 --fsize=200000000 ./harness > golden.json`
5. `gzip -9 -c golden.json > tests/data/headsetcontrol_golden.json.gz`
   and `python3 tools/gen_driver_specs.py`.
