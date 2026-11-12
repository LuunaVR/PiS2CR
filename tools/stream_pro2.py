"""Run the Pro Controller 2 init handshake, then print decoded input reports."""
import socket
import struct
import sys
import time

from probe_pro2 import connect

ATT_WRITE_REQ = 0x12
ATT_WRITE_RSP = 0x13
ATT_WRITE_CMD = 0x52
ATT_NOTIFY = 0x1B
ATT_MTU_REQ = 0x02
ATT_MTU_RSP = 0x03
ATT_READ_REQ = 0x0A
ATT_READ_RSP = 0x0B

# Handles seen in the probe_pro2 GATT listing of this controller.
H_INPUT_CCCD = 0x002F
H_STD_CCCD = 0x000B
H_CMD_WRITE = 0x0014
H_CMD_RESP = 0x001A
H_CMD_RESP_CCCD = 0x001B
H_INPUT = 0x002E

# Feature mask for command 0x0C and report format for command 0x03/0x0A, as sent by SDL3's USB driver
# (SDL_hidapi_switch2.c): 0x27 = buttons, sticks, IMU, rumble.
FEATURES = 0x27
REPORT_FORMAT = 0x09

# 24-bit mask from report bytes 2-4; GL/GR order confirmed by the user on hardware.
BUTTONS = {
    "B": 0x000001, "A": 0x000002, "Y": 0x000004, "X": 0x000008,
    "R": 0x000010, "ZR": 0x000020, "PLUS": 0x000040, "RS": 0x000080,
    "DOWN": 0x000100, "RIGHT": 0x000200, "LEFT": 0x000400, "UP": 0x000800,
    "L": 0x001000, "ZL": 0x002000, "MINUS": 0x004000, "LS": 0x008000,
    "HOME": 0x010000, "CAPTURE": 0x020000, "GR": 0x040000, "GL": 0x080000,
    "C": 0x100000,
}


def wait_for(sock, wanted, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            pkt = sock.recv(512)
        except socket.timeout:
            continue
        found = wanted(pkt)
        if found is not None:
            return found
    raise TimeoutError("no matching ATT packet")


def subscribe(sock, cccd):
    sock.send(struct.pack("<BHH", ATT_WRITE_REQ, cccd, 0x0001))
    wait_for(sock, lambda p: True if p[0] == ATT_WRITE_RSP else None, 2)


def command(sock, cmd, sub, data=b""):
    frame = bytes([cmd, 0x91, 0x01, sub, 0x00, len(data), 0x00, 0x00]) + data
    sock.send(struct.pack("<BH", ATT_WRITE_CMD, H_CMD_WRITE) + frame)

    def is_reply(pkt):
        if pkt[0] == ATT_NOTIFY and struct.unpack("<H", pkt[1:3])[0] == H_CMD_RESP:
            return pkt[3:]
        return None

    reply = wait_for(sock, is_reply, 2)
    if len(reply) < 8 or reply[0] != cmd or reply[1] != 0x01:
        raise RuntimeError(f"unexpected reply {reply.hex()}")
    return reply[8:]


def stick(raw3):
    value = int.from_bytes(raw3, "little")
    return value & 0xFFF, value >> 12


def read_flash(sock, address, length):
    request = bytes([length, 0x7E, 0, 0]) + address.to_bytes(4, "little")
    return command(sock, 0x02, 0x04, request)[8:8 + length]


def open_controller(addr, connect_timeout, report_format=REPORT_FORMAT):
    """Connect, run the init handshake and return a socket that streams input reports."""
    sock = connect(addr, connect_timeout)
    sock.settimeout(1)
    # Without a larger MTU the controller sends no notifications.
    sock.send(struct.pack("<BH", ATT_MTU_REQ, 517))
    wait_for(sock, lambda p: True if p[0] == ATT_MTU_RSP else None, 2)
    subscribe(sock, H_CMD_RESP_CCCD)
    command(sock, 0x09, 0x07, bytes([0x01, 0, 0, 0]))
    for sub in (0x02, 0x04):
        command(sock, 0x0C, sub, bytes([FEATURES, 0, 0, 0]))
    command(sock, 0x03, 0x0A, bytes([report_format, 0, 0, 0]))
    subscribe(sock, H_STD_CCCD)
    subscribe(sock, H_INPUT_CCCD)
    return sock


def main():
    addr = sys.argv[1] if len(sys.argv) > 1 else sys.exit("usage: stream_pro2.py CONTROLLER_MAC")
    connect_timeout = float(sys.argv[2]) if len(sys.argv) > 2 else 60
    listen_seconds = float(sys.argv[3]) if len(sys.argv) > 3 else 20
    capture_path = sys.argv[4] if len(sys.argv) > 4 else "/tmp/pro2_capture.txt"
    sock = open_controller(addr, connect_timeout)
    print(f"connected to {addr}")
    start = time.monotonic()
    last_buttons = None
    last_print = 0.0
    count = 0
    per_handle = {}
    with open(capture_path, "w") as capture:
        while time.monotonic() - start < listen_seconds:
            try:
                pkt = sock.recv(512)
            except socket.timeout:
                continue
            if pkt[0] != ATT_NOTIFY:
                continue
            handle = struct.unpack("<H", pkt[1:3])[0]
            now = time.monotonic() - start
            capture.write(f"{now:.3f} {handle:#06x} {pkt[3:].hex()}\n")
            per_handle[handle] = per_handle.get(handle, 0) + 1
            if handle != H_INPUT:
                continue
            report = pkt[3:]
            count += 1
            buttons = int.from_bytes(report[2:5], "little")
            if buttons != last_buttons or now - last_print > 1:
                names = [name for name, mask in BUTTONS.items() if buttons & mask]
                print(f"{now:7.3f} len={len(report)} buttons={buttons:#08x} {names} "
                      f"L={stick(report[5:8])} R={stick(report[8:11])}")
                last_buttons = buttons
                last_print = now
    counts = {f"{h:#06x}": n for h, n in per_handle.items()}
    print(f"{count} reports on {H_INPUT:#06x}; packets per handle: {counts}; capture in {capture_path}")
    sock.send(struct.pack("<BH", ATT_READ_REQ, 0x0035))
    try:
        alive = wait_for(sock, lambda p: p if p[0] in (ATT_READ_RSP, 0x01) else None, 2)
        print(f"link alive, read reply {alive.hex()}")
    except (TimeoutError, OSError) as err:
        print(f"link check failed: {err}")
    sock.close()


if __name__ == "__main__":
    main()
