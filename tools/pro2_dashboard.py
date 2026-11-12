"""Serve a local web page showing the live Pro Controller 2 input published by the relay; clicks inject presses."""
import json
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import stream_pro2 as pro2

HERE = Path(__file__).parent
PAGES = {"/": HERE / "pro2_dashboard.html", "/overlay": HERE / "pro2_overlay.html"}
FRAGMENTS = {"<!--PAD-->": HERE / "pro2_pad.html", "<!--TWITCH-->": HERE / "pro2_twitch.html",
             "<!--OVERLAYS-->": HERE / "pro2_overlays.html", "<!--FUN-->": HERE / "pro2_fun.html",
             "<!--DEBUG-->": HERE / "pro2_debug.html"}
IDLE_STATE = {"connected": False, "status": "waiting for controller (press any button)",
              "buttons": [], "left": [2048, 2048], "right": [2048, 2048], "rate": 0.0,
              "counter": 0, "button_mask": "000000", "external_power": False, "charging": False,
              "motion_present": False, "motion_hex": "", "raw": ""}
state = {**IDLE_STATE, "battery_level": 0, "battery_pct": 0, "battery_mv": None, "switch": "unknown",
         "gyro_dps": None, "accel_g": None, "injected": [], "injectable": [], "injected_sticks": {},
         "chat_buttons": [], "chat_sticks": {}}
lock = threading.Lock()

# Set by the relay: button names the page may press, and the callbacks taking (name, down) and (side, x, y, active).
allowed_buttons = frozenset()
press_handler = None
stick_handler = None
chat = None     # pro2_chat.ChatController, set by the relay
twitch = None   # pro2_twitch.TwitchBridge, set by the relay
history = None  # pro2_history.InputHistory, set by the relay
overlay_settings = None  # pro2_overlay_settings.OverlaySettings, set by the relay
macros = None  # pro2_macro.MacroStore, set by the relay
macro_player = None  # pro2_macro.MacroPlayer, set by the relay
STICK_SIDES = frozenset({"left", "right"})
MAX_BODY = 4096
MAX_BODY_BY_ROUTE = {"/macro/save": 400_000}   # a macro of a few thousand steps is bigger than an ordinary request
MOTION_HOLD_S = 1.0


def update(**fields):
    with lock:
        state.update(fields)


stamps = deque(maxlen=40)
last_motion = 0.0


def publish(report):
    """Record one 112-byte input report from the relay; the dashboard itself never touches Bluetooth."""
    global last_motion
    now = time.monotonic()
    stamps.append(now)
    if len(report) >= 106 and report[65] == 0x28:
        last_motion = now
    mask = int.from_bytes(report[2:5], "little")
    rate = (len(stamps) - 1) / (stamps[-1] - stamps[0]) if len(stamps) > 1 else 0.0
    power = report[1]
    update(connected=True, status="connected",
           buttons=[name for name, bit in pro2.BUTTONS.items() if mask & bit],
           left=list(pro2.stick(report[5:8])), right=list(pro2.stick(report[8:11])),
           rate=round(rate, 1), raw=report.hex(), counter=report[0],
           button_mask=f"{mask:06x}", external_power=bool(power & 1),
           charging=bool(power & 2), battery_level=(power >> 2) & 0xF,
           battery_pct=5 * round(min((power >> 2) & 0xF, 9) * 100 / 9 / 5),
           motion_present=now - last_motion < MOTION_HOLD_S,
           motion_hex=report[66:106].hex())


def reset(status):
    stamps.clear()
    update(**{**IDLE_STATE, "status": status})


class ChatUnavailable(Exception):
    pass


def _press(request):
    name, down = request["button"], request["down"]
    if name not in allowed_buttons or not isinstance(down, bool) or press_handler is None:
        raise ValueError("bad press")
    press_handler(name, down)


def _stick(request):
    side, active = request["stick"], request["active"]
    x, y = request["x"], request["y"]
    if (side not in STICK_SIDES or not isinstance(active, bool) or stick_handler is None
            or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not -1 <= v <= 1 for v in (x, y))):
        raise ValueError("bad stick")
    stick_handler(side, x, y, active)


def twitch_state():
    _need_chat()
    return {**chat.snapshot(), "twitch": twitch.status()}


def _need_chat():
    if chat is None or twitch is None:
        raise ChatUnavailable("chat control is not running")


def _twitch_settings(request):
    _need_chat()
    chat.update_settings(request["settings"], request.get("confirm_restricted", False))
    return twitch_state()


def _twitch_test(request):
    _need_chat()
    text, run = request["text"], request.get("run", False)
    if not isinstance(text, str) or len(text) > 200 or not isinstance(run, bool):
        raise ValueError("bad test message")
    return chat.handle_message("you (test)", text, source="test", run=run)


def _twitch_redeem(request):
    """Simulate a viewer redeeming one of the configured rewards."""
    _need_chat()
    return chat.redeem("you (test)", request["reward_id"], source="test")


def _reward_create(request):
    _need_chat()
    return twitch.create_reward(request["reward"])


def _reward_update(request):
    _need_chat()
    return twitch.update_reward(request["id"], request["reward"])


def _reward_delete(request):
    _need_chat()
    reward_id = request["id"]
    twitch.delete_reward(reward_id)
    kept = [r for r in chat.snapshot()["settings"]["redeems"] if r["id"] != reward_id]
    chat.update_settings({"redeems": kept})
    return twitch_state()


def overlay_data(since):
    idle_timer = {"active": False, "remaining_s": 0, "total_s": 0, "redeemed_by": None}
    return {"events": history.since(since) if history else [], "last_id": history.last_id if history else 0,
            "now": time.time(), "timer": chat.timer() if chat else idle_timer,
            "settings": overlay_settings.snapshot() if overlay_settings else {}}


def _overlay_settings(request):
    if overlay_settings is None:
        raise ChatUnavailable("overlay settings are not running")
    return overlay_settings.update(request["group"], request["values"])


def _overlay_reset(request):
    if overlay_settings is None:
        raise ChatUnavailable("overlay settings are not running")
    return overlay_settings.reset_layout()


def _twitch_event(request):
    _need_chat()
    return chat.handle_event(request)


def macro_state():
    if macros is None or macro_player is None:
        raise ChatUnavailable("macros are not running")
    return {"macros": macros.listing(), **macro_player.state(), "save_error": macros.save_error}


def _macro_save(request):
    macro_state()
    macros.put(request["name"], request["macro"])
    return macro_state()


def _macro_delete(request):
    macro_state()
    macros.delete(request["name"])
    return macro_state()


def _macro_play(request):
    macro_state()
    macro = macros.get(request["name"]) if isinstance(request["name"], str) else None
    if macro is None:
        raise ValueError("no such macro")
    return {"started": macro_player.play(request["name"], macro), **macro_state()}


def _macro_speed(request):
    macro_state()
    macros.set_speed(request["name"], request["speed"])
    return macro_state()


def _macro_seek(request):
    macro_state()
    position = request["position"]
    if isinstance(position, bool) or not isinstance(position, (int, float)) or not 0 <= position <= 3600:
        raise ValueError("position must be a number of seconds")
    if not macro_player.seek(position):
        raise ValueError("nothing is playing")
    return macro_state()


def _macro_stop(request):
    macro_state()
    macro_player.stop()
    return macro_state()


def _twitch_connect(request):
    _need_chat()
    twitch.connect()
    return twitch_state()


def _twitch_disconnect(request):
    _need_chat()
    twitch.disconnect()
    return twitch_state()


# path -> handler(request) returning None (204) or a JSON-able reply; ValueError, KeyError and TypeError mean 400.
ROUTES = {"/press": _press, "/stick": _stick, "/twitch/settings": _twitch_settings, "/twitch/test": _twitch_test,
          "/twitch/redeem": _twitch_redeem, "/twitch/event": _twitch_event,
          "/twitch/connect": _twitch_connect, "/twitch/disconnect": _twitch_disconnect,
          "/twitch/rewards/create": _reward_create,
          "/twitch/rewards/update": _reward_update, "/twitch/rewards/delete": _reward_delete,
          "/overlay/settings": _overlay_settings, "/overlay/reset-layout": _overlay_reset,
          "/macro/save": _macro_save, "/macro/delete": _macro_delete, "/macro/play": _macro_play, "/macro/stop": _macro_stop,
          "/macro/speed": _macro_speed, "/macro/seek": _macro_seek}


class Handler(BaseHTTPRequestHandler):
    def _reply(self, code, body=b"", kind="application/json"):
        self.send_response(code)
        if body:
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
        else:
            self.send_header("Content-Length", "0")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code, payload):
        self._reply(code, json.dumps(payload).encode())

    def do_GET(self):
        url = urlsplit(self.path)
        if url.path == "/state":
            with lock:
                payload = {**state, "history": history.recent(8) if history else [],
                           "overlays": overlay_settings.snapshot() if overlay_settings else {}}
            self._reply(200, json.dumps(payload).encode())
        elif url.path == "/overlay/settings":
            self._json(200, overlay_settings.snapshot() if overlay_settings else {})
        elif url.path == "/overlay/data":
            try:
                since = int(parse_qs(url.query).get("since", ["0"])[0])
            except ValueError:
                since = 0
            self._json(200, overlay_data(since))
        elif url.path == "/twitch/state":
            try:
                self._json(200, twitch_state())
            except ChatUnavailable as err:
                self._json(503, {"error": str(err)})
        elif url.path == "/macro/state":
            try:
                self._json(200, macro_state())
            except ChatUnavailable as err:
                self._json(503, {"error": str(err)})
        elif url.path == "/twitch/rewards":
            try:
                _need_chat()
                self._json(200, twitch.list_rewards())
            except ChatUnavailable as err:
                self._json(503, {"error": str(err)})
            except ValueError as err:
                self._json(400, {"error": str(err)})
        elif url.path in PAGES:
            html = PAGES[url.path].read_text(encoding="utf-8")
            for marker, fragment in FRAGMENTS.items():
                if marker in html:
                    html = html.replace(marker, fragment.read_text(encoding="utf-8"))
            self._reply(200, html.encode(), "text/html; charset=utf-8")
        else:
            self.send_error(404)

    def do_POST(self):
        port = self.server.server_address[1]
        hosts = {f"localhost:{port}", f"127.0.0.1:{port}"}
        origin = self.headers.get("Origin")
        # The JSON content type forces a CORS preflight, and the origin/host checks stop other web pages and DNS rebinding.
        if (self.path not in ROUTES or self.headers.get("Host") not in hosts
                or self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json"
                or (origin is not None and origin not in {f"http://{h}" for h in hosts})):
            self.send_error(403)
            return
        try:
            length = int(self.headers.get("Content-Length", ""))
            if not 0 < length <= MAX_BODY_BY_ROUTE.get(self.path, MAX_BODY):
                raise ValueError("bad length")
            request = json.loads(self.rfile.read(length))
            if not isinstance(request, dict):
                raise ValueError("request must be an object")
            result = ROUTES[self.path](request)
        except ChatUnavailable as err:
            self._json(503, {"error": str(err)})
            return
        except (ValueError, KeyError, TypeError) as err:
            self._json(400, {"error": str(err)})
            return
        if result is None:
            self._reply(204)
        else:
            self._json(200, result)

    def log_message(self, *args):
        pass


def serve(port=8000):
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()