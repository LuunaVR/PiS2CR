"""Twitch chat control: turn chat messages into controller commands and run them as timed holds.

Plain Python with no hardware or network access, so it can be tested on its own. The relay passes an
on_change callback that receives (buttons, sticks) whenever the set of held inputs changes.
"""
import copy
import json
import math
import os
import re
import sys
import tempfile
import threading
import time
import unicodedata
from collections import deque, namedtuple
from pathlib import Path

MAX_HOLD_S = 3.0  # default for the longest-hold setting
MAX_SETTING_S = 99  # the most a chat message can ask for is 99.99 s
MIN_HOLD_S = 0.05
MAX_MESSAGE_CHARS = 40
MAX_SEQUENCE_CHARS = 500  # Twitch's own limit for a message
CHAIN_GAP_S = 0.05  # release between two commands of one message that use the same input, so "a a" is two presses
MAX_REDEEMS = 12
MIN_REDEEM_S = 10
MAX_REDEEM_S = 3600
LOG_SIZE = 40
SETTINGS_PATH = Path.home() / ".config" / "pis2cr" / "chat.json"

# name -> (action, aliases). An action is ("button", Switch button name) or ("stick", side, x, y) with y up.
# Short names go to what chat uses most: walking (l and r). D-pad and bumpers get a d / b prefix so left and right stay free.
GROUPS = [
    {"id": "move", "label": "Move", "default": True, "restricted": False,
     "note": "Left stick directions. Two at once make a diagonal.",
     "commands": {"forward": (("stick", "left", 0, 1), ("f", "fwd", "forwards")),
                  "back": (("stick", "left", 0, -1), ("bk", "backward", "backwards")),
                  "left": (("stick", "left", -1, 0), ("l",)),
                  "right": (("stick", "left", 1, 0), ("r",))}},
    {"id": "look", "label": "Look", "default": True, "restricted": False,
     "note": "Right stick directions. Two at once make a diagonal.",
     "commands": {"lookup": (("stick", "right", 0, 1), ("lu",)),
                  "lookdown": (("stick", "right", 0, -1), ("ld",)),
                  "lookleft": (("stick", "right", -1, 0), ("ll",)),
                  "lookright": (("stick", "right", 1, 0), ("lr",))}},
    {"id": "face", "label": "Face Buttons", "default": True, "restricted": False,
     "note": "The four right-hand buttons.",
     "commands": {"a": (("button", "A"), ()), "b": (("button", "B"), ()),
                  "x": (("button", "X"), ()), "y": (("button", "Y"), ())}},
    {"id": "dpad", "label": "D-Pad", "default": True, "restricted": False,
     "note": "The four directional buttons.",
     "commands": {"dpadup": (("button", "UP"), ("dup", "du")), "dpaddown": (("button", "DOWN"), ("ddown", "dd")),
                  "dpadleft": (("button", "LEFT"), ("dleft", "dl")), "dpadright": (("button", "RIGHT"), ("dright", "dr"))}},
    {"id": "shoulders", "label": "Bumpers and Triggers", "default": True, "restricted": False,
     "note": "LB, RB, ZL and ZR. None of these can open a system menu.",
     "commands": {"leftbumper": (("button", "L"), ("lb",)), "rightbumper": (("button", "R"), ("rb",)),
                  "zl": (("button", "ZL"), ("lt", "lefttrigger")), "zr": (("button", "ZR"), ("rt", "righttrigger"))}},
    {"id": "clicks", "label": "Stick Clicks", "default": True, "restricted": False,
     "note": "Pressing each stick in.",
     "commands": {"ls": (("button", "LS"), ()), "rs": (("button", "RS"), ())}},
    {"id": "menu", "label": "Others", "default": False, "restricted": True,
     "note": "Can open the Home menu, pause or leave the game, or take screenshots.",
     "commands": {"plus": (("button", "PLUS"), ("start",)), "minus": (("button", "MINUS"), ("select",)),
                  "home": (("button", "HOME"), ()), "capture": (("button", "CAPTURE"), ())}},
]
MODES = ("off", "redeem", "open")
# everyone; vips = VIPs and the broadcaster plus the listed users; users = the broadcaster plus the listed users only.
WHO = ("everyone", "vips", "users")
MAX_ALLOWED_USERS = 100

ALIASES = {alias: name for group in GROUPS for name, (_, aliases) in group["commands"].items() for alias in aliases}
COMMANDS = {name: (group["id"], action) for group in GROUPS for name, (action, _) in group["commands"].items()}
COMMANDS.update({alias: COMMANDS[name] for alias, name in ALIASES.items()})
assert len(COMMANDS) == len({n for g in GROUPS for n in g["commands"]}) + len(ALIASES), "duplicate command name"
RESTRICTED_GROUPS = frozenset(group["id"] for group in GROUPS if group["restricted"])
COMMAND_NAMES = tuple(name for group in GROUPS for name in group["commands"])
MAX_CUSTOM_ALIASES = 60
MAX_MACRO_TRIGGERS = 20

_NUMBER = re.compile(r"(?:[0-9]{1,6}(?:\.[0-9]{1,2})?|\.[0-9]{1,2})")
Command = namedtuple("Command", "name group action seconds")
# known: worth showing in the activity log (a deliberate command attempt, not ordinary chatter).
Parsed = namedtuple("Parsed", "command reason known")
Sequence = namedtuple("Sequence", "commands reason known")


def default_settings():
    return {
        "mode": "off",
        "who": "everyone",
        "allowed_users": [],
        "multi_commands": False,
        "max_commands": 12,
        "custom_aliases": {},
        "disabled_commands": [],
        "macro_chat": False,
        "macro_triggers": {},
        "macro_cooldown_seconds": 120.0,
        "groups": {group["id"]: group["default"] for group in GROUPS},
        "tap_seconds": 0.5,
        "max_hold_seconds": MAX_HOLD_S,
        "user_cooldown_seconds": 0.5,
        "max_active": 6,
        "redeems": [],
    }


def _number(value, name, low, high):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not low <= value <= high:
        raise ValueError(f"{name} must be a number from {low:g} to {high:g}")
    return float(value)


def normalize_settings(patch, base):
    """Return base updated with the valid fields of patch; raise ValueError on anything invalid."""
    if not isinstance(patch, dict):
        raise ValueError("settings must be an object")
    out = copy.deepcopy(base)
    if "mode" in patch:
        if patch["mode"] not in MODES:
            raise ValueError("mode must be off, redeem or open")
        out["mode"] = patch["mode"]
    if "who" in patch:
        if patch["who"] not in WHO:
            raise ValueError("who must be everyone, vips or users")
        out["who"] = patch["who"]
    if "allowed_users" in patch:
        names = patch["allowed_users"]
        if not isinstance(names, list) or len(names) > MAX_ALLOWED_USERS:
            raise ValueError(f"at most {MAX_ALLOWED_USERS} users")
        clean = []
        for name in names:
            name = name.strip().lstrip("@").lower() if isinstance(name, str) else ""
            if not re.fullmatch(r"[a-z0-9_]{1,25}", name):
                raise ValueError("user names use letters, numbers and underscores, up to 25 characters")
            if name not in clean:
                clean.append(name)
        out["allowed_users"] = clean
    if "multi_commands" in patch:
        if not isinstance(patch["multi_commands"], bool):
            raise ValueError("multi_commands must be true or false")
        out["multi_commands"] = patch["multi_commands"]
    if "custom_aliases" in patch:
        names = patch["custom_aliases"]
        if not isinstance(names, dict) or len(names) > MAX_CUSTOM_ALIASES:
            raise ValueError(f"at most {MAX_CUSTOM_ALIASES} custom names")
        clean = {}
        for alias, target in names.items():
            alias = alias.strip().lower() if isinstance(alias, str) else ""
            if not re.fullmatch(r"[a-z]{1,20}", alias):
                raise ValueError("custom names use letters only, up to 20 of them")
            if alias in COMMANDS or alias in out["macro_triggers"]:
                raise ValueError(f"{alias} is already a command")
            target = ALIASES.get(target, target) if isinstance(target, str) else None
            if target not in COMMAND_NAMES:
                raise ValueError(f"{alias} must point at a command")
            if alias in clean:
                raise ValueError(f"{alias} is listed twice")
            clean[alias] = target
        out["custom_aliases"] = clean
    if "macro_chat" in patch:
        if not isinstance(patch["macro_chat"], bool):
            raise ValueError("macro_chat must be true or false")
        out["macro_chat"] = patch["macro_chat"]
    if "macro_triggers" in patch:
        triggers = patch["macro_triggers"]
        if not isinstance(triggers, dict) or len(triggers) > MAX_MACRO_TRIGGERS:
            raise ValueError(f"at most {MAX_MACRO_TRIGGERS} macro words")
        clean = {}
        for word, macro in triggers.items():
            word = word.strip().lower() if isinstance(word, str) else ""
            if not re.fullmatch(r"[a-z]{1,20}", word):
                raise ValueError("macro words use letters only, up to 20 of them")
            if word in COMMANDS or word in out["custom_aliases"]:
                raise ValueError(f"{word} is already a command")
            if not isinstance(macro, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", macro):
                raise ValueError(f"{word} must point at a macro name")
            if word in clean:
                raise ValueError(f"{word} is listed twice")
            clean[word] = macro
        out["macro_triggers"] = clean
    if "disabled_commands" in patch:
        names = patch["disabled_commands"]
        if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
            raise ValueError("disabled_commands must be a list of command names")
        clean = []
        for name in names:
            name = ALIASES.get(name.strip().lower(), name.strip().lower())
            if name not in COMMAND_NAMES:
                raise ValueError(f"{name!r} is not a command")
            if name not in clean:
                clean.append(name)
        out["disabled_commands"] = clean
    if "groups" in patch:
        if not isinstance(patch["groups"], dict):
            raise ValueError("groups must be an object")
        for gid, enabled in patch["groups"].items():
            if gid not in out["groups"] or not isinstance(enabled, bool):
                raise ValueError(f"bad group setting: {gid}")
            out["groups"][gid] = enabled
    ranges = {"tap_seconds": (0.1, MAX_SETTING_S), "max_hold_seconds": (0.5, MAX_SETTING_S),
              "user_cooldown_seconds": (0.0, 3600.0), "max_active": (1, 100), "max_commands": (2, 30),
              "macro_cooldown_seconds": (0.0, 3600.0)}
    for key, (low, high) in ranges.items():
        if key in patch:
            value = _number(patch[key], key, low, high)
            out[key] = int(value) if key in ("max_active", "max_commands") else round(value, 2)
    if "redeems" in patch:
        items = patch["redeems"]
        if not isinstance(items, list) or len(items) > MAX_REDEEMS:
            raise ValueError(f"at most {MAX_REDEEMS} rewards")
        seen, clean = set(), []
        for item in items:
            rid = item.get("id") if isinstance(item, dict) else None
            if not isinstance(rid, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", rid):
                raise ValueError("bad reward id")
            if rid in seen:
                raise ValueError("a reward is listed twice")
            seen.add(rid)
            seconds = _number(item.get("seconds"), "duration", MIN_REDEEM_S, MAX_REDEEM_S)
            clean.append({"id": rid, "seconds": int(round(seconds))})
        out["redeems"] = clean
    return out


def _clean(text):
    # Twitch appends an invisible character (U+E0000) to repeated messages; also drop other format/unassigned marks.
    text = "".join(ch for ch in str(text) if unicodedata.category(ch) not in ("Cf", "Cn", "Co"))
    # A time may follow the name directly: "y5" means "y 5".
    return re.sub(r"(?<=[a-z])(?=[0-9.])", " ", text.strip().lower())


def _resolve(word, custom):
    """The command name a word stands for: a game-specific custom name, a built-in short form, or the word itself."""
    return (custom or {}).get(word) or ALIASES.get(word, word)


def parse_message(text, max_hold=MAX_HOLD_S, custom=None):
    """One command per message: `name` or `name seconds`. Anything else is rejected whole."""
    text = _clean(text)
    if not text:
        return Parsed(None, "empty message", False)
    words = text.split()
    name = _resolve(words[0], custom)
    if name not in COMMANDS:
        return Parsed(None, "not a command", False)
    if len(words) > 2 or len(text) > MAX_MESSAGE_CHARS:
        return Parsed(None, "only one command per message", False)
    seconds = None
    if len(words) == 2:
        if not _NUMBER.fullmatch(words[1]):
            return Parsed(None, "the time must be a plain number like 2 or 1.5", True)
        seconds = float(words[1])
        if seconds < MIN_HOLD_S:
            return Parsed(None, f"shortest hold is {MIN_HOLD_S:g} s", True)
        if seconds > max_hold:
            seconds = max_hold  # a longer ask still runs, for the longest hold
    group, action = COMMANDS[name]
    return Parsed(Command(name, group, action, seconds), None, True)


def parse_sequence(text, max_hold=MAX_HOLD_S, max_commands=12, custom=None):
    """Several commands in one message, each `name` or `name seconds`. Every word must belong to a command, or the
    whole message is ignored (so ordinary chatter that happens to contain "a" does nothing)."""
    text = _clean(text)
    words = text.split()
    if not words or _resolve(words[0], custom) not in COMMANDS or len(text) > MAX_SEQUENCE_CHARS:
        return Sequence([], "not a command", False)
    commands, index = [], 0
    while index < len(words):
        name = _resolve(words[index], custom)
        if name not in COMMANDS:
            return Sequence([], "not a command", False)
        index += 1
        seconds = None
        if index < len(words) and _NUMBER.fullmatch(words[index]):
            seconds = float(words[index])
            index += 1
            if seconds < MIN_HOLD_S:
                return Sequence([], f"shortest hold is {MIN_HOLD_S:g} s", True)
            seconds = min(seconds, max_hold)
        group, action = COMMANDS[name]
        commands.append(Command(name, group, action, seconds))
    if len(commands) > max_commands:
        return Sequence([], f"at most {max_commands} commands in one message", True)
    return Sequence(commands, None, True)


def _user_key(user):
    user = "".join(ch for ch in str(user) if ch.isprintable()).strip()[:40]
    return user or "unknown"


class ChatController:
    """Settings, access rules and the timed holds started by chat commands and redeems."""

    def __init__(self, on_change, path=SETTINGS_PATH, clock=time.monotonic, wall=time.time, on_accept=None):
        self._on_change = on_change
        self._on_accept = on_accept
        self._path = Path(path) if path else None
        self._clock = clock
        self._wall = wall
        self._lock = threading.RLock()
        self._settings = default_settings()
        self._holds = []
        self._paused = False
        self._on_macro = None
        self._stop_macro = None
        self._last_macro = -1e9
        self._next_id = 1
        self._last_accept = {}
        self._redeem_until = None
        self._redeem_total = 0.0
        self._redeem_by = None
        self._log = deque(maxlen=LOG_SIZE)
        self._stats = {"accepted": 0, "ignored": 0}
        self._emitted = (frozenset(), {})
        self.save_error = None
        self._load()

    # -- persistence ---------------------------------------------------------------------------------------------

    def _load(self):
        if self._path is None or not self._path.exists():
            return
        try:
            self._settings = normalize_settings(json.loads(self._path.read_text(encoding="utf-8")), default_settings())
        except (OSError, ValueError) as err:
            self._settings = default_settings()
            try:   # keep the file for inspection; the next save would overwrite it
                self._path.replace(self._path.with_name(self._path.name + ".bad"))
            except OSError:
                pass
            print(f"chat settings could not be read ({err}); using defaults", file=sys.stderr, flush=True)
        self._apply_safe_start()

    def _apply_safe_start(self):
        # A reboot must never come back with open chat control or menu buttons enabled.
        for gid in RESTRICTED_GROUPS:
            self._settings["groups"][gid] = False
        if self._settings["mode"] == "open":
            self._settings["mode"] = "redeem"

    def _save(self):
        if self._path is None:
            return
        data = copy.deepcopy(self._settings)
        for gid in RESTRICTED_GROUPS:
            data["groups"][gid] = False
        if data["mode"] == "open":
            data["mode"] = "redeem"
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=self._path.parent, prefix=".chat-", suffix=".json")
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(data, handle, indent=2)
            os.replace(tmp, self._path)
            self.save_error = None
        except OSError as err:
            self.save_error = str(err)

    # -- output --------------------------------------------------------------------------------------------------

    def _output(self):
        buttons, sums = set(), {"left": [0.0, 0.0], "right": [0.0, 0.0]}
        now = self._clock()
        for hold in self._holds:
            # A message with several commands holds only the one whose turn it is.
            actions = ([hold["action"]] if "steps" not in hold
                       else [step["action"] for step in hold["steps"] if step["start"] <= now < step["end"]])
            for action in actions:
                if action[0] == "button":
                    buttons.add(action[1])
                else:
                    sums[action[1]][0] += action[2]
                    sums[action[1]][1] += action[3]
        sticks = {}
        for side, (x, y) in sums.items():
            length = math.hypot(x, y)
            if length > 1:
                x, y = x / length, y / length
            if abs(x) > 1e-9 or abs(y) > 1e-9:
                sticks[side] = (round(x, 3), round(y, 3))
        return frozenset(buttons), sticks

    def _changed_output(self):
        output = self._output()
        if output == self._emitted:
            return None
        self._emitted = output
        return output

    def _fire(self, change):
        if change is not None:
            self._on_change(*change)

    # -- state ---------------------------------------------------------------------------------------------------

    def _redeem_remaining(self, now):
        return max(0.0, self._redeem_until - now) if self._redeem_until is not None else 0.0

    def _accepting(self, now):
        mode = self._settings["mode"]
        return mode == "open" or (mode == "redeem" and self._redeem_remaining(now) > 0)

    def _viewer_allowed(self, user, login, badges):
        settings = self._settings
        if settings["who"] == "everyone" or "broadcaster" in badges:
            return True
        if {user.lower(), (login or "").lower()} & set(settings["allowed_users"]):
            return True
        return settings["who"] == "vips" and "vip" in badges

    def _note(self, user, text, ok, reason):
        self._log.append({"t": round(self._wall(), 1), "user": user, "text": str(text)[:60], "ok": ok, "reason": reason})

    def _drop_blocked(self):
        """Stop everything that is now turned off (a group or a single command), including the later commands of a message."""
        kept = []
        for hold in self._holds:
            if "steps" not in hold:
                if self._command_on(hold["group"], hold["name"]):
                    kept.append(hold)
                continue
            hold["steps"] = [step for step in hold["steps"] if self._command_on(step["group"], step["name"])]
            if hold["steps"]:
                hold["until"] = hold["steps"][-1]["end"]
                kept.append(hold)
        self._holds = kept

    def _command_on(self, group, name):
        settings = self._settings
        return settings["groups"][group] and name not in settings["disabled_commands"]

    def _prune(self, now):
        self._holds = [hold for hold in self._holds if hold["until"] > now]
        if self._redeem_until is not None and self._redeem_until <= now:
            self._redeem_until = None

    # -- chat ----------------------------------------------------------------------------------------------------

    def handle_message(self, user, text, source="chat", run=True, login=None, badges=()):
        """Process one chat message. source "test" is the streamer's own console: it skips the mode and who checks.

        login is the Twitch login name and badges the sender's badge names ("broadcaster", "vip", ...), when known."""
        user = _user_key(user)
        with self._lock:
            now = self._clock()
            self._prune(now)
            settings = self._settings
            testing = source == "test"
            custom = settings["custom_aliases"]
            if settings["multi_commands"]:
                sequence = parse_sequence(text, settings["max_hold_seconds"], settings["max_commands"], custom)
                commands, parse_reason, known = sequence.commands, sequence.reason, sequence.known
            else:
                parsed = parse_message(text, settings["max_hold_seconds"], custom)
                commands, parse_reason, known = [parsed.command] if parsed.command else [], parsed.reason, parsed.known

            def reject(reason, name, logged):
                if run:  # a dry-run check leaves no trace
                    self._stats["ignored"] += 1
                    if logged:
                        self._note(user, text, False, reason)
                return {"ok": False, "reason": reason, "command": name, "seconds": None}

            triggers = settings["macro_triggers"]
            word = next((w for w in re.findall(r"[a-z]+", _clean(text)) if w in triggers), None)
            macro = triggers.get(word)
            if macro is not None and self._on_macro is not None:
                if not testing:
                    if not settings["macro_chat"]:
                        return reject("macros from chat are turned off", None, False)
                    if self._paused:
                        return reject("a performance is running", None, False)
                    if not self._accepting(now):
                        return reject("chat control is not open right now", None, False)
                    if not self._viewer_allowed(user, login, badges):
                        return reject("not allowed to send commands", None, False)
                    if now - self._last_macro < settings["macro_cooldown_seconds"]:
                        return reject("that is on cooldown", None, False)
                if not run:
                    return {"ok": True, "reason": f"would run the macro {macro}", "command": word, "seconds": None}
                result = self._on_macro(macro, user)
                if not result.get("ok"):
                    return reject(result.get("reason", "the macro could not start"), word, True)
                self._last_macro = now
                self._stats["accepted"] += 1
                self._note(user, text, True, f"macro {macro}")
                if self._on_accept is not None:
                    self._on_accept(user, word)
                return {"ok": True, "reason": f"macro {macro}", "command": word, "seconds": result.get("seconds")}
            if not known:
                return reject(parse_reason, None, testing)
            if not testing and self._paused:
                return reject("a performance is running", None, False)
            if not testing and not self._accepting(now):
                return reject("chat control is not open right now", None, False)
            if not testing and not self._viewer_allowed(user, login, badges):
                return reject("not allowed to send commands", None, False)
            name = _resolve(_clean(text).split()[0], custom)
            if not commands:
                return reject(parse_reason, name, True)
            # A command that is turned off is skipped without any wait, but the message still counts as valid.
            runnable = [c for c in commands if self._command_on(c.group, c.name)]
            skipped = [c.name for c in commands if not self._command_on(c.group, c.name)]
            if not runnable:
                first = commands[0]
                restricted = first.group in RESTRICTED_GROUPS and not settings["groups"][first.group]
                return reject("restricted" if restricted else f"{first.name} is turned off", name, True)
            reason = None
            if not testing and now - self._last_accept.get(user.lower(), -1e9) < settings["user_cooldown_seconds"]:
                reason = "slow down"
            elif len(runnable) > 1 and any(h["user"].lower() == user.lower() and "steps" in h for h in self._holds):
                reason = "your last commands are still running"
            elif len(self._holds) >= settings["max_active"]:
                reason = "too many commands running"
            if reason:
                return reject(reason, name, True)
            seconds_each = [c.seconds if c.seconds is not None else settings["tap_seconds"] for c in runnable]
            if len(runnable) == 1:
                command, seconds = runnable[0], seconds_each[0]
                label, result_command, hold = f"{command.name} {seconds:g}s", command.name, {
                    "id": self._next_id, "user": user, "name": command.name, "group": command.group,
                    "action": command.action, "until": now + seconds, "seconds": seconds}
            else:
                steps, at = [], now
                for index, (command, length) in enumerate(zip(runnable, seconds_each)):
                    steps.append({"start": at, "end": at + length, "group": command.group, "name": command.name,
                                  "action": command.action})
                    # Different inputs follow each other at once; only the same input needs a release in between.
                    same_next = index + 1 < len(runnable) and runnable[index + 1].action == command.action
                    at += length + (CHAIN_GAP_S if same_next else 0)
                seconds = round(steps[-1]["end"] - now, 2)
                result_command = " ".join(c.name for c in runnable)
                if len(result_command) > 80:
                    result_command = result_command[:79] + "\u2026"
                label = f"{len(runnable)} commands, {seconds:g}s"
                hold = {"id": self._next_id, "user": user, "name": result_command, "group": None, "steps": steps,
                        "until": steps[-1]["end"], "seconds": seconds}
            if skipped:
                label += f" (skipped {', '.join(skipped)})"
            if run:
                self._holds.append(hold)
                self._next_id += 1
                self._last_accept[user.lower()] = now
                self._stats["accepted"] += 1
                self._note(user, text, True, label)
            change = self._changed_output()
        self._fire(change)
        if run and self._on_accept is not None:
            shown = " ".join(c.name + (f" {c.seconds:g}s" if c.seconds is not None else "") for c in runnable)
            self._on_accept(user, shown if len(shown) <= 50 else shown[:49] + "\u2026")
        return {"ok": True, "reason": label if run else f"would run {label}", "command": result_command,
                "seconds": seconds}

    def redeem(self, user, reward_id, source="twitch", redemption_id=None):
        """A channel point redeem opens chat control for that reward's duration.

        Only a redeem that gives more time than is left is accepted (status FULFILLED) and it resets the timer to its
        own duration. Anything that would change nothing is denied with status CANCELED so the points can be refunded.
        A reward that is not set up for chat control gets status None: leave it alone.
        """
        user = _user_key(user)
        with self._lock:
            now = self._clock()
            self._prune(now)
            settings = self._settings
            config = next((r for r in settings["redeems"] if r["id"] == reward_id), None)
            result = {"ok": False, "remaining_s": round(self._redeem_remaining(now), 1), "status": "CANCELED",
                      "redemption_id": redemption_id}
            if config is None:
                return {**result, "reason": "not a chat control reward", "status": None}
            if settings["mode"] == "off":
                return {**result, "reason": "chat control is off"}
            if settings["mode"] == "open":
                return {**result, "reason": "chat control is already open to everyone"}
            if config["seconds"] <= self._redeem_remaining(now):
                left = self._redeem_remaining(now)
                self._note(user, "redeem", False, f"denied, {int(left // 60)}:{int(left % 60):02d} is already left")
                return {**result, "reason": "more time is already left"}
            self._redeem_until = now + config["seconds"]
            self._redeem_total = float(config["seconds"])
            self._redeem_by = user
            self._note(user, "redeem", True, f"chat control for {config['seconds']}s")
        return {**result, "ok": True, "reason": "chat control started", "status": "FULFILLED",
                "remaining_s": float(config["seconds"])}

    def handle_event(self, event, source="bot"):
        """Entry point for events pushed by the Twitch side.

        {"type": "message", "user", "text"} or {"type": "redeem", "user", "reward_id", "redemption_id" (optional)}.
        A redeem reply carries "status": FULFILLED, CANCELED (refund it) or null (not ours).
        """
        if not isinstance(event, dict):
            raise ValueError("event must be an object")
        kind = event.get("type")
        if kind == "message":
            if not isinstance(event.get("user"), str) or not isinstance(event.get("text"), str):
                raise ValueError("message needs user and text")
            badges, login = event.get("badges") or [], event.get("login")
            if (not isinstance(badges, list) or not all(isinstance(b, str) for b in badges)
                    or not (login is None or isinstance(login, str))):
                raise ValueError("badges must be a list of names and login a string")
            return self.handle_message(event["user"], event["text"], source=source, login=login, badges=frozenset(badges))
        if kind == "redeem":
            redemption = event.get("redemption_id")
            if (not isinstance(event.get("user"), str) or not isinstance(event.get("reward_id"), str)
                    or not (redemption is None or isinstance(redemption, str))):
                raise ValueError("redeem needs user and reward_id")
            return self.redeem(event["user"], event["reward_id"], source=source, redemption_id=redemption)
        raise ValueError("unknown event type")

    # -- settings and control ------------------------------------------------------------------------------------

    def update_settings(self, patch, confirm_restricted=False):
        with self._lock:
            new = normalize_settings(patch, self._settings)
            for gid in RESTRICTED_GROUPS:
                if new["groups"][gid] and not self._settings["groups"][gid] and confirm_restricted is not True:
                    raise ValueError("turning on the Others buttons needs confirmation")
            turned_off = new["mode"] == "off" and self._settings["mode"] != "off"
            self._settings = new
            if turned_off and self._stop_macro is not None:
                self._stop_macro()
            if new["mode"] == "off":
                self._holds.clear()
                self._redeem_until = None
            else:
                self._drop_blocked()
            self._save()
            change = self._changed_output()
        self._fire(change)

    def set_macro_runner(self, run, stop):
        """run(name, user) starts a macro and returns {"ok": bool, "reason": str, "seconds": float}; stop() ends it."""
        self._on_macro, self._stop_macro = run, stop

    def pause(self, paused):
        """A macro owns the controller: drop whatever chat is holding and ignore commands until it ends."""
        with self._lock:
            self._paused = bool(paused)
            if self._paused:
                self._holds.clear()
            change = self._changed_output()
        self._fire(change)

    def tick(self):
        with self._lock:
            now = self._clock()
            self._prune(now)
            change = self._changed_output()
        self._fire(change)

    def start(self):
        def loop():
            while True:
                time.sleep(0.01)
                self.tick()
        threading.Thread(target=loop, daemon=True).start()

    def reward_ids(self):
        with self._lock:
            return [r["id"] for r in self._settings["redeems"]]

    def timer(self):
        """What the timer overlay shows: only a redeem window has a time limit."""
        with self._lock:
            now = self._clock()
            self._prune(now)
            remaining = self._redeem_remaining(now) if self._settings["mode"] == "redeem" else 0.0
            return {"active": remaining > 0, "remaining_s": round(remaining, 1), "total_s": self._redeem_total,
                    "redeemed_by": self._redeem_by if remaining > 0 else None}

    def snapshot(self):
        with self._lock:
            now = self._clock()
            self._prune(now)
            return {
                "settings": copy.deepcopy(self._settings),
                "groups": [{"id": g["id"], "label": g["label"], "note": g["note"], "restricted": g["restricted"],
                            "commands": [{"name": name, "aliases": list(aliases)}
                                         for name, (_, aliases) in g["commands"].items()]} for g in GROUPS],
                "accepting": self._accepting(now),
                "redeem_remaining_s": round(self._redeem_remaining(now), 1),
                "redeem_total_s": self._redeem_total,
                "holds": [{"id": h["id"], "user": h["user"], "command": h["name"],
                           "remaining_s": round(max(0.0, h["until"] - now), 2), "seconds": h["seconds"]}
                          for h in self._holds],
                "log": list(reversed(self._log)),
                "stats": dict(self._stats),
                "save_error": self.save_error,
            }
