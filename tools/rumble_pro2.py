"""Cycle the player LEDs and rumble the motors on a Pro Controller 2 so the effects can be felt and seen."""
import struct
import sys
import time

import stream_pro2 as pro2

H_VIBRATION = 0x0012
LF_FREQ = 0x0E1
HF_FREQ = 0x1E1
LED_PATTERNS = (0x01, 0x03, 0x07, 0x0F, 0x09, 0x06, 0x01)


def sample(amp):
    """5-byte HD rumble sample that drives only the low-frequency band."""
    return (LF_FREQ | (int(amp * 0x3FF) << 10) | (HF_FREQ << 20)).to_bytes(5, "little")


class Rumble:
    def __init__(self, sock):
        self.sock = sock
        self.counter = 0

    def send(self, left, right):
        def block(amp):
            return bytes([0x50 + (self.counter & 0x0F)]) + sample(amp) * 3

        self.sock.send(struct.pack("<BH", pro2.ATT_WRITE_CMD, H_VIBRATION) + b"\x00" + block(left) + block(right))
        self.counter += 1

    def burst(self, left, right, seconds):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self.send(left, right)
            time.sleep(0.016)
        self.send(0, 0)


def say(start, text):
    print(f"{time.monotonic() - start:6.1f}s {text}", flush=True)


def main():
    addr = sys.argv[1] if len(sys.argv) > 1 else sys.exit("usage: rumble_pro2.py CONTROLLER_MAC")
    sock = pro2.open_controller(addr, float(sys.argv[2]) if len(sys.argv) > 2 else 60)
    start = time.monotonic()
    say(start, "connected; LED test")
    for pattern in LED_PATTERNS:
        reply = pro2.command(sock, 0x09, 0x07, bytes([pattern, 0, 0, 0]))
        say(start, f"LED pattern {pattern:#04x} reply {reply.hex()}")
        time.sleep(2.5)

    if "leds" in sys.argv[3:]:
        sock.close()
        return

    rumble = Rumble(sock)
    say(start, "rumble test")
    time.sleep(1)
    steps = (("both 0.5", 0.5, 0.5), ("left 0.5", 0.5, 0.0), ("right 0.5", 0.0, 0.5),
             ("both 0.2", 0.2, 0.2), ("both 0.5", 0.5, 0.5), ("both 1.0", 1.0, 1.0))
    for label, left, right in steps:
        say(start, f"rumble {label}")
        rumble.burst(left, right, 0.7)
        time.sleep(1.3)
    say(start, "done")
    sock.close()


if __name__ == "__main__":
    main()
