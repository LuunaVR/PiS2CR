"""Subscribe to the common 0x05 input report and print its decoded motion block."""
import socket
import struct
import sys
import time

import probe_pro2 as probe
import stream_pro2 as pro2

INPUT_05_UUID = "ab7de9be-89fe-49ad-828f-118f09df7fd2"


def read_characteristics(sock, handler):
    start = 1
    while start <= 0xFFFF:
        sock.send(struct.pack("<BHH", probe.ATT_READ_BY_TYPE_REQ, start, 0xFFFF) + probe.UUID_CHARACTERISTIC)
        rsp = pro2.wait_for(sock, lambda p: p if p[0] != pro2.ATT_NOTIFY else None, 3)
        if rsp[0] != probe.ATT_READ_BY_TYPE_RSP:
            return
        size = rsp[1]
        last = start
        for off in range(2, len(rsp), size):
            last = handler(rsp[off:off + size])
        start = last + 1


def find_value_handle(sock, wanted):
    found = []

    def characteristic(entry):
        handle, props, value_handle = struct.unpack("<HBH", entry[:5])
        uuid = probe.fmt_uuid(entry[5:])
        print(f"  char value {value_handle:#06x} props {props:#04x} uuid {uuid}")
        if uuid == wanted:
            found.append(value_handle)
        return handle

    read_characteristics(sock, characteristic)
    return found[0] if found else None


def main():
    addr = sys.argv[1] if len(sys.argv) > 1 else sys.exit("usage: imu_probe.py CONTROLLER_MAC")
    seconds = float(sys.argv[2]) if len(sys.argv) > 2 else 4
    report_format = int(sys.argv[3], 0) if len(sys.argv) > 3 else pro2.REPORT_FORMAT
    sock = pro2.open_controller(addr, 300, report_format)
    find_value_handle(sock, INPUT_05_UUID)
    for value_handle in (0x000A, 0x000E, 0x0026):
        pro2.subscribe(sock, value_handle + 1)
    end = time.monotonic() + seconds
    counts, samples = {}, {}
    while time.monotonic() < end:
        try:
            pkt = sock.recv(512)
        except socket.timeout:
            continue
        if pkt[0] != pro2.ATT_NOTIFY:
            continue
        handle = struct.unpack("<H", pkt[1:3])[0]
        counts[handle] = counts.get(handle, 0) + 1
        samples.setdefault(handle, pkt[3:])
    for handle, count in sorted(counts.items()):
        report = samples[handle]
        print(f"handle {handle:#06x}: {count} reports, len={len(report)}")
        print(f"  {report.hex()}")
    sock.close()


if __name__ == "__main__":
    main()
