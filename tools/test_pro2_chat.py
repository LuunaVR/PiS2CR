"""Tests for the chat command parser and controller. Run: python -m unittest test_pro2_chat (from tools/)."""
import json
import tempfile
import unittest
from pathlib import Path

import pro2_chat as chat

TWO_MIN = {"id": "r2", "seconds": 120}
ONE_MIN = {"id": "r1", "seconds": 60}
THREE_MIN = {"id": "r3", "seconds": 180}


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def make(path=None, accepted=None, **settings):
    clock = Clock()
    events = []
    controller = chat.ChatController(lambda buttons, sticks: events.append((buttons, sticks)), path=path,
                                     clock=clock, wall=clock,
                                     on_accept=None if accepted is None else lambda user, text: accepted.append((user, text)))
    controller.update_settings({"mode": "open", "tap_seconds": 0.2, "user_cooldown_seconds": 0, "max_active": 8,
                                **settings})
    return controller, clock, events


class ParseTests(unittest.TestCase):
    def ok(self, text, name, seconds=None):
        parsed = chat.parse_message(text)
        self.assertIsNone(parsed.reason, text)
        self.assertEqual((parsed.command.name, parsed.command.seconds), (name, seconds), text)

    def bad(self, text):
        parsed = chat.parse_message(text)
        self.assertIsNone(parsed.command, text)

    def test_plain_commands_are_case_insensitive(self):
        self.ok("left", "left")
        self.ok("  RIGHT  ", "right")
        self.ok("A", "a")
        self.ok("LookUp", "lookup")

    def test_short_names(self):
        for text, name in (("f", "forward"), ("fwd", "forward"), ("bk", "back"), ("dl", "dpadleft"), ("DR", "dpadright"),
                           ("du", "dpadup"), ("dd", "dpaddown"), ("dup", "dpadup"), ("ddown", "dpaddown"),
                           ("lb", "leftbumper"), ("rb", "rightbumper"),
                           ("lt", "zl"), ("rt", "zr"), ("lu", "lookup"), ("ll", "lookleft"), ("start", "plus")):
            self.ok(text, name)

    def test_the_dpad_has_full_names(self):
        for name, button in (("dpadup", "UP"), ("dpaddown", "DOWN"), ("dpadleft", "LEFT"), ("dpadright", "RIGHT")):
            self.ok(name, name)
            self.assertEqual(chat.parse_message(name).command.action, ("button", button))

    def test_durations_with_or_without_the_leading_zero(self):
        self.ok("left 3", "left", 3.0)
        self.ok("left 1.5", "left", 1.5)
        self.ok("left .3", "left", 0.3)
        self.ok("left 0.3", "left", 0.3)
        self.ok("f .5", "forward", 0.5)
        self.ok("left   2", "left", 2.0)

    def test_the_whole_message_is_ignored_when_wrong(self):
        for text in ("left 3 please", "left 3 a y x", "a b", "left right", "please left", "left 0",
                     "left 3s", "left -1", "left 1.555", "left one", "left, 3", "", "   ", "up " * 30, "left 0.04"):
            self.bad(text)

    def test_unknown_words_are_not_commands(self):
        for text in ("hello", "lol", "!left", "jump", "strafeleft", "rl", "5a"):
            self.assertFalse(chat.parse_message(text).known, text)

    def test_up_and_down_are_not_built_in(self):
        for text in ("up", "u", "down", "d"):
            self.assertFalse(chat.parse_message(text).known, text)
        custom = {"up": "forward", "down": "back"}
        self.assertEqual(chat.parse_message("Up2", custom=custom).command.name, "forward")
        self.assertEqual(chat.parse_message("down", custom=custom).command.name, "back")

    def test_the_time_can_follow_the_name_directly(self):
        self.ok("y2", "y", 2.0)
        self.ok("y5", "y", 3.0)   # longer than the longest hold is shortened to it
        self.ok("Left2", "left", 2.0)
        self.ok("lookup.5", "lookup", 0.5)
        self.ok("dl1.5", "dpadleft", 1.5)
        self.ok("f 3", "forward", 3.0)
        for text in ("y5s", "y5 6", "y1.555", "y-1", "y0", "y."):
            self.bad(text)

    def test_l_and_r_walk_and_the_bumpers_are_lb_and_rb(self):
        self.ok("l", "left")
        self.ok("r 2", "right", 2.0)
        self.ok("lb", "leftbumper")
        self.ok("rb", "rightbumper")

    def test_invisible_characters_twitch_adds_are_ignored(self):
        self.ok("left\U000e0000", "left")
        self.ok("left 2 \U000e0000", "left", 2.0)

    def test_every_name_is_unique(self):
        names = [n for g in chat.GROUPS for n in g["commands"]]
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(len(chat.COMMANDS), len(names) + len(chat.ALIASES))


class VocabularyTests(unittest.TestCase):
    def test_no_digits_in_command_names(self):
        for group in chat.GROUPS:
            for name, (_, aliases) in group["commands"].items():
                for word in (name, *aliases):
                    self.assertFalse(any(c.isdigit() for c in word), word)

    def test_group_labels_are_title_case(self):
        self.assertEqual([g["label"] for g in chat.GROUPS],
                         ["Move", "Look", "Face Buttons", "D-Pad", "Bumpers and Triggers", "Stick Clicks", "Others"])


class WhoTests(unittest.TestCase):
    def sends(self, controller, user="viewer", login=None, badges=()):
        return controller.handle_message(user, "a", login=login, badges=badges)["ok"]

    def test_everyone_is_the_default(self):
        controller, _, _ = make()
        self.assertEqual(controller.snapshot()["settings"]["who"], "everyone")
        self.assertTrue(self.sends(controller))

    def test_vips_and_the_broadcaster_only(self):
        controller, _, _ = make(who="vips")
        self.assertFalse(self.sends(controller))
        self.assertFalse(self.sends(controller, badges={"moderator", "subscriber"}))
        self.assertTrue(self.sends(controller, badges={"vip"}))
        self.assertTrue(self.sends(controller, badges={"broadcaster"}))

    def test_listed_users_only(self):
        controller, _, _ = make(who="users", allowed_users=["@Bob", "alice_1", "bob"])
        self.assertEqual(controller.snapshot()["settings"]["allowed_users"], ["bob", "alice_1"])
        self.assertTrue(self.sends(controller, user="Bob"))
        self.assertTrue(self.sends(controller, user="Display Name", login="alice_1"))
        self.assertFalse(self.sends(controller, user="carol", badges={"vip"}))
        self.assertTrue(self.sends(controller, user="streamer", badges={"broadcaster"}))

    def test_the_list_adds_to_vips(self):
        controller, _, _ = make(who="vips", allowed_users=["bob"])
        self.assertTrue(self.sends(controller, user="bob"))
        self.assertTrue(self.sends(controller, user="carol", badges={"vip"}))
        self.assertFalse(self.sends(controller, user="dave"))

    def test_refused_senders_are_counted_but_not_logged_and_the_test_console_is_exempt(self):
        controller, _, _ = make(who="users")
        self.assertFalse(self.sends(controller))
        snapshot = controller.snapshot()
        self.assertEqual(snapshot["stats"]["ignored"], 1)
        self.assertEqual(snapshot["log"], [])
        self.assertTrue(controller.handle_message("you (test)", "a", source="test")["ok"])

    def test_bot_events_carry_badges_and_must_be_well_formed(self):
        controller, _, _ = make(who="vips")
        event = {"type": "message", "user": "v", "text": "a"}
        self.assertFalse(controller.handle_event(event)["ok"])
        self.assertTrue(controller.handle_event({**event, "badges": ["vip"]})["ok"])
        for bad in ({"badges": "vip"}, {"badges": [1]}, {"login": 5}):
            with self.assertRaises(ValueError):
                controller.handle_event({**event, **bad})

    def test_who_is_kept_across_restarts(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "chat.json"
            make(path, who="users", allowed_users=["bob"])
            settings = make(path)[0].snapshot()["settings"]
            self.assertEqual((settings["who"], settings["allowed_users"]), ("users", ["bob"]))


class SequenceTests(unittest.TestCase):
    KONAMI = "dup dup ddown ddown dleft dright dleft dright b a start"

    def names(self, text, **kwargs):
        sequence = chat.parse_sequence(text, **kwargs)
        return [(c.name, c.seconds) for c in sequence.commands]

    def test_parsing_a_message_of_commands(self):
        self.assertEqual(self.names("left 2 a   B\tdl .5"), [("left", 2.0), ("a", None), ("b", None), ("dpadleft", 0.5)])
        self.assertEqual(len(self.names(self.KONAMI)), 11)
        self.assertEqual(self.names("a 99", max_hold=3), [("a", 3.0)])

    def test_one_stray_word_ruins_the_message(self):
        for text in ("this is a message", "a lot of people here", "a b hello", "hello a", "a 1 2", "2 a", "a 1.234", "", "   "):
            sequence = chat.parse_sequence(text)
            self.assertEqual((sequence.commands, sequence.known), ([], False), text)

    def test_the_classic_code_works_with_custom_up_and_down(self):
        custom = {"up": "forward", "down": "back"}
        words = [name for name, _ in self.names("up up down down left right left right b a start", custom=custom)]
        self.assertEqual(words, ["forward", "forward", "back", "back", "left", "right", "left", "right", "b", "a", "plus"])
        self.assertEqual(self.names("up2 down2.5 Y1 a", custom=custom),
                         [("forward", 2.0), ("back", 2.5), ("y", 1.0), ("a", None)])
        self.assertEqual(self.names("up up"), [])   # without the custom names it is not a message of commands

    def test_bad_times_and_too_many_commands_are_deliberate_mistakes(self):
        self.assertEqual(chat.parse_sequence("a 0.04 b"), chat.Sequence([], "shortest hold is 0.05 s", True))
        self.assertEqual(chat.parse_sequence("a a a", max_commands=2).reason, "at most 2 commands in one message")

    def test_messages_with_several_commands_need_the_option(self):
        controller, _, events = make()
        self.assertFalse(controller.handle_message("viewer", "a b")["ok"])
        self.assertEqual(events, [])

    def test_commands_run_one_after_another(self):
        controller, clock, events = make(multi_commands=True)
        result = controller.handle_message("viewer", "a b 0.4 a")
        self.assertEqual((result["ok"], result["command"], result["seconds"]), (True, "a b a", 0.8))
        start = clock.now
        # A 0-0.2 s, B 0.2-0.6 s, A 0.6-0.8 s: different buttons follow each other at once, never two together.
        timeline = {0.1: {"A"}, 0.4: {"B"}, 0.7: {"A"}, 0.85: set()}
        for offset, expected in timeline.items():
            clock.now = start + offset
            controller.tick()
            self.assertEqual(events[-1][0], frozenset(expected), offset)
        self.assertEqual(controller.snapshot()["holds"], [])

    def test_quick_taps_have_no_dead_time_between_different_buttons(self):
        controller, clock, events = make(multi_commands=True)
        result = controller.handle_message("viewer", "y 0.1 x 0.1 a 0.1")
        self.assertEqual(result["seconds"], 0.3)
        start = clock.now
        for offset, expected in ((0.05, {"Y"}), (0.15, {"X"}), (0.25, {"A"}), (0.32, set())):
            clock.now = start + offset
            controller.tick()
            self.assertEqual(events[-1][0], frozenset(expected), offset)
        controller.handle_message("other", "a 0.05")
        self.assertEqual(events[-1][0], frozenset({"A"}))

    def test_commands_that_are_skipped_cost_no_time(self):
        controller, _, _ = make(multi_commands=True)
        result = controller.handle_message("viewer", "a start select b home")
        self.assertEqual((result["seconds"], result["command"]), (0.4, "a b"))

    def test_trailing_and_leading_space_is_ignored(self):
        controller, _, _ = make(multi_commands=True)
        for text in ("a b ", "  a b", "a b\u00a0", "a b\u3000 \u200b\U000e0000", "a b \n", "\t a  b \t"):
            self.assertTrue(controller.handle_message(f"v{hash(text)}", text)["ok"], repr(text))
        self.assertEqual(chat.parse_message(" left 2 \u00a0").command.seconds, 2.0)

    def test_the_same_button_twice_is_two_presses(self):
        controller, clock, events = make(multi_commands=True)
        controller.handle_message("viewer", "a a")
        start = clock.now
        for offset, expected in ((0.1, {"A"}), (0.22, set()), (0.3, {"A"}), (0.5, set())):
            clock.now = start + offset
            controller.tick()
            self.assertEqual(events[-1][0], frozenset(expected), offset)

    def test_turned_off_commands_are_skipped_but_the_message_still_counts(self):
        controller, clock, events = make(multi_commands=True)
        result = controller.handle_message("viewer", self.KONAMI)
        self.assertTrue(result["ok"])
        self.assertTrue(result["reason"].endswith("(skipped plus)"), result["reason"])
        self.assertEqual(result["command"], "dpadup dpadup dpaddown dpaddown dpadleft dpadright dpadleft dpadright b a")
        # "this is a message" still does nothing, and a message of only turned-off commands is refused.
        self.assertFalse(controller.handle_message("other", "this is a message")["ok"])
        self.assertEqual(controller.handle_message("third", "start minus")["reason"], "restricted")

    def test_two_chatters_at_once_both_run(self):
        controller, clock, events = make(multi_commands=True)
        controller.handle_message("one", "a b")
        controller.handle_message("two", "x y")
        self.assertEqual(events[-1][0], frozenset({"A", "X"}))
        self.assertEqual(len(controller.snapshot()["holds"]), 2)

    def test_a_chatter_cannot_start_a_second_message_while_one_is_running(self):
        controller, clock, _ = make(multi_commands=True)
        self.assertTrue(controller.handle_message("one", "a b")["ok"])
        self.assertEqual(controller.handle_message("one", "x y")["reason"], "your last commands are still running")
        self.assertTrue(controller.handle_message("One", "x")["ok"])   # a single command is not blocked
        clock.now += 1
        self.assertTrue(controller.handle_message("one", "x y")["ok"])

    def test_limits_apply_to_the_whole_message_count(self):
        controller, _, _ = make(multi_commands=True, max_commands=3)
        self.assertEqual(controller.handle_message("viewer", "a a a a")["reason"], "at most 3 commands in one message")
        self.assertTrue(controller.handle_message("viewer", "a a a")["ok"])

    def test_turning_a_group_off_cancels_its_later_commands(self):
        controller, clock, events = make(multi_commands=True)
        controller.handle_message("viewer", "a 2 dup 2")
        controller.update_settings({"groups": {"dpad": False}})
        clock.now += 2.2
        controller.tick()
        self.assertEqual(events[-1][0], frozenset())
        self.assertEqual(controller.snapshot()["holds"], [])
        controller.handle_message("other", "a 2 dup 2")
        controller.update_settings({"groups": {"face": False}})
        self.assertEqual(events[-1][0], frozenset())

    def test_history_shows_the_commands_typed(self):
        accepted = []
        controller, _, _ = make(accepted=accepted, multi_commands=True)
        controller.handle_message("viewer", "a 2 b")
        self.assertEqual(accepted, [("viewer", "a 2s b")])

    def test_settings(self):
        controller, _, _ = make()
        self.assertEqual(controller.snapshot()["settings"]["max_commands"], 12)
        for bad in ({"multi_commands": "yes"}, {"max_commands": 1}, {"max_commands": 31}):
            with self.assertRaises(ValueError, msg=str(bad)):
                controller.update_settings(bad)


class CommandSwitchTests(unittest.TestCase):
    def test_one_command_can_be_turned_off_on_its_own(self):
        controller, _, events = make(disabled_commands=["x", "Start"])
        self.assertEqual(controller.snapshot()["settings"]["disabled_commands"], ["x", "plus"])
        result = controller.handle_message("viewer", "x")
        self.assertEqual((result["ok"], result["reason"]), (False, "x is turned off"))
        self.assertEqual(events, [])
        self.assertTrue(controller.handle_message("other", "y")["ok"])

    def test_a_single_menu_button_can_be_left_off_when_the_others_group_is_on(self):
        controller, _, _ = make()
        controller.update_settings({"groups": {"menu": True}, "disabled_commands": ["home", "capture"]}, confirm_restricted=True)
        self.assertTrue(controller.handle_message("one", "plus")["ok"])
        self.assertTrue(controller.handle_message("two", "select")["ok"])
        self.assertEqual(controller.handle_message("three", "home")["reason"], "home is turned off")
        controller.update_settings({"groups": {"menu": False}})
        self.assertEqual(controller.handle_message("four", "plus")["reason"], "restricted")

    def test_a_custom_name_follows_its_command(self):
        controller, _, _ = make(custom_aliases={"jump": "b"}, disabled_commands=["b"])
        self.assertEqual(controller.handle_message("viewer", "jump")["reason"], "b is turned off")

    def test_turning_one_command_off_cancels_it_in_a_running_message(self):
        controller, clock, events = make(multi_commands=True)
        controller.handle_message("viewer", "a 2 x 2")
        controller.update_settings({"disabled_commands": ["x"]})
        clock.now += 2.2
        controller.tick()
        self.assertEqual((events[-1][0], controller.snapshot()["holds"]), (frozenset(), []))
        controller.handle_message("other", "x 2")
        self.assertTrue(controller.snapshot()["holds"] == [] or events[-1][0] == frozenset())

    def test_bad_lists_are_refused_and_the_setting_is_kept(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "chat.json"
            controller, _, _ = make(path, disabled_commands=["home"])
            for bad in ("home", ["nope"], [5], [["home"]]):
                with self.assertRaises(ValueError, msg=str(bad)):
                    controller.update_settings({"disabled_commands": bad})
            self.assertEqual(make(path)[0].snapshot()["settings"]["disabled_commands"], ["home"])


class PauseTests(unittest.TestCase):
    def test_a_running_macro_pauses_chat(self):
        controller, _, events = make()
        controller.handle_message("viewer", "a 2")
        self.assertEqual(events[-1][0], frozenset({"A"}))
        controller.pause(True)
        self.assertEqual(events[-1], (frozenset(), {}))
        self.assertEqual(controller.handle_message("viewer2", "b")["reason"], "a performance is running")
        self.assertTrue(controller.handle_message("you (test)", "b", source="test", run=False)["ok"])
        controller.pause(False)
        self.assertTrue(controller.handle_message("viewer2", "b")["ok"])


class MacroTriggerTests(unittest.TestCase):
    def setUp(self):
        self.started, self.stopped = [], []
        self.controller, self.clock, self.events = make(macro_chat=True, macro_triggers={"Roll": "roll-test"},
                                                        macro_cooldown_seconds=60)
        self.accepted = []
        self.controller._on_accept = lambda user, text: self.accepted.append((user, text))
        self.controller.set_macro_runner(
            lambda name, user: (self.started.append((name, user)) or {"ok": True, "seconds": 17.0}),
            lambda: self.stopped.append(True))

    def send(self, text="roll", user="viewer", **kwargs):
        return self.controller.handle_message(user, text, **kwargs)

    def test_the_word_starts_the_macro(self):
        self.assertEqual(self.controller.snapshot()["settings"]["macro_triggers"], {"roll": "roll-test"})
        result = self.send(" ROLL ")
        self.assertEqual((result["ok"], result["command"], result["seconds"]), (True, "roll", 17.0))
        self.assertEqual((self.started, self.accepted), ([("roll-test", "viewer")], [("viewer", "roll")]))
        self.assertEqual(self.controller.snapshot()["log"][0]["reason"], "macro roll-test")

    def test_the_word_counts_anywhere_in_a_message_but_only_as_a_whole_word(self):
        self.assertTrue(self.send("lets ROLL it!")["ok"])
        self.clock.now += 61
        self.assertTrue(self.send("rickroll, roll?")["ok"])
        self.clock.now += 61
        for chatter in ("rickroll", "rolling", "enrolled"):
            self.assertFalse(self.send(chatter)["ok"])
        self.assertEqual(len(self.started), 2)

    def test_it_needs_the_switch_but_the_test_console_does_not(self):
        self.controller.update_settings({"macro_chat": False})
        self.assertEqual(self.send()["reason"], "macros from chat are turned off")
        self.assertEqual(self.started, [])
        self.assertTrue(self.send(user="you (test)", source="test")["ok"])
        self.assertEqual(len(self.started), 1)

    def test_cooldown_applies_between_runs(self):
        self.assertTrue(self.send()["ok"])
        self.clock.now += 30
        self.assertEqual(self.send(user="other")["reason"], "that is on cooldown")
        self.clock.now += 31
        self.assertTrue(self.send(user="other")["ok"])
        self.assertEqual(len(self.started), 2)

    def test_the_usual_rules_apply(self):
        self.controller.update_settings({"mode": "off"})
        self.assertEqual(self.send()["reason"], "chat control is not open right now")
        self.controller.update_settings({"mode": "open", "who": "users", "allowed_users": ["bob"]})
        self.assertEqual(self.send(user="carol")["reason"], "not allowed to send commands")
        self.assertTrue(self.send(user="bob")["ok"])
        self.controller.pause(True)
        self.assertEqual(self.send(user="bob")["reason"], "a performance is running")
        self.assertEqual(len(self.started), 1)

    def test_a_dry_run_and_a_failed_start(self):
        result = self.send(source="test", run=False)
        self.assertEqual((result["ok"], self.started), (True, []))
        self.controller.set_macro_runner(lambda name, user: {"ok": False, "reason": "no macro called roll-test"}, None)
        result = self.send()
        self.assertEqual((result["ok"], result["reason"]), (False, "no macro called roll-test"))
        self.assertEqual(self.controller.snapshot()["log"][0]["ok"], False)

    def test_without_a_runner_the_word_is_just_chatter(self):
        controller, _, _ = make(macro_triggers={"roll": "x"})
        self.assertEqual(controller.handle_message("viewer", "roll")["reason"], "not a command")

    def test_words_must_not_clash_with_commands_or_custom_names(self):
        for bad in ({"a": "m"}, {"left": "m"}, {"roll2": "m"}, {"": "m"}, {"roll": "Bad Name"}, {"roll": 5},
                    {"x" * 21: "m"}, {"Roll": "m", "roll": "n"}, ["roll"]):
            with self.assertRaises(ValueError, msg=str(bad)):
                self.controller.update_settings({"macro_triggers": bad})
        self.controller.update_settings({"custom_aliases": {"jump": "b"}})
        with self.assertRaises(ValueError):
            self.controller.update_settings({"macro_triggers": {"jump": "m"}})
        with self.assertRaises(ValueError):
            self.controller.update_settings({"custom_aliases": {"roll": "b"}})
        self.assertEqual(self.controller.snapshot()["settings"]["macro_triggers"], {"roll": "roll-test"})
        for bad in ({"macro_chat": "yes"}, {"macro_cooldown_seconds": -1}, {"macro_cooldown_seconds": 4000}):
            with self.assertRaises(ValueError, msg=str(bad)):
                self.controller.update_settings(bad)

    def test_switching_chat_off_stops_a_running_macro_once(self):
        self.controller.update_settings({"mode": "off"})
        self.controller.update_settings({"tap_seconds": 1})   # still off: nothing more to stop
        self.assertEqual(self.stopped, [True])


class UnreadableSettingsTests(unittest.TestCase):
    def test_an_unreadable_file_is_kept_aside_instead_of_overwritten(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "chat.json"
            path.write_text('{"mode": "nonsense"}', encoding="utf-8")
            controller = chat.ChatController(lambda *args: None, path=path)
            self.assertEqual(controller.snapshot()["settings"]["mode"], "off")
            self.assertEqual((Path(folder) / "chat.json.bad").read_text(encoding="utf-8"), '{"mode": "nonsense"}')
            controller.update_settings({"tap_seconds": 1})
            self.assertEqual((Path(folder) / "chat.json.bad").read_text(encoding="utf-8"), '{"mode": "nonsense"}')


class CustomNameTests(unittest.TestCase):
    def test_a_custom_name_works_like_the_command_it_points_at(self):
        controller, clock, events = make(custom_aliases={"Jump": "b", "hop": "f"})
        self.assertEqual(controller.snapshot()["settings"]["custom_aliases"], {"jump": "b", "hop": "forward"})
        result = controller.handle_message("viewer", "JUMP2")
        self.assertEqual((result["ok"], result["command"], result["seconds"]), (True, "b", 2.0))
        self.assertEqual(events[-1][0], frozenset({"B"}))
        self.assertTrue(controller.handle_message("other", "hop")["ok"])
        self.assertEqual(events[-1][1], {"left": (0.0, 1.0)})

    def test_custom_names_work_inside_longer_messages(self):
        controller, _, _ = make(multi_commands=True, custom_aliases={"jump": "b"})
        result = controller.handle_message("viewer", "left jump a")
        self.assertEqual((result["ok"], result["command"]), (True, "left b a"))
        self.assertFalse(controller.handle_message("viewer2", "jump and run")["ok"])

    def test_a_custom_name_for_a_turned_off_command_stays_off(self):
        controller, _, events = make(custom_aliases={"pause": "start"})
        result = controller.handle_message("viewer", "pause")
        self.assertEqual((result["ok"], result["reason"]), (False, "restricted"))
        self.assertEqual(events, [])

    def test_custom_names_can_be_removed(self):
        controller, _, _ = make(custom_aliases={"jump": "b"})
        controller.update_settings({"custom_aliases": {}})
        self.assertFalse(controller.handle_message("viewer", "jump")["ok"])

    def test_bad_custom_names_are_refused(self):
        controller, _, _ = make(custom_aliases={"jump": "b"})
        for bad in ({"a": "b"}, {"left": "b"}, {"fwd": "b"}, {"jump2": "b"}, {"": "b"}, {"x" * 21: "b"}, {"jump": "nope"},
                    {"jump": "jump"}, {"jump": 5}, {"Jump": "a", "jump": "b"}, {5: "a"}, ["jump"],
                    {"".join(chr(97 + i // 26 ** place % 26) for place in range(3)): "a" for i in range(61)}):
            with self.assertRaises(ValueError, msg=str(bad)):
                controller.update_settings({"custom_aliases": bad})
        self.assertEqual(controller.snapshot()["settings"]["custom_aliases"], {"jump": "b"})

    def test_custom_names_are_kept_across_restarts(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "chat.json"
            make(path, custom_aliases={"jump": "b"})
            self.assertEqual(make(path)[0].snapshot()["settings"]["custom_aliases"], {"jump": "b"})


class ControllerTests(unittest.TestCase):
    def test_tap_and_release(self):
        controller, clock, events = make()
        self.assertTrue(controller.handle_message("viewer", "a")["ok"])
        self.assertEqual(events[-1][0], frozenset({"A"}))
        clock.now += 0.1
        controller.tick()
        self.assertEqual(events[-1][0], frozenset({"A"}))
        clock.now += 0.2
        controller.tick()
        self.assertEqual(events[-1], (frozenset(), {}))

    def test_left_and_right_walk_and_the_dpad_has_its_own_names(self):
        controller, _, events = make()
        controller.handle_message("one", "left 2")
        self.assertEqual(events[-1], (frozenset(), {"left": (-1.0, 0.0)}))
        controller.handle_message("two", "dl 2")
        self.assertEqual(events[-1][0], frozenset({"LEFT"}))

    def test_left_and_forward_merge_into_a_diagonal(self):
        controller, _, events = make()
        controller.handle_message("one", "left 2")
        controller.handle_message("two", "f 2")
        x, y = events[-1][1]["left"]
        self.assertAlmostEqual(x, -0.707, places=3)
        self.assertAlmostEqual(y, 0.707, places=3)

    def test_dpad_pairs_press_both_directions(self):
        controller, _, events = make()
        controller.handle_message("one", "dleft 2")
        controller.handle_message("two", "du 2")
        self.assertEqual(events[-1][0], frozenset({"LEFT", "UP"}))

    def test_timed_hold_and_decimals(self):
        controller, clock, events = make()
        controller.handle_message("viewer", "left 1.5")
        clock.now += 1.4
        controller.tick()
        self.assertIn("left", events[-1][1])
        clock.now += 0.2
        controller.tick()
        self.assertEqual(events[-1][1], {})

    def test_several_viewers_stack(self):
        controller, clock, events = make()
        controller.handle_message("one", "a 2")
        controller.handle_message("two", "dl 1")
        controller.handle_message("three", "lookup 1")
        buttons, sticks = events[-1]
        self.assertEqual(buttons, frozenset({"A", "LEFT"}))
        self.assertEqual(sticks, {"right": (0.0, 1.0)})
        clock.now += 1.1
        controller.tick()
        self.assertEqual(events[-1][0], frozenset({"A"}))

    def test_opposite_directions_cancel(self):
        controller, _, events = make()
        controller.handle_message("one", "forward 2")
        controller.handle_message("two", "back 2")
        self.assertEqual(events[-1][1], {})

    def test_one_command_per_message(self):
        controller, _, events = make()
        before = len(events)
        for text in ("left 3 please", "left 3 a y x", "a b"):
            self.assertFalse(controller.handle_message("viewer", text)["ok"])
        self.assertEqual(len(events), before)

    def test_default_groups(self):
        controller, _, _ = make(max_active=20)
        for text in ("a", "dl", "forward", "lookleft", "lb", "rb", "zl", "zr", "left", "ls", "rs"):
            self.assertTrue(controller.handle_message(text + "x", text)["ok"], text)
        for text in ("plus", "minus", "home", "capture", "start", "select"):
            self.assertFalse(controller.handle_message(text + "x", text)["ok"], text)

    def test_factory_defaults(self):
        settings = chat.ChatController(lambda *args: None, path=None).snapshot()["settings"]
        self.assertEqual((settings["mode"], settings["tap_seconds"], settings["max_hold_seconds"],
                          settings["user_cooldown_seconds"], settings["max_active"]), ("off", 0.5, 3.0, 0.5, 6))
        self.assertEqual([g for g, on in settings["groups"].items() if not on], ["menu"])

    def test_typed_values_beyond_the_sliders_are_accepted(self):
        controller, _, _ = make()
        controller.update_settings({"tap_seconds": 5, "max_hold_seconds": 30, "user_cooldown_seconds": 60, "max_active": 50})
        self.assertTrue(controller.handle_message("viewer", "left 30")["ok"])

    def test_menu_group_needs_confirmation(self):
        controller, _, _ = make()
        with self.assertRaises(ValueError):
            controller.update_settings({"groups": {"menu": True}})
        with self.assertRaises(ValueError):
            controller.update_settings({"groups": {"menu": True}}, confirm_restricted="yes")
        controller.update_settings({"groups": {"menu": True}}, confirm_restricted=True)
        self.assertTrue(controller.handle_message("viewer", "home")["ok"])

    def test_disabling_a_group_releases_its_holds(self):
        controller, _, events = make()
        controller.handle_message("viewer", "a 3")
        controller.update_settings({"groups": {"face": False}})
        self.assertEqual(events[-1][0], frozenset())

    def test_there_is_no_wait_between_one_viewers_commands_by_default(self):
        controller, _, _ = make()
        self.assertTrue(controller.handle_message("viewer", "a")["ok"])
        self.assertTrue(controller.handle_message("viewer", "b")["ok"])

    def test_cooldown_and_max_active(self):
        controller, clock, _ = make(user_cooldown_seconds=1.0, max_active=2)
        self.assertTrue(controller.handle_message("viewer", "a")["ok"])
        self.assertFalse(controller.handle_message("Viewer", "b")["ok"])
        clock.now += 1.1
        self.assertTrue(controller.handle_message("viewer", "b 3")["ok"])
        self.assertTrue(controller.handle_message("other", "x 3")["ok"])
        self.assertFalse(controller.handle_message("third", "y")["ok"])

    def test_chatter_is_not_logged_but_attempts_are(self):
        controller, _, _ = make()
        logged = len(controller.snapshot()["log"])
        controller.handle_message("viewer", "hello everyone")
        controller.handle_message("viewer", "a lot of people here")
        self.assertEqual(len(controller.snapshot()["log"]), logged)
        controller.handle_message("viewer", "left 0.04")
        self.assertEqual(controller.snapshot()["log"][0]["ok"], False)

    def test_dry_run_does_nothing(self):
        controller, _, events = make()
        before = len(events)
        snapshot = controller.snapshot()
        self.assertTrue(controller.handle_message("test", "a", source="test", run=False)["ok"])
        self.assertFalse(controller.handle_message("test", "left 0.04", source="test", run=False)["ok"])
        self.assertEqual(len(events), before)
        after = controller.snapshot()
        self.assertEqual((after["log"], after["stats"]), (snapshot["log"], snapshot["stats"]))

    def test_accepted_commands_are_reported_for_the_history(self):
        accepted = []
        controller, _, _ = make(accepted=accepted)
        controller.handle_message("viewer", "left 2")
        controller.handle_message("viewer", "A")
        controller.handle_message("viewer", "left 0.04")
        self.assertEqual(accepted, [("viewer", "left 2s"), ("viewer", "a")])

    def test_events_from_the_bot(self):
        controller, _, _ = make(mode="redeem", redeems=[TWO_MIN])
        result = controller.handle_event({"type": "redeem", "user": "v", "reward_id": "r2", "redemption_id": "abc"})
        self.assertEqual((result["ok"], result["status"], result["redemption_id"]), (True, "FULFILLED", "abc"))
        self.assertTrue(controller.handle_event({"type": "message", "user": "v", "text": "b"})["ok"])
        for bad in ({"type": "message", "user": "v"}, {"type": "redeem", "user": "v"}, {"type": "nope"}, []):
            with self.assertRaises(ValueError):
                controller.handle_event(bad)

    def test_settings_validation(self):
        controller, _, _ = make()
        for bad in ({"mode": "all"}, {"tap_seconds": 500}, {"max_hold_seconds": 400}, {"max_active": 0},
                    {"who": "mods"}, {"allowed_users": "bob"}, {"allowed_users": ["bob smith"]}, {"allowed_users": [""]},
                    {"allowed_users": [5]}, {"allowed_users": ["x" * 26]}, {"allowed_users": ["a"] * 101},
                    {"groups": {"nope": True}}, {"groups": {"face": "yes"}},
                    {"redeems": [{"id": "r", "seconds": 5}]},
                    {"redeems": [{"id": "bad id!", "seconds": 60}]},
                    {"redeems": [ONE_MIN, ONE_MIN]}, {"redeems": "x"}, {"redeems": [{"seconds": 60}]},
                    {"redeems": [{"id": f"r{i}", "seconds": 60} for i in range(13)]}):
            with self.assertRaises(ValueError, msg=str(bad)):
                controller.update_settings(bad)

    def test_longer_than_the_longest_hold_is_shortened_to_it(self):
        controller, _, _ = make(max_hold_seconds=1.0)
        result = controller.handle_message("viewer", "left 100")
        self.assertEqual((result["ok"], result["seconds"]), (True, 1.0))
        self.assertTrue(controller.handle_message("other", "left 1")["ok"])
        self.assertEqual(chat.parse_message("left 100", 5).command.seconds, 5)

    def test_open_mode_and_menu_buttons_are_not_restored_after_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "chat.json"
            controller, _, _ = make(path=path, tap_seconds=0.5, redeems=[TWO_MIN])
            controller.update_settings({"groups": {"menu": True}}, confirm_restricted=True)
            saved = json.loads(path.read_text())
            self.assertEqual(saved["mode"], "redeem")
            self.assertFalse(saved["groups"]["menu"])
            fresh = chat.ChatController(lambda *args: None, path=path)
            settings = fresh.snapshot()["settings"]
            self.assertEqual(settings["tap_seconds"], 0.5)
            self.assertEqual(settings["redeems"], [TWO_MIN])
            self.assertEqual(settings["mode"], "redeem")
            self.assertFalse(settings["groups"]["menu"])

    def test_corrupt_settings_file_falls_back_to_defaults(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "chat.json"
            path.write_text("{not json")
            self.assertEqual(chat.ChatController(lambda *args: None, path=path).snapshot()["settings"]["mode"], "off")

    def test_old_settings_files_still_load(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "chat.json"
            path.write_text(json.dumps({"mode": "off", "redeem_title": "x", "redeem_minutes": 2, "tap_seconds": 0.3}))
            settings = chat.ChatController(lambda *args: None, path=path).snapshot()["settings"]
            self.assertEqual((settings["mode"], settings["tap_seconds"], settings["redeems"]), ("off", 0.3, []))


class RedeemTests(unittest.TestCase):
    def setUp(self):
        self.controller, self.clock, self.events = make(mode="redeem", redeems=[ONE_MIN, TWO_MIN, THREE_MIN])

    def redeem(self, reward_id, user="viewer"):
        return self.controller.redeem(user, reward_id)

    def test_chat_is_closed_until_a_redeem(self):
        self.assertFalse(self.controller.handle_message("viewer", "a")["ok"])
        self.assertEqual(self.events, [])
        self.assertFalse(self.controller.timer()["active"])
        result = self.redeem("r2")
        self.assertEqual((result["ok"], result["status"]), (True, "FULFILLED"))
        self.assertTrue(self.controller.handle_message("viewer", "a")["ok"])
        self.assertEqual(self.controller.timer()["total_s"], 120)

    def test_the_window_ends(self):
        self.redeem("r1")
        self.clock.now += 61
        self.assertFalse(self.controller.handle_message("viewer", "a")["ok"])
        self.assertFalse(self.controller.timer()["active"])

    def test_a_longer_redeem_resets_the_timer_to_its_own_length_without_adding(self):
        self.redeem("r2")
        self.clock.now += 30
        result = self.redeem("r3")
        self.assertEqual((result["ok"], result["status"]), (True, "FULFILLED"))
        self.assertAlmostEqual(self.controller.timer()["remaining_s"], 180, delta=0.1)
        self.assertEqual(self.controller.timer()["total_s"], 180)

    def test_a_shorter_or_equal_redeem_is_denied_and_refundable(self):
        self.redeem("r3")
        self.clock.now += 20
        for reward in ("r1", "r2"):
            result = self.redeem(reward)
            self.assertEqual((result["ok"], result["status"]), (False, "CANCELED"), reward)
        self.assertAlmostEqual(self.controller.timer()["remaining_s"], 160, delta=0.1)
        self.redeem("r3")  # 160 s left, 180 s is longer, so it is accepted
        self.assertAlmostEqual(self.controller.timer()["remaining_s"], 180, delta=0.1)
        self.assertEqual(self.redeem("r3")["status"], "CANCELED")  # now equal

    def test_the_same_reward_late_in_the_window_resets_it(self):
        self.redeem("r2")
        self.clock.now += 100
        self.assertEqual(self.redeem("r2")["status"], "FULFILLED")
        self.assertAlmostEqual(self.controller.timer()["remaining_s"], 120, delta=0.1)

    def test_other_rewards_are_left_alone(self):
        result = self.redeem("someone-elses-reward")
        self.assertEqual((result["ok"], result["status"]), (False, None))
        self.assertFalse(self.controller.timer()["active"])

    def test_redeems_are_denied_when_off_or_open(self):
        self.controller.update_settings({"mode": "off"})
        self.assertEqual(self.redeem("r1")["status"], "CANCELED")
        self.controller.update_settings({"mode": "open"})
        result = self.redeem("r1")
        self.assertEqual(result["status"], "CANCELED")
        self.assertIn("already open", result["reason"])

    def test_off_closes_the_window(self):
        self.redeem("r2")
        self.controller.update_settings({"mode": "off"})
        self.assertFalse(self.controller.timer()["active"])

    def test_timer_names_who_redeemed(self):
        self.redeem("r1", user="Sam")
        self.assertEqual(self.controller.timer()["redeemed_by"], "Sam")


class OffTests(unittest.TestCase):
    def test_off_releases_everything(self):
        controller, _, events = make()
        controller.handle_message("viewer", "a 3")
        controller.update_settings({"mode": "off"})
        self.assertEqual(events[-1], (frozenset(), {}))


if __name__ == "__main__":
    unittest.main()
