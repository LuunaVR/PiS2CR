"""Enable notifications on the Pro Controller 2 input characteristics and print raw packets."""
import socket
import struct
import sys
import time

from probe_pro2 import connect

ATT_WRITE_REQ = 0x12
ATT_NOTIFY = 0x1B
NOTIFY_VALUE_HANDLES = (0x000A, 0x0026)


def main():
    addr = sys.argv[1] if len(sys.argv) > 1 else sys.exit("usage: listen_pro2.py CONTROLLER_MAC")
    connect_timeout = float(sys.argv[2]) if len(sys.argv) > 2 else 60
    listen_seconds = float(sys.argv[3]) if len(sys.argv) > 3 else 20
    sock = connect(addr, connect_timeout)
    print(f"connected to {addr}")

    # Assumes each client config descriptor sits right after its value handle.
    for value_handle in NOTIFY_VALUE_HANDLES:
        sock.send(struct.pack("<BHH", ATT_WRITE_REQ, value_handle + 1, 0x0001))
        print(f"enable notify {value_handle + 1:#06x}: {sock.recv(64).hex()}")

    start = time.monotonic()
    sock.settimeout(1)
    while time.monotonic() - start < listen_seconds:
        try:
            pkt = sock.recv(512)
        except socket.timeout:
            continue
        if pkt[0] == ATT_NOTIFY:
            handle = struct.unpack("<H", pkt[1:3])[0]
            print(f"{time.monotonic() - start:7.3f} {handle:#06x} {pkt[3:].hex()}")
        else:
            print(f"{time.monotonic() - start:7.3f} other {pkt.hex()}")
    sock.close()


if __name__ == "__main__":
    main()
