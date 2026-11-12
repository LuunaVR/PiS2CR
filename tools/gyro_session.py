"""Guided motion capture: N short beeps start phase N, one long buzz stops it; LEDs show the phase number."""
import socket
import struct
import sys
import time

import stream_pro2 as pro2

# (name, instruction, seconds)
PHASES = [
    ("still_flat", "lie flat on the desk face up, hands off", 8),
    ("top_up", "hold still with the shoulder-button edge pointing up", 8),
    ("left_down", "hold still with the left side pointing down", 8),
    ("yaw", "flat on the desk, slowly spin left and right like a plate", 10),
    ("pitch", "in the air, tilt the top edge up and down slowly", 10),
    ("roll", "in the air, tilt left side down then right side down slowly", 10),
    ("shake", "shake it quickly in all directions", 6),
]
SETTLE_S = 8
GAP_S = 4
LED_PATTERNS = [0x01, 0x03, 0x07, 0x0F, 0x09, 0x05, 0x0D]


def drain(sock, seconds, capture, t0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        try:
            pkt = sock.recv(512)
        except socket.timeout:
            continue
        if pkt[0] == pro2.ATT_NOTIFY and struct.unpack("<H", pkt[1:3])[0] == pro2.H_INPUT:
            capture.write(f"{time.monotonic() - t0:.4f} {pkt[3:].hex()}\n")


def main():
    addr = sys.argv[1]
    out = sys.argv[2]
    sock = pro2.open_controller(addr, float(sys.argv[3]))
    print("connected; put the controller on the desk", flush=True)
    t0 = time.monotonic()
    with open(out, "w") as capture:
        drain(sock, SETTLE_S, capture, t0)
        for i, (name, text, seconds) in enumerate(PHASES):
            pro2.command(sock, 0x09, 0x07, bytes([LED_PATTERNS[i], 0, 0, 0]))
            print(f"phase {i + 1} {name} in {GAP_S}s: {text}", flush=True)
            drain(sock, GAP_S, capture, t0)
            for _ in range(i + 1):
                pro2.command(sock, 0x0A, 0x02, bytes([6, 0, 0, 0]))
                drain(sock, 0.5, capture, t0)
            capture.write(f"# START {name} {time.monotonic() - t0:.4f}\n")
            print(f"  GO {name}", flush=True)
            drain(sock, seconds, capture, t0)
            capture.write(f"# END {name} {time.monotonic() - t0:.4f}\n")
            pro2.command(sock, 0x0A, 0x02, bytes([1, 0, 0, 0]))
            print(f"  STOP {name}", flush=True)
        capture.flush()
    print("done", flush=True)
    sock.close()


if __name__ == "__main__":
    main()
