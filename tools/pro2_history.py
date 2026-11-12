"""Input history shared by the dashboard and the overlays: what the streamer and chat pressed, newest last.

Streamer presses come from the controller (and dashboard clicks); chat commands come from the chat controller.
Plain Python with an injectable clock so it can be tested without hardware.
"""
import math
import threading
import time
from collections import deque

LABELS = {"L": "LB", "R": "RB", "LS": "LS Click", "RS": "RS Click", "UP": "D-Pad \u2191", "DOWN": "D-Pad \u2193",
          "LEFT": "D-Pad \u2190", "RIGHT": "D-Pad \u2192", "PLUS": "+", "MINUS": "-", "CAPTURE": "Capture",
          "HOME": "Home"}
# Shoulders first, then the d-pad, then the face buttons, so a chord reads naturally.
ORDER = ["ZL", "L", "ZR", "R", "UP", "DOWN", "LEFT", "RIGHT", "A", "B", "X", "Y", "LS", "RS", "PLUS", "MINUS",
         "HOME", "CAPTURE", "C", "GL", "GR"]
ARROWS = ["\u2192", "\u2197", "\u2191", "\u2196", "\u2190", "\u2199", "\u2193", "\u2198"]  # east, counter-clockwise


class InputHistory:
    def __init__(self, size=200, wall=time.time):
        self._events = deque(maxlen=size)
        self._wall = wall
        self._lock = threading.Lock()
        self._last_id = 0

    def add(self, source, kind, text, user=None):
        """source is "streamer" or "chat"; kind is "button", "stick" or "command"."""
        with self._lock:
            self._last_id += 1
            self._events.append({"id": self._last_id, "t": round(self._wall(), 2), "source": source, "kind": kind,
                                 "text": text, "user": user})
            return self._last_id

    def add_chat(self, user, text):
        self.add("chat", "command", text, user)

    @property
    def last_id(self):
        with self._lock:
            return self._last_id

    def since(self, after_id=0, limit=50):
        with self._lock:
            events = [e for e in self._events if e["id"] > after_id]
        return events[-limit:]

    def recent(self, count):
        with self._lock:
            return list(self._events)[-count:]


class StreamerTracker:
    """Turns the streamer's raw input into history entries without spamming it.

    A held button makes one entry. Buttons that go down within CHORD_S share an entry ("ZR + A"). A stick makes
    an entry only when it settles in one of eight directions for DWELL_S, and nothing more until it returns to the
    middle or settles in another direction.
    """

    CHORD_S = 0.06
    DWELL_S = 0.25
    STICK_ON = 0.6
    STICK_OFF = 0.35

    def __init__(self, history, clock=time.monotonic):
        self._history = history
        self._clock = clock
        self._real = frozenset()
        self._ui = frozenset()
        self._down = frozenset()
        self._pending = []
        self._pending_since = 0.0
        self._sticks = {side: {"engaged": False, "candidate": None, "since": 0.0, "shown": None}
                        for side in ("left", "right")}

    def update_real(self, buttons, left=None, right=None):
        """Buttons currently held on the real controller and the sticks as (x, y) from -1 to 1 with y up."""
        now = self._clock()
        self._real = frozenset(buttons)
        self._apply(now)
        for side, xy in (("left", left), ("right", right)):
            if xy is not None:
                self._stick(side, xy, now)

    def update_ui(self, buttons):
        """Buttons held through the dashboard, which count as the streamer's."""
        now = self._clock()
        self._ui = frozenset(buttons)
        self._apply(now)

    def flush(self):
        self._flush(self._clock())

    def _apply(self, now):
        union = self._real | self._ui
        fresh = union - self._down
        self._down = union
        self._flush(now)
        if fresh:
            if self._pending and now - self._pending_since <= self.CHORD_S:
                self._pending.extend(fresh)
            else:
                self._flush(now, force=True)
                self._pending, self._pending_since = list(fresh), now

    def _flush(self, now, force=False):
        if self._pending and (force or now - self._pending_since > self.CHORD_S):
            names = sorted(set(self._pending), key=lambda n: ORDER.index(n) if n in ORDER else len(ORDER))
            self._history.add("streamer", "button", " + ".join(LABELS.get(n, n) for n in names))
            self._pending = []

    def _stick(self, side, xy, now):
        x, y = xy
        state = self._sticks[side]
        magnitude = math.hypot(x, y)
        if magnitude < self.STICK_OFF:
            state.update(engaged=False, candidate=None, shown=None)
            return
        if magnitude < self.STICK_ON and not state["engaged"]:
            return
        state["engaged"] = True
        sector = round(math.atan2(y, x) / (math.pi / 4)) % 8
        if sector != state["candidate"]:
            state["candidate"], state["since"] = sector, now
        if sector != state["shown"] and now - state["since"] >= self.DWELL_S:
            state["shown"] = sector
            self._history.add("streamer", "stick", f"{'LS' if side == 'left' else 'RS'} {ARROWS[sector]}")
