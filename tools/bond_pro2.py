"""Bond a Pro Controller 2 to this Pi with Nintendo's command 0x15 pairing, after backing up its stored pairing."""
import json
import os
import pathlib
import re
import secrets
import subprocess
import sys
import time

import stream_pro2 as pro2

PAIRING_FLASH = 0x1FA000
ENTRY_SIZE = 0x28
CONFIG_DIR = pathlib.Path.home() / ".config" / "pis2cr"


def host_address(adapter="hci0"):
    out = subprocess.run(["hciconfig", adapter], capture_output=True, text=True, check=True).stdout
    return re.search(r"BD Address: ((?:[0-9A-F]{2}:){5}[0-9A-F]{2})", out).group(1)


def to_wire(addr):
    return bytes(int(part, 16) for part in addr.split(":"))[::-1]


def from_wire(raw):
    return ":".join(f"{b:02X}" for b in raw[::-1])


def aes_ecb(key, block):
    return subprocess.run(["openssl", "enc", "-aes-128-ecb", "-nosalt", "-nopad", "-K", key.hex()],
                          input=block, capture_output=True, check=True).stdout


def write_private(path, data):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)


def stored_entries(flash):
    count = flash[0]
    return [from_wire(flash[8 + i * ENTRY_SIZE:14 + i * ENTRY_SIZE]) for i in range(min(count, 2))]


def main():
    addr = sys.argv[1] if len(sys.argv) > 1 else sys.exit("usage: bond_pro2.py CONTROLLER_MAC")
    wait = float(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2][0].isdigit() else 120
    dry_run = "--dry-run" in sys.argv
    sock = pro2.open_controller(addr, wait)
    print("connected", flush=True)

    flash = pro2.read_flash(sock, PAIRING_FLASH, 0x40) + pro2.read_flash(sock, PAIRING_FLASH + 0x40, 0x40)
    backup = CONFIG_DIR / f"controller-pairing-backup-{time.strftime('%Y%m%d-%H%M%S')}.bin"
    write_private(backup, flash)
    print(f"current stored hosts: {stored_entries(flash)}; backup written to {backup}")
    if dry_run:
        print("dry run: nothing changed")
        return

    host = host_address()
    host2 = host[:-2] + f"{(int(host[-2:], 16) - 1) & 0xFF:02X}"
    reply = pro2.command(sock, 0x15, 0x01, bytes([0, 2]) + to_wire(host) + to_wire(host2))
    print(f"controller address reported: {from_wire(reply[3:9])}")

    a1 = secrets.token_bytes(16)
    b1 = pro2.command(sock, 0x15, 0x04, b"\x00" + a1)[1:17]
    ltk = bytes(x ^ y for x, y in zip(a1, b1))

    a2 = secrets.token_bytes(16)
    b2 = pro2.command(sock, 0x15, 0x02, b"\x00" + a2)[1:17]
    expected = aes_ecb(ltk[::-1], a2[::-1])
    if b2 != expected and b2 != expected[::-1]:
        print("key confirmation FAILED; nothing was stored on the controller")
        sys.exit(1)
    print("key confirmation ok")

    pro2.command(sock, 0x15, 0x03, b"\x00")
    write_private(CONFIG_DIR / "pairing.json", json.dumps(
        {"controller": addr, "host": host, "host2": host2, "ltk": ltk.hex()}).encode())
    pro2.command(sock, 0x0A, 0x02, bytes([3, 0, 0, 0]))
    print(f"paired: controller now stores {host} and {host2}")
    sock.close()


if __name__ == "__main__":
    main()
