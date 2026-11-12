"""Relay a Pro Controller 2 (BLE) to the Classic HID backend's control socket, reconnecting forever."""
import json
import os
import re
import socket
import struct
import subprocess
import sys
import threading
import time

import pro2_chat as chat
import pro2_dashboard as dashboard
import pro2_history
import pro2_macro
import pro2_motion
import pro2_overlay_settings
import pro2_twitch as twitch
import stream_pro2 as pro2

# The backend runs with --desktop <uid of the user running this relay>.
CONTROL_SOCKET = f"/run/pcble2gamepad/{os.getuid()}/pro.sock"
SWITCH_MAC = os.environ.get("PIS2CR_SWITCH_MAC", "")

# (Pro 2 byte index, Pro 2 mask) -> (Switch report byte index, Switch mask)
BUTTON_MAP = {
    (2, 0x04): (0, 0x01),  # Y
    (2, 0x08): (0, 0x02),  # X
    (2, 0x01): (0, 0x04),  # B
    (2, 0x02): (0, 0x08),  # A
    (2, 0x10): (0, 0x40),  # R
    (2, 0x20): (0, 0x80),  # ZR
    (3, 0x40): (1, 0x01),  # Minus
    (2, 0x40): (1, 0x02),  # Plus
    (2, 0x80): (1, 0x04),  # right stick click
    (3, 0x80): (1, 0x08),  # left stick click
    (4, 0x01): (1, 0x10),  # Home
    (4, 0x02): (1, 0x20),  # Capture
    (3, 0x01): (2, 0x01),  # Down
    (3, 0x08): (2, 0x02),  # Up
    (3, 0x02): (2, 0x04),  # Right
    (3, 0x04): (2, 0x08),  # Left
    (3, 0x10): (2, 0x40),  # L
    (3, 0x20): (2, 0x80),  # ZL
}

# Emulated stick calibration of the reference backend: centre, positive and negative travel per axis.
EMU_LEFT = ((2159, 1466, 1517), (1916, 1583, 1465))
EMU_RIGHT = ((2070, 1414, 1522), (2013, 1510, 1531))
REAL_TRAVEL = 1600
DEADZONE = 60
# The backend neutralises inputs after 500 ms and stops its session after 5 s without any request.
KEEPALIVE_S = 0.2
SILENCE_S = 3.0
BATTERY_POLL_S = 20
SWITCH_RETRY_S = 15  # how often to redial a Switch that dropped while the controller is still connected


def log(message):
    print(f"{time.strftime('%H:%M:%S')} {message}", file=sys.stderr, flush=True)


def _names_to_switch():
    names = {}
    for (src_byte, src_mask), dst in BUTTON_MAP.items():
        bit = src_mask << (8 * (src_byte - 2))
        names.update({name: dst for name, mask in pro2.BUTTONS.items() if mask == bit})
    return names


NAME_TO_SWITCH = _names_to_switch()
NEUTRAL_STICKS = (EMU_LEFT[0][0], EMU_LEFT[1][0], EMU_RIGHT[0][0], EMU_RIGHT[1][0])


def convert_buttons(report):
    out = [0, 0, 0]
    for (src_byte, src_mask), (dst_byte, dst_mask) in BUTTON_MAP.items():
        if report[src_byte] & src_mask:
            out[dst_byte] |= dst_mask
    return tuple(out)


def convert_axis(raw, real_center, emu):
    center, pos, neg = emu
    delta = raw - real_center
    if abs(delta) < DEADZONE:
        return center
    n = max(-1.0, min(1.0, delta / REAL_TRAVEL))
    return max(0, min(4095, round(center + n * (pos if n > 0 else neg))))


def inject_axis(n, emu):
    center, pos, neg = emu
    return max(0, min(4095, round(center + n * (pos if n > 0 else neg))))


class Injector:
    """Buttons pressed from the dashboard; a press expires unless refreshed, so a closed tab cannot leave one stuck."""

    HOLD_TTL_S = 1.5
    MIN_PRESS_S = 0.06

    def __init__(self, wake):
        self.wake = wake
        self.lock = threading.Lock()
        self.buttons = {}  # name -> [pressed_at, expires_at]
        self.sticks = {}  # side -> [x, y, expires_at], x and y in -1..1 with y up
        self.chat_buttons = frozenset()
        self.chat_sticks = {}
        self.macro_buttons = frozenset()
        self.macro_sticks = {}

    def press(self, name, down):
        now = time.monotonic()
        expires = None
        with self.lock:
            entry = self.buttons.get(name)
            if down:
                if entry is None or entry[1] <= now:
                    entry = self.buttons[name] = [now, now]
                entry[1] = now + self.HOLD_TTL_S
            elif entry is not None:
                entry[1] = expires = max(now, entry[0] + self.MIN_PRESS_S)
        self.wake.set()
        if expires is not None:
            threading.Timer(expires - now + 0.005, self.wake.set).start()

    def stick(self, side, x, y, active):
        with self.lock:
            if active:
                self.sticks[side] = [x, y, time.monotonic() + self.HOLD_TTL_S]
            else:
                self.sticks.pop(side, None)
        self.wake.set()

    def set_chat(self, buttons, sticks):
        """Inputs currently held by chat commands; the chat controller owns their timing."""
        with self.lock:
            self.chat_buttons = frozenset(buttons)
            self.chat_sticks = dict(sticks)
        self.wake.set()

    def set_macro(self, buttons, sticks):
        """Inputs held by a running macro; its player owns their timing. They show up like chat's inputs."""
        with self.lock:
            self.macro_buttons = frozenset(buttons)
            self.macro_sticks = dict(sticks)
        self.wake.set()

    def split(self):
        """Held inputs by origin: (dashboard buttons, chat buttons, dashboard sticks, chat sticks)."""
        now = time.monotonic()
        with self.lock:
            self.buttons = {n: e for n, e in self.buttons.items() if e[1] > now}
            self.sticks = {s: e for s, e in self.sticks.items() if e[2] > now}
            return (frozenset(self.buttons), self.chat_buttons | self.macro_buttons,
                    {s: (e[0], e[1]) for s, e in self.sticks.items()}, {**self.chat_sticks, **self.macro_sticks})


def _rounded(sticks):
    return {side: [round(v, 3) for v in xy] for side, xy in sticks.items()}


class Sender(threading.Thread):
    """Sends the latest frame to the backend on change and at least every KEEPALIVE_S; None means neutral."""

    def __init__(self, path):
        super().__init__(daemon=True)
        self.path = path
        self.frame = None
        self.wake = threading.Event()
        self.injector = Injector(self.wake)
        self.tracker = None  # history.StreamerTracker, set by main
        self.cancel_combo = pro2_macro.CancelCombo()
        self.on_cancel = None  # called when the real controller holds LB and RB together
        self.sources_shown = None
        self.status = None
        self.switch_state = None
        self.battery = None

    def set(self, frame):
        changed = frame != self.frame
        self.frame = frame
        if changed:
            self.wake.set()

    def request(self, payload, timeout=1):
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            sock.connect(self.path)
            sock.sendall(json.dumps(payload).encode() + b"\n")
            reply = b""
            while not reply.endswith(b"\n"):
                chunk = sock.recv(8192)
                if not chunk:
                    break
                reply += chunk
        return json.loads(reply)

    def run(self):
        while True:
            self.wake.wait(KEEPALIVE_S)
            self.wake.clear()
            frame = self.frame
            ui_buttons, chat_buttons, ui_sticks, chat_sticks = self.injector.split()
            injected = ui_buttons | chat_buttons
            sticks = {**chat_sticks, **ui_sticks}
            sources = (ui_buttons, chat_buttons, ui_sticks, chat_sticks)
            if sources != self.sources_shown:
                self.sources_shown = sources
                dashboard.update(injected=sorted(ui_buttons), chat_buttons=sorted(chat_buttons),
                                 injected_sticks=_rounded(ui_sticks), chat_sticks=_rounded(chat_sticks))
                if self.tracker is not None:
                    self.tracker.update_ui(ui_buttons)
            if frame is None and not injected and not sticks:
                payload = {"version": 1, "method": "release"}
            else:
                buttons = list(frame[0]) if frame else [0, 0, 0]
                for name in injected:
                    byte, mask = NAME_TO_SWITCH[name]
                    buttons[byte] |= mask
                axes = list(frame[1] if frame else NEUTRAL_STICKS)
                for side, (x, y) in sticks.items():
                    first = 0 if side == "left" else 2
                    emu = EMU_LEFT if side == "left" else EMU_RIGHT
                    axes[first] = inject_axis(x, emu[0])
                    axes[first + 1] = inject_axis(y, emu[1])
                payload = {"version": 1, "method": "input", "buttons": buttons, "sticks": axes}
                if frame and frame[2] is not None:
                    payload["imu"] = list(frame[2])
            if self.battery is not None:
                payload["battery"] = self.battery
            try:
                reply = self.request(payload)
                status = "ok" if reply.get("ok") else f"rejected: {reply.get('error')}"
                switch = reply.get("result", {}).get("state", "unknown")
                if switch != self.switch_state:
                    log(f"switch link {switch}")
                    self.switch_state = switch
                    dashboard.update(switch=switch)
            except (OSError, ValueError) as err:
                status = f"unreachable: {err}"
                if self.switch_state is not None:
                    self.switch_state = None
                    dashboard.update(switch="unknown")
            if status != self.status:
                log(f"backend {status}")
                self.status = status


def reconnect_switch(sender, reason, quiet=False):
    """Ask the backend to dial the Switch if the link is down; runs off-thread because the backend blocks while dialling."""
    if sender.switch_state == "connected" or not SWITCH_MAC:
        return

    def work():
        try:
            reply = sender.request({"version": 1, "method": "reconnect", "address": SWITCH_MAC}, timeout=15)
            if not quiet or not reply.get("ok"):
                log(f"switch reconnect ({reason}): {'started' if reply.get('ok') else reply.get('error')}")
        except (OSError, ValueError) as err:
            if not quiet:
                log(f"switch reconnect ({reason}) failed: {err}")

    threading.Thread(target=work, daemon=True).start()


def speed_up_link(addr):
    """Ask the controller for a 7.5 ms connection interval; the kernel keeps the slower one it picked on connect."""
    try:
        listing = subprocess.run(["hcitool", "con"], capture_output=True, text=True, timeout=3).stdout
        handle = re.search(rf"LE {addr} handle (\d+)", listing)
        if handle is None:
            log("link speed-up skipped: connection handle not found")
            return
        result = subprocess.run(
            ["sudo", "-n", "/usr/bin/hcitool", "lecup", "--handle", handle.group(1),
             "--min", "6", "--max", "6", "--latency", "0", "--timeout", "200"],
            capture_output=True, text=True, timeout=5)
        log("link interval 7.5 ms requested" if result.returncode == 0
            else f"link speed-up failed: {result.stderr.strip()}")
    except (OSError, subprocess.SubprocessError) as err:
        log(f"link speed-up failed: {err}")


def read_stick_centers(sock):
    """Neutral stick positions from the controller's own calibration (user block if valid, else factory)."""
    centers = []
    for factory, user in ((0x130A8, 0x1FC040), (0x130E8, 0x1FC060)):
        user_block = pro2.read_flash(sock, user, 11)
        block = user_block[2:] if user_block[:2] == b"\xb2\xa1" else pro2.read_flash(sock, factory, 9)
        centers.append((block[0] | (block[1] & 0x0F) << 8, block[1] >> 4 | block[2] << 4))
    return tuple(centers)


def read_battery(sock, report_power):
    """Ask the controller for its own battery voltage and charge status; log them when they change."""
    try:
        mv = int.from_bytes(pro2.command(sock, 0x0B, 0x03)[:2], "little")
        charge = pro2.command(sock, 0x0B, 0x04)[:2].hex()
    except (TimeoutError, RuntimeError):
        return
    seen = (mv, charge, report_power)
    if seen != read_battery.last:
        read_battery.last = seen
        log(f"battery {mv} mV, charge status 0x{charge}, report power byte 0x{report_power:02x}")
    dashboard.update(battery_mv=mv)


read_battery.last = None


def stream(sock, sender):
    try:
        centers = read_stick_centers(sock)
    except (TimeoutError, RuntimeError):
        centers = ((2048, 2048), (2048, 2048))
    log(f"stick centres {centers}")
    imu = pro2_motion.SwitchImu()
    # Only some reports carry gyro samples; a frame without motion makes the backend send a flat, zero-rate sample.
    held_imu = None
    last_report = time.monotonic()
    next_battery = 0.0
    next_switch_retry = time.monotonic() + SWITCH_RETRY_S
    power_byte = 0
    while True:
        if time.monotonic() >= next_battery:
            read_battery(sock, power_byte)
            next_battery = time.monotonic() + BATTERY_POLL_S
        if time.monotonic() >= next_switch_retry:
            reconnect_switch(sender, "link down", quiet=True)
            next_switch_retry = time.monotonic() + SWITCH_RETRY_S
        try:
            pkt = sock.recv(512)
        except socket.timeout:
            if time.monotonic() - last_report > SILENCE_S:
                raise ConnectionError("no reports")
            continue
        if not pkt:
            raise ConnectionError("link closed")
        last_report = time.monotonic()
        if len(pkt) < 14 or pkt[0] != pro2.ATT_NOTIFY or struct.unpack("<H", pkt[1:3])[0] != pro2.H_INPUT:
            continue
        report = pkt[3:]
        power_byte = report[1]
        dashboard.publish(report)
        lraw = pro2.stick(report[5:8])
        rraw = pro2.stick(report[8:11])
        if sender.tracker is not None or sender.on_cancel is not None:
            mask = int.from_bytes(report[2:5], "little")
            pressed = [name for name, bit in pro2.BUTTONS.items() if mask & bit]
            if sender.on_cancel is not None and sender.cancel_combo.update(pressed):
                sender.on_cancel()
        if sender.tracker is not None:
            unit = lambda raw, center: max(-1.0, min(1.0, (raw - center) / REAL_TRAVEL))
            sender.tracker.update_real(
                pressed,
                (unit(lraw[0], centers[0][0]), unit(lraw[1], centers[0][1])),
                (unit(rraw[0], centers[1][0]), unit(rraw[1], centers[1][1])))
        decoded = pro2_motion.decode(report[66:106]) if report[65] == 40 else None
        if decoded is not None:
            switch_imu, gyro_dps, accel_g = imu.convert(*decoded)
            held_imu = tuple(switch_imu)
            dashboard.update(gyro_dps=[round(v, 1) for v in gyro_dps], accel_g=[round(v, 3) for v in accel_g])
        sender.battery = pro2_motion.switch_battery_byte(report[1])
        sender.set((convert_buttons(report), (
            convert_axis(lraw[0], centers[0][0], EMU_LEFT[0]),
            convert_axis(lraw[1], centers[0][1], EMU_LEFT[1]),
            convert_axis(rraw[0], centers[1][0], EMU_RIGHT[0]),
            convert_axis(rraw[1], centers[1][1], EMU_RIGHT[1]),
        ), held_imu))


def main():
    addr = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("PIS2CR_CONTROLLER_MAC", "")
    if not re.fullmatch(r"([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}", addr):
        sys.exit("Controller address missing: pass it as the first argument or set PIS2CR_CONTROLLER_MAC.")
    sender = Sender(CONTROL_SOCKET)
    sender.start()
    dashboard.allowed_buttons = frozenset(NAME_TO_SWITCH)
    dashboard.press_handler = sender.injector.press
    dashboard.stick_handler = sender.injector.stick
    history = pro2_history.InputHistory()
    sender.tracker = pro2_history.StreamerTracker(history)
    dashboard.history = history
    dashboard.overlay_settings = pro2_overlay_settings.OverlaySettings()
    controller = chat.ChatController(sender.injector.set_chat, on_accept=history.add_chat)
    controller.start()
    dashboard.chat = controller
    dashboard.macros = pro2_macro.MacroStore(NAME_TO_SWITCH)
    dashboard.macro_player = pro2_macro.MacroPlayer(sender.injector.set_macro, on_change=controller.pause)

    def run_macro(name, user):
        macro = dashboard.macros.get(name)
        if macro is None:
            return {"ok": False, "reason": f"no macro called {name}"}
        if not dashboard.macro_player.play(name, macro):
            return {"ok": False, "reason": "a performance is already running"}
        log(f"macro {name} started by {user}")
        return {"ok": True, "seconds": pro2_macro.play_time(macro)}

    controller.set_macro_runner(run_macro, dashboard.macro_player.stop)

    def cancel_macro():
        if dashboard.macro_player.state()["playing"]:
            dashboard.macro_player.stop()
            log("macro cancelled by LB + RB on the controller")

    sender.on_cancel = cancel_macro
    dashboard.twitch = twitch.TwitchBridge(controller)
    dashboard.twitch.start()
    dashboard.update(injectable=sorted(NAME_TO_SWITCH))
    dashboard.serve()
    log(f"relay started, waiting for controller {addr}")
    while True:
        try:
            # One connect attempt (about 5 s) per loop; the controller advertises after any button press.
            sock = pro2.open_controller(addr, 0)
        except (OSError, RuntimeError):
            time.sleep(0.5)
            continue
        log("controller connected")
        speed_up_link(addr)
        reconnect_switch(sender, "controller connected")
        try:
            stream(sock, sender)
        except OSError as err:
            log(f"controller lost: {err}")
        finally:
            sock.close()
            sender.set(None)
            sender.tracker.update_real(set())
            dashboard.reset("controller disconnected (press any button)")
            reconnect_switch(sender, "controller lost")


if __name__ == "__main__":
    main()
