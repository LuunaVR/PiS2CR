"""Timed macros: a list of controller states played back by the relay, with exact timing and no network in between.

A macro step is {"t": seconds, "buttons": [names], "left": [x, y], "right": [x, y]} and means "from time t on, hold exactly
this". Sticks run from -1 to 1 with y up. The last step must release everything. Plain Python, so it can be tested on its own.
"""
import copy
import json
import os
import re
import tempfile
import threading
import time
from pathlib import Path

SETTINGS_PATH = Path.home() / ".config" / "pis2cr" / "macros.json"
NAME = re.compile(r"[a-z][a-z0-9_-]{0,31}")
MAX_STEPS = 4000
MAX_DURATION_S = 600
MAX_MACROS = 40
MIN_SPEED, MAX_SPEED = 0.5, 4.0
MAX_MIN_RELEASE_S = 0.5
MIN_PRESS_S = 0.03   # a note is never shortened below this to make room for a release


def _stick(value, name):
    if (not isinstance(value, list) or len(value) != 2
            or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not -1 <= v <= 1 for v in value)):
        raise ValueError(f"{name} must be [x, y] with each between -1 and 1")
    return [round(float(v), 3) for v in value]


def _speed(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not MIN_SPEED <= value <= MAX_SPEED:
        raise ValueError(f"speed must be from {MIN_SPEED * 100:g}% to {MAX_SPEED * 100:g}%")
    return round(float(value), 3)


def clean_macro(data, allowed_buttons):
    """Validated copy of a macro; raises ValueError with a plain message for anything wrong."""
    if not isinstance(data, dict):
        raise ValueError("a macro must be an object")
    label = data.get("label", "")
    if not isinstance(label, str) or len(label) > 40 or not label.isprintable():
        raise ValueError("the label must be up to 40 characters")
    min_release = data.get("min_release", 0.0)
    if isinstance(min_release, bool) or not isinstance(min_release, (int, float)) or not 0 <= min_release <= MAX_MIN_RELEASE_S:
        raise ValueError(f"min_release must be from 0 to {MAX_MIN_RELEASE_S:g} seconds")
    steps = data.get("steps")
    if not isinstance(steps, list) or not 1 <= len(steps) <= MAX_STEPS:
        raise ValueError(f"a macro needs 1 to {MAX_STEPS} steps")
    clean, last = [], 0.0
    for step in steps:
        if not isinstance(step, dict):
            raise ValueError("every step must be an object")
        t = step.get("t")
        if isinstance(t, bool) or not isinstance(t, (int, float)) or not last <= t <= MAX_DURATION_S:
            raise ValueError(f"step times must go up and stay within {MAX_DURATION_S} s")
        last = float(t)
        buttons = step.get("buttons", [])
        if not isinstance(buttons, list) or len(buttons) > 8 or any(b not in allowed_buttons for b in buttons):
            raise ValueError("buttons must be a short list of known button names")
        item = {"t": round(last, 4), "buttons": sorted(set(buttons))}
        fixed = step.get("fixed", False)
        if not isinstance(fixed, bool):
            raise ValueError("fixed must be true or false")
        if fixed:
            item["fixed"] = True
        for side in ("left", "right"):
            if side in step:
                item[side] = _stick(step[side], side)
        clean.append(item)
    final = clean[-1]
    if final["buttons"] or any(any(v for v in final.get(side, [])) for side in ("left", "right")):
        raise ValueError("the last step must release every button and stick")
    return {"label": label, "duration": clean[-1]["t"], "speed": _speed(data.get("speed", 1.0)),
            "min_release": round(float(min_release), 3), "steps": clean}


def scaled_steps(macro, speed=None):
    """The steps with their waits divided by the speed, except the wait after a step marked "fixed" (a game's own
    animation, like taking an instrument out, does not get faster). When the macro asks for a minimum release, a note that
    would be re-pressed sooner than that is cut short, so fast playback still sounds as two presses (a game ignores a blink)."""
    speed = macro.get("speed", 1.0) if speed is None else speed
    steps, now, previous = [], 0.0, None
    for step in macro["steps"]:
        if previous is None:
            now = step["t"] / speed
        else:
            now += (step["t"] - previous["t"]) / (1.0 if previous.get("fixed") else speed)
        steps.append({**step, "t": round(now, 4)})
        previous = step
    gap = macro.get("min_release", 0.0)
    if gap > 0:
        for k in range(len(steps) - 2):
            held, release, again = steps[k], steps[k + 1], steps[k + 2]
            if release["buttons"] or not set(held["buttons"]) & set(again["buttons"]):
                continue
            wanted = round(again["t"] - gap, 4)
            if wanted < release["t"]:
                release["t"] = min(release["t"], max(wanted, round(held["t"] + MIN_PRESS_S, 4)))
    return steps


def play_time(macro):
    """Seconds the macro takes at its saved speed."""
    return round(scaled_steps(macro)[-1]["t"], 2)


class MacroStore:
    """Named macros kept in a small json file so they survive restarts."""

    def __init__(self, allowed_buttons, path=SETTINGS_PATH):
        self._allowed = frozenset(allowed_buttons)
        self._path = Path(path) if path else None
        self._lock = threading.Lock()
        self._macros = {}
        self.save_error = None
        self._load()

    def _load(self):
        if self._path is None or not self._path.exists():
            return
        try:
            stored = json.loads(self._path.read_text(encoding="utf-8"))
            self._macros = {name: clean_macro(macro, self._allowed) for name, macro in stored.items() if NAME.fullmatch(name)}
        except (OSError, ValueError, AttributeError):
            self._macros = {}

    def _save(self):
        if self._path is None:
            return
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=self._path.parent, prefix=".macros-", suffix=".json")
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(self._macros, handle)
            os.replace(tmp, self._path)
            self.save_error = None
        except OSError as err:
            self.save_error = str(err)

    def put(self, name, macro):
        if not isinstance(name, str) or not NAME.fullmatch(name):
            raise ValueError("a macro name is lowercase letters, numbers, - or _, starting with a letter")
        clean = clean_macro(macro, self._allowed)
        with self._lock:
            if name not in self._macros and len(self._macros) >= MAX_MACROS:
                raise ValueError(f"at most {MAX_MACROS} macros")
            if name in self._macros and "speed" not in macro:   # re-importing keeps the speed you tuned
                clean["speed"] = self._macros[name]["speed"]
            self._macros[name] = clean
            self._save()

    def delete(self, name):
        with self._lock:
            if self._macros.pop(name, None) is None:
                raise ValueError("no such macro")
            self._save()

    def set_speed(self, name, speed):
        speed = _speed(speed)
        with self._lock:
            if name not in self._macros:
                raise ValueError("no such macro")
            self._macros[name]["speed"] = speed
            self._save()

    def get(self, name):
        with self._lock:
            macro = self._macros.get(name)
            return copy.deepcopy(macro) if macro else None

    def listing(self):
        with self._lock:
            return [{"name": name, "label": m["label"], "duration": m["duration"], "steps": len(m["steps"]),
                     "speed": m["speed"], "play_time": play_time(m)}
                    for name, m in sorted(self._macros.items())]


class CancelCombo:
    """True once each time all the named buttons become held together on the streamer's own controller."""

    def __init__(self, names=("L", "R")):
        self._names = frozenset(names)
        self._held = False

    def update(self, pressed):
        held = self._names <= set(pressed)
        fired = held and not self._held
        self._held = held
        return fired


class MacroPlayer:
    """Plays one macro at a time on its own thread. output(buttons, sticks) takes a frozenset and {side: (x, y)}."""

    def __init__(self, output, on_change=None, clock=time.monotonic):
        self._output = output
        self._on_change = on_change or (lambda playing: None)
        self._clock = clock
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._wake = threading.Event()   # set by stop() and seek() to cut a wait short
        self._seek = None
        self._thread = None
        self._name = None
        self._started = 0.0
        self._duration = 0.0

    def play(self, name, macro):
        """Start a macro at its own saved speed; False when another one is still running."""
        steps = scaled_steps(macro)
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return False
            self._stop.clear()
            self._wake.clear()
            self._seek = None
            self._name, self._duration = name, steps[-1]["t"]
            self._started = self._clock()
            self._thread = threading.Thread(target=self._run, args=(steps,), daemon=True)
            self._thread.start()
            return True

    def stop(self):
        self._stop.set()
        self._wake.set()

    def seek(self, position):
        """Jump to a time (seconds into the playing macro); False when nothing is playing."""
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                return False
            self._seek = float(position)
        self._wake.set()
        return True

    def wait(self, timeout=None):
        thread = self._thread
        if thread is not None:
            thread.join(timeout)

    def state(self):
        with self._lock:
            playing = self._thread is not None and self._thread.is_alive()
            return {"playing": self._name if playing else None,
                    "elapsed": round(self._clock() - self._started, 2) if playing else 0.0,
                    "duration": self._duration if playing else 0.0}

    def _run(self, steps):
        self._on_change(True)
        # Leading fixed steps (taking the instrument out) are never skipped into; a jump lands after them.
        first = 0
        while first < len(steps) - 1 and steps[first].get("fixed"):
            first += 1
        try:
            origin, i = self._started, 0
            while i < len(steps):
                step = steps[i]
                remaining = origin + step["t"] - self._clock()
                if remaining > 0:
                    self._wake.wait(remaining)
                self._wake.clear()
                if self._stop.is_set():
                    break
                with self._lock:
                    pending, self._seek = self._seek, None
                    if pending is not None:
                        target = min(max(pending, steps[first]["t"]), steps[-1]["t"])
                        origin = self._started = self._clock() - target
                if pending is not None:
                    i = next(k for k, s in enumerate(steps) if s["t"] >= target)
                    self._output(frozenset(), {})
                    continue
                if origin + step["t"] - self._clock() > 0.002:
                    continue   # woken early by something else
                sticks = {side: tuple(step[side]) for side in ("left", "right") if side in step and any(step[side])}
                self._output(frozenset(step["buttons"]), sticks)
                i += 1
        finally:
            self._output(frozenset(), {})   # whatever happened, let go of everything
            self._on_change(False)
