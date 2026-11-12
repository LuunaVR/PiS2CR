"""Latency report from a btmon -T log: BLE cadence, HID cadence, controller->Switch processing, injection->air, air time."""
import bisect
import re
import statistics as st
import sys
from datetime import datetime

HEADER = re.compile(r"^(?:\S+: )?[<>@] .*?(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d+)$")
BLE_HANDLE = "0x002e"
SWITCH_B = 0x04  # B in the first button byte of a Switch 0x30 report
PRO2_B = 0x01  # B in the first button byte of a Pro Controller 2 report


def stamp(text):
    return datetime.strptime(text, "%Y-%m-%d %H:%M:%S.%f").timestamp()


def parse(path):
    """Return HID reports sent to the Switch, BLE reports from the controller, and send-complete events."""
    hid, ble, done = [], [], []
    kind = when = None
    data = bytearray()
    handle11 = False

    def flush():
        if kind == "tx" and data[:2] == b"\xa1\x30":
            hid.append((when, data[4]))
        elif kind == "rx" and data:
            ble.append((when, data[2]))
        elif kind == "done" and handle11:
            done.append(when)

    for line in open(path, errors="replace"):
        line = line.rstrip("\n")
        m = HEADER.match(line)
        if m:
            flush()
            data = bytearray()
            handle11 = False
            when = stamp(m.group(1))
            kind = ("tx" if "ACL Data TX" in line else "rx" if "ACL Data RX" in line
                    else "done" if "Number of Completed" in line else None)
            continue
        if kind == "done" and line.strip() == "Handle: 11":
            handle11 = True
        elif kind == "tx" and line.startswith("        ") and not line.lstrip().startswith("Channel"):
            data += bytes(int(t, 16) for t in line[8:56].split() if len(t) == 2 and all(c in "0123456789abcdef" for c in t))
        elif kind == "rx" and f"Handle: {BLE_HANDLE}" in line:
            data = bytearray(b"\x00\x00\x00")
        elif kind == "rx" and line.strip().startswith("Data[112]: "):
            data = bytearray(bytes.fromhex(line.strip().split(": ", 1)[1]))
    flush()
    return hid, ble, done


def stats(name, values_ms):
    if not values_ms:
        print(f"{name}: no samples")
        return
    values_ms = sorted(values_ms)
    p95 = values_ms[min(len(values_ms) - 1, len(values_ms) * 95 // 100)]
    print(f"{name}: n={len(values_ms)} min {values_ms[0]:.1f} median {st.median(values_ms):.1f} "
          f"mean {st.mean(values_ms):.1f} p95 {p95:.1f} max {values_ms[-1]:.1f} ms")


def gaps(times):
    return [(b - a) * 1000 for a, b in zip(times, times[1:])]


def first_after(times, flags, start, wanted):
    """Time of the first entry at or after start whose flag equals wanted."""
    i = bisect.bisect_left(times, start)
    while i < len(times):
        if flags[i] == wanted:
            return times[i]
        i += 1
    return None


def main():
    hid, ble, done = parse(sys.argv[1])
    hid_t = [t for t, _ in hid]
    hid_b = [bool(b & SWITCH_B) for _, b in hid]
    print(f"HID reports to Switch: {len(hid)}  BLE reports from controller: {len(ble)}")
    stats("HID report gap (Pi -> Switch cadence)", gaps(hid_t))
    stats("BLE report gap (controller -> Pi cadence)", gaps([t for t, _ in ble]))

    air = []
    for t in hid_t:
        j = bisect.bisect_left(done, t)
        if j < len(done) and done[j] - t < 0.05:
            air.append((done[j] - t) * 1000)
    stats("Pi radio: HCI send -> send complete", air)

    ble_t = [t for t, _ in ble]
    ble_b = [bool(b & PRO2_B) for _, b in ble]
    for label, edge in (("press", True), ("release", False)):
        lat = []
        for i in range(1, len(ble_b)):
            if ble_b[i] == edge and ble_b[i - 1] != edge:
                sent = first_after(hid_t, hid_b, ble_t[i], edge)
                if sent is not None and sent - ble_t[i] < 0.2:
                    lat.append((sent - ble_t[i]) * 1000)
        stats(f"Real B {label}: BLE report at Pi -> HID report sent to Switch", lat)

    if len(sys.argv) > 2:
        events = [line.split() for line in open(sys.argv[2])]
        for label, edge in (("press", True), ("release", False)):
            lat = []
            for name, t in events:
                if (name == "down") == edge:
                    sent = first_after(hid_t, hid_b, float(t), edge)
                    if sent is not None and sent - float(t) < 0.2:
                        lat.append((sent - float(t)) * 1000)
            stats(f"Injected B {label}: dashboard request -> HID report sent to Switch", lat)


main()
