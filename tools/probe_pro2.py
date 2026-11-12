"""Connect to a Switch 2 Pro Controller over a raw L2CAP ATT socket and list its GATT layout."""
import ctypes
import errno
import select
import socket
import struct
import sys
import time

libc = ctypes.CDLL("libc.so.6", use_errno=True)

SOL_BLUETOOTH = 274
BT_SECURITY = 4
BT_SECURITY_LOW = 1
BDADDR_LE_PUBLIC = 1
ATT_CID = 4

ATT_ERROR = 0x01
ATT_READ_BY_TYPE_REQ = 0x08
ATT_READ_BY_TYPE_RSP = 0x09
ATT_READ_BY_GROUP_REQ = 0x10
ATT_READ_BY_GROUP_RSP = 0x11
UUID_PRIMARY_SERVICE = b"\x00\x28"
UUID_CHARACTERISTIC = b"\x03\x28"


def fmt_uuid(raw):
    raw = raw[::-1]
    if len(raw) == 2:
        return raw.hex()
    h = raw.hex()
    return f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:]}"


def connect_once(addr, wait):
    sock = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_SEQPACKET, socket.BTPROTO_L2CAP)
    sock.setsockopt(SOL_BLUETOOTH, BT_SECURITY, struct.pack("BB", BT_SECURITY_LOW, 0))
    sock.setblocking(False)
    mac = bytes(int(part, 16) for part in addr.split(":"))[::-1]
    # Python 3.13 cannot set the L2CAP CID or LE address type, so build sockaddr_l2 by hand.
    sockaddr = struct.pack("<HH6sHB", socket.AF_BLUETOOTH, 0, mac, ATT_CID, BDADDR_LE_PUBLIC) + b"\x00"
    # The kernel needs the socket bound to the ATT fixed channel before it will connect to an LE device.
    local = struct.pack("<HH6sHB", socket.AF_BLUETOOTH, 0, bytes(6), ATT_CID, BDADDR_LE_PUBLIC) + b"\x00"
    if libc.bind(sock.fileno(), local, len(local)) != 0:
        code = ctypes.get_errno()
        sock.close()
        raise OSError(code, f"bind: {errno.errorcode.get(code, 'error')}")
    if libc.connect(sock.fileno(), sockaddr, len(sockaddr)) != 0:
        code = ctypes.get_errno()
        if code != errno.EINPROGRESS:
            sock.close()
            raise OSError(code, errno.errorcode.get(code, "connect error"))
        _, writable, _ = select.select([], [sock], [], wait)
        code = sock.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR) if writable else errno.ETIMEDOUT
        if code:
            sock.close()
            raise OSError(code, errno.errorcode.get(code, "connect error"))
    sock.settimeout(5)
    return sock


def connect(addr, timeout):
    deadline = time.monotonic() + timeout
    while True:
        try:
            return connect_once(addr, 5)
        except OSError as err:
            if time.monotonic() > deadline:
                raise
            print(f"connect failed ({err}); retrying")
            time.sleep(0.5)


def read_all(sock, opcode, rsp_opcode, uuid, entry_parser):
    start = 1
    while start <= 0xFFFF:
        sock.send(struct.pack("<BHH", opcode, start, 0xFFFF) + uuid)
        rsp = sock.recv(512)
        if rsp[0] == ATT_ERROR or rsp[0] != rsp_opcode:
            return
        size = rsp[1]
        last = start
        for off in range(2, len(rsp), size):
            last = entry_parser(rsp[off:off + size])
        start = last + 1


def main():
    addr = sys.argv[1] if len(sys.argv) > 1 else sys.exit("usage: probe_pro2.py CONTROLLER_MAC")
    timeout = float(sys.argv[2]) if len(sys.argv) > 2 else 30
    sock = connect(addr, timeout)
    print(f"connected to {addr}")

    def service(entry):
        start, end = struct.unpack("<HH", entry[:4])
        print(f"service {start:#06x}-{end:#06x} uuid {fmt_uuid(entry[4:])}")
        return end

    def characteristic(entry):
        handle, props, value_handle = struct.unpack("<HBH", entry[:5])
        print(f"  char handle {handle:#06x} value {value_handle:#06x} props {props:#04x} uuid {fmt_uuid(entry[5:])}")
        return handle

    read_all(sock, ATT_READ_BY_GROUP_REQ, ATT_READ_BY_GROUP_RSP, UUID_PRIMARY_SERVICE, service)
    read_all(sock, ATT_READ_BY_TYPE_REQ, ATT_READ_BY_TYPE_RSP, UUID_CHARACTERISTIC, characteristic)
    sock.close()


if __name__ == "__main__":
    main()
