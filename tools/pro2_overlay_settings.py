"""Options for the single overlay, kept on the Pi so the dashboard can change them live.

Positions are percentages of a 1920 x 1080 stage, so the layout survives any 16:9 browser source size.
Nothing here is secret. The file only lets options survive a restart.
"""
import copy
import json
import os
import re
import tempfile
import threading
from pathlib import Path

SETTINGS_PATH = Path.home() / ".config" / "pis2cr" / "overlays.json"
WIDGETS = ("controller", "history", "timer")
_HEX = re.compile(r"#[0-9a-fA-F]{6}")


def _box(x, y, w, h):
    return {"visible": ("bool", True), "x": ("number", x, 0, 100), "y": ("number", y, 0, 100),
            "w": ("number", w, 2, 100), "h": ("number", h, 2, 100), "opacity": ("int", 100, 10, 100)}


# group -> option -> ("bool", default) | ("number", default, low, high) | ("int", default, low, high)
#                    | ("choice", default, choices) | ("text", default, max length) | ("color", default)
SPECS = {
    "editor": {"snap": ("number", 1.2, 0, 5)},   # layout editor only: snap distance in % of the stage, 0 means off
    "colors": {"streamer": ("color", "#efa3cb"), "chat": ("color", "#a98bff"), "text": ("color", "#ffffff")},
    "controller": {**_box(2, 64, 26, 34), "streamer": ("bool", True), "chat": ("bool", True), "tint": ("bool", True)},
    "history": {**_box(70, 42, 28, 56), "count": ("int", 6, 1, 20), "align": ("choice", "right", ("left", "center", "right")),
                "newest": ("choice", "bottom", ("top", "bottom")), "streamer": ("bool", True), "chat": ("bool", True),
                "sticks": ("bool", False), "names": ("bool", True), "fade": ("number", 10, 0, 600),
                "backdrop": ("int", 78, 0, 100)},
    "timer": {**_box(36, 2, 28, 14), "label": ("text", "Chat Control", 40), "bar": ("bool", True), "who": ("bool", True),
              "backdrop": ("int", 82, 0, 100)},
}


def defaults():
    return {group: {key: spec[1] for key, spec in options.items()} for group, options in SPECS.items()}


def _check(key, spec, value):
    kind = spec[0]
    if kind == "bool":
        if not isinstance(value, bool):
            raise ValueError(f"{key} must be true or false")
        return value
    if kind in ("number", "int"):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not spec[2] <= value <= spec[3]:
            raise ValueError(f"{key} must be a number from {spec[2]:g} to {spec[3]:g}")
        return int(round(value)) if kind == "int" else round(float(value), 2)
    if kind == "choice":
        if value not in spec[2]:
            raise ValueError(f"{key} must be one of {', '.join(spec[2])}")
        return value
    if kind == "color":
        if not isinstance(value, str) or not _HEX.fullmatch(value):
            raise ValueError(f"{key} must be a colour like #a98bff")
        return value.lower()
    if not isinstance(value, str) or len(value) > spec[2] or not value.isprintable():
        raise ValueError(f"{key} must be up to {spec[2]} characters")
    return value


class OverlaySettings:
    def __init__(self, path=SETTINGS_PATH):
        self._path = Path(path) if path else None
        self._lock = threading.Lock()
        self._values = defaults()
        self.save_error = None
        self._load()

    def _load(self):
        if self._path is None or not self._path.exists():
            return
        try:
            stored = json.loads(self._path.read_text(encoding="utf-8"))
            values = defaults()
            for group, options in SPECS.items():
                for key, spec in options.items():
                    if key in stored.get(group, {}):
                        values[group][key] = _check(key, spec, stored[group][key])
            self._values = values
        except (OSError, ValueError, AttributeError, TypeError):
            self._values = defaults()

    def _save(self):
        if self._path is None:
            return
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=self._path.parent, prefix=".overlays-", suffix=".json")
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(self._values, handle, indent=2)
            os.replace(tmp, self._path)
            self.save_error = None
        except OSError as err:
            self.save_error = str(err)

    def snapshot(self):
        with self._lock:
            return copy.deepcopy(self._values)

    def update(self, group, values):
        """Change some options of one group; raises ValueError for anything unknown or out of range."""
        if group not in SPECS:
            raise ValueError("unknown overlay part")
        if not isinstance(values, dict) or not values:
            raise ValueError("values must be an object")
        clean = {}
        for key, value in values.items():
            if key not in SPECS[group]:
                raise ValueError(f"unknown option: {key}")
            clean[key] = _check(key, SPECS[group][key], value)
        with self._lock:
            self._values[group].update(clean)
            self._save()
            return copy.deepcopy(self._values)

    def reset_layout(self):
        """Put every widget back where it started; colours and other options stay."""
        fresh = defaults()
        with self._lock:
            for widget in WIDGETS:
                for key in ("visible", "x", "y", "w", "h"):
                    self._values[widget][key] = fresh[widget][key]
            self._save()
            return copy.deepcopy(self._values)
