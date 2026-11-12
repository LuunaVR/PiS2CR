"""Guided motion capture: player LEDs cue each motion while raw 0x2E reports are saved to a file."""
import socket
import struct
import sys
import time

import stream_pro2 as pro2

# LED pattern -> what the user should do. 1 light = still, 2 = yaw, 3 = pitch, 4 = roll.
PHASES = [
    ("still", 0x01),
    ("yaw", 0x03),
    ("pitch", 0x07),
    ("roll", 0x0F),
    ("still2", 0x01),
]
WARMUP_LEDS = 0x09
WARMUP_SECONDS = 4
PHASE_SECONDS = 5


def main():
    addr = sys.argv[1] if len(sys.argv) > 1 else sys.exit("usage: motion_capture.py CONTROLLER_MAC")
    out_path = sys.argv[2] if len(sys.argv) > 2 else "/tmp/motion.txt"
    sock = pro2.open_controller(addr, 300)
    pro2.command(sock, 0x09, 0x07, bytes([WARMUP_LEDS, 0, 0, 0]))
    time.sleep(WARMUP_SECONDS)
    with open(out_path, "w") as out:
        for name, leds in PHASES:
            pro2.command(sock, 0x09, 0x07, bytes([leds, 0, 0, 0]))
            end = time.monotonic() + PHASE_SECONDS
            while time.monotonic() < end:
                try:
                    pkt = sock.recv(512)
                except socket.timeout:
                    continue
                if pkt[0] == pro2.ATT_NOTIFY and struct.unpack("<H", pkt[1:3])[0] == pro2.H_INPUT:
                    out.write(f"{name} {time.monotonic():.4f} {pkt[3:].hex()}\n")
    pro2.command(sock, 0x09, 0x07, bytes([0x01, 0, 0, 0]))
    sock.close()
    print("done")


if __name__ == "__main__":
    main()
