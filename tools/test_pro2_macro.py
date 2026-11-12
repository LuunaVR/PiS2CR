"""Tests for timed macros. Run: python -m unittest test_pro2_macro (from tools/)."""
import tempfile
import threading
import time
import unittest
from pathlib import Path

import pro2_macro as macro

BUTTONS = {"A", "B", "L", "R", "LEFT"}


def song(*steps):
    return {"label": "Test", "steps": [{"t": t, "buttons": list(b), **extra} for t, b, extra in steps]}


class CleanTests(unittest.TestCase):
    def test_a_valid_macro_is_normalised(self):
        clean = macro.clean_macro(song((0, ["L", "A", "A"], {"left": [0, 1]}), (0.5, [], {"left": [0, 0]}), (0.7, [], {})), BUTTONS)
        self.assertEqual(clean["duration"], 0.7)
        self.assertEqual(clean["steps"][0], {"t": 0.0, "buttons": ["A", "L"], "left": [0.0, 1.0]})

    def test_bad_macros_are_refused(self):
        ok = (0, ["A"], {}), (1, [], {})
        bad = [
            "x", {"steps": []}, {"steps": "no"}, {"label": "x" * 41, "steps": [{"t": 0, "buttons": []}]},
            song((0, ["NOPE"], {}), (1, [], {})),                       # unknown button
            song((1, ["A"], {}), (0.5, [], {})),                        # time goes backwards
            song((0, ["A"], {}), (601, [], {})),                        # too long
            song((0, ["A"], {}), (1, ["A"], {})),                       # never lets go
            song((0, ["A"], {}), (1, [], {"left": [0, 0.5]})),          # stick left deflected at the end
            song((0, ["A"], {"left": [2, 0]}), (1, [], {})),            # stick out of range
            song((0, ["A"], {"left": [True, 0]}), (1, [], {})),
            song((0, ["A"], {"left": [0]}), (1, [], {})),
            song((True, ["A"], {}), (1, [], {})),
            {"steps": [{"t": 0, "buttons": "A"}, {"t": 1, "buttons": []}]},
            {"steps": [{"t": i * 0.01, "buttons": []} for i in range(macro.MAX_STEPS + 1)]},
        ]
        for item in bad:
            with self.assertRaises(ValueError, msg=str(item)[:60]):
                macro.clean_macro(item, BUTTONS)
        macro.clean_macro({"steps": [{"t": t, "buttons": b} for t, b in ((0, ["A"]), (1, []))]}, BUTTONS)
        self.assertTrue(ok)


class StoreTests(unittest.TestCase):
    def test_put_get_list_delete_and_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "macros.json"
            store = macro.MacroStore(BUTTONS, path)
            store.put("roll", song((0, ["A"], {}), (1, [], {})))
            self.assertEqual(store.listing(), [{"name": "roll", "label": "Test", "duration": 1.0, "steps": 2,
                                                "speed": 1.0, "play_time": 1.0}])
            self.assertEqual(macro.MacroStore(BUTTONS, path).get("roll")["duration"], 1.0)
            store.delete("roll")
            self.assertIsNone(store.get("roll"))
            with self.assertRaises(ValueError):
                store.delete("roll")

    def test_bad_names_and_limits(self):
        store = macro.MacroStore(BUTTONS, None)
        good = song((0, ["A"], {}), (1, [], {}))
        for name in ("", "Roll", "1roll", "a b", "x" * 33, None, 5):
            with self.assertRaises(ValueError, msg=str(name)):
                store.put(name, good)
        for i in range(macro.MAX_MACROS):
            store.put(f"m{i}", good)
        with self.assertRaises(ValueError):
            store.put("one-too-many", good)
        store.put("m0", good)   # replacing an existing one is fine

    def test_a_broken_file_is_ignored(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "macros.json"
            path.write_text("{not json", encoding="utf-8")
            self.assertEqual(macro.MacroStore(BUTTONS, path).listing(), [])


class SpeedTests(unittest.TestCase):
    def test_speed_and_min_release_are_validated(self):
        base = song((0, ["A"], {}), (1, [], {}))
        self.assertEqual(macro.clean_macro({**base, "speed": 2, "min_release": 0.085}, BUTTONS)["speed"], 2.0)
        self.assertEqual(macro.clean_macro(base, BUTTONS)["speed"], 1.0)
        for bad in ({"speed": 0.4}, {"speed": 4.1}, {"speed": "fast"}, {"speed": True}, {"min_release": -1}, {"min_release": 0.6}):
            with self.assertRaises(ValueError, msg=str(bad)):
                macro.clean_macro({**base, **bad}, BUTTONS)

    def test_the_store_keeps_the_speed_across_restarts_and_reimports(self):
        good = song((0, ["A"], {}), (1, [], {}))
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "macros.json"
            store = macro.MacroStore(BUTTONS, path)
            store.put("roll", good)
            store.set_speed("roll", 2.5)
            self.assertEqual(store.listing()[0]["speed"], 2.5)
            self.assertEqual(store.listing()[0]["play_time"], 0.4)
            store.put("roll", good)   # re-import without a speed keeps the tuned one
            self.assertEqual(macro.MacroStore(BUTTONS, path).get("roll")["speed"], 2.5)
            store.put("roll", {**good, "speed": 1.5})   # an explicit speed wins
            self.assertEqual(store.get("roll")["speed"], 1.5)
            for bad in (0.1, 5, "x", None):
                with self.assertRaises(ValueError):
                    store.set_speed("roll", bad)
            with self.assertRaises(ValueError):
                store.set_speed("nope", 1)

    def test_steps_are_scaled_by_the_speed(self):
        clean = macro.clean_macro(song((0, ["A"], {}), (0.4, [], {}), (1.0, ["B"], {}), (2.0, [], {})), BUTTONS)
        self.assertEqual([s["t"] for s in macro.scaled_steps(clean, 2)], [0, 0.2, 0.5, 1.0])
        self.assertEqual([s["t"] for s in macro.scaled_steps({**clean, "speed": 0.5})], [0, 0.8, 2.0, 4.0])
        self.assertEqual([s["t"] for s in macro.scaled_steps(clean)], [0, 0.4, 1.0, 2.0])

    def test_a_repressed_button_gets_its_release_at_any_speed(self):
        # A for 0.3 s, released for 0.12 s, then A again.
        base = song((0, ["A"], {}), (0.3, [], {}), (0.42, ["A"], {}), (0.7, [], {}))
        loose = macro.clean_macro(base, BUTTONS)
        strict = macro.clean_macro({**base, "min_release": 0.085}, BUTTONS)
        self.assertEqual([s["t"] for s in macro.scaled_steps(loose, 1)], [0, 0.3, 0.42, 0.7])
        self.assertEqual([s["t"] for s in macro.scaled_steps(strict, 1)], [0, 0.3, 0.42, 0.7])   # already long enough
        fast = [s["t"] for s in macro.scaled_steps(strict, 2)]
        self.assertEqual(fast, [0, 0.125, 0.21, 0.35])   # the 60 ms gap is widened to 85 ms by ending the first note sooner
        faster = [s["t"] for s in macro.scaled_steps(strict, 4)]
        self.assertGreaterEqual(faster[1] - faster[0], macro.MIN_PRESS_S)   # but a note is never cut below the minimum press

    def test_the_release_is_only_pulled_back_for_the_same_button_and_never_below_a_minimum_press(self):
        different = macro.clean_macro({**song((0, ["A"], {}), (0.1, [], {}), (0.12, ["B"], {}), (0.3, [], {})),
                                       "min_release": 0.085}, BUTTONS)
        self.assertEqual([s["t"] for s in macro.scaled_steps(different, 1)], [0, 0.1, 0.12, 0.3])
        short = macro.clean_macro({**song((0, ["A"], {}), (0.05, [], {}), (0.07, ["A"], {}), (0.3, [], {})),
                                   "min_release": 0.085}, BUTTONS)
        steps = [s["t"] for s in macro.scaled_steps(short, 1)]
        self.assertEqual(steps[1], 0.03)   # cut to the minimum press, still not a full 85 ms release

    def test_waits_after_a_fixed_step_do_not_get_faster(self):
        steps = [{"t": 0, "buttons": ["A"], "fixed": True}, {"t": 0.5, "buttons": []}, {"t": 1.5, "buttons": ["B"]},
                 {"t": 2.5, "buttons": [], "fixed": True}, {"t": 3.5, "buttons": ["A"]}, {"t": 3.6, "buttons": []}]
        clean = macro.clean_macro({"steps": steps}, BUTTONS)
        self.assertEqual([s["t"] for s in macro.scaled_steps(clean, 2)], [0, 0.5, 1.0, 1.5, 2.5, 2.55])
        self.assertEqual(macro.play_time({**clean, "speed": 2.0}), 2.55)
        self.assertEqual(macro.play_time(clean), 3.6)
        for bad in ("yes", 1):
            with self.assertRaises(ValueError):
                macro.clean_macro({"steps": [{"t": 0, "buttons": [], "fixed": bad}]}, BUTTONS)

    def test_a_player_runs_at_the_saved_speed(self):
        seen = []
        player = macro.MacroPlayer(lambda buttons, sticks: seen.append(time.monotonic()))
        steps = macro.clean_macro({**song((0, ["A"], {}), (0.4, [], {})), "speed": 4}, BUTTONS)
        start = time.monotonic()
        player.play("t", steps)
        self.assertAlmostEqual(player.state()["duration"], 0.1, places=3)
        player.wait(2)
        self.assertLess(seen[1] - start, 0.2)


class PlayerTests(unittest.TestCase):
    def make(self):
        seen, changes = [], []
        player = macro.MacroPlayer(lambda buttons, sticks: seen.append((time.monotonic(), buttons, sticks)),
                                   on_change=changes.append)
        return player, seen, changes

    def test_steps_play_in_order_at_their_times_and_everything_is_released(self):
        player, seen, changes = self.make()
        steps = macro.clean_macro(song((0, ["A"], {"left": [0, 1]}), (0.10, ["B"], {}), (0.20, [], {})), BUTTONS)
        start = time.monotonic()
        self.assertTrue(player.play("t", steps))
        self.assertFalse(player.play("again", steps))   # one at a time
        self.assertEqual(player.state()["playing"], "t")
        player.wait(2)
        self.assertEqual([(b, s) for _, b, s in seen],
                         [(frozenset({"A"}), {"left": (0.0, 1.0)}), (frozenset({"B"}), {}), (frozenset(), {}), (frozenset(), {})])
        for (when, _, _), expected in zip(seen, (0, 0.10, 0.20)):
            self.assertAlmostEqual(when - start, expected, delta=0.03)
        self.assertEqual(changes, [True, False])
        self.assertIsNone(player.state()["playing"])

    def test_stop_lets_go_at_once(self):
        player, seen, changes = self.make()
        player.play("t", macro.clean_macro(song((0, ["A"], {}), (5, [], {})), BUTTONS))
        time.sleep(0.05)
        player.stop()
        player.wait(1)
        self.assertEqual(seen[-1][1:], (frozenset(), {}))
        self.assertEqual(len(seen), 2)
        self.assertEqual(changes, [True, False])
        self.assertTrue(player.play("next", macro.clean_macro(song((0, ["B"], {}), (0.05, [], {})), BUTTONS)))
        player.wait(1)

    def test_seek_jumps_forward_and_back_and_skips_the_intro(self):
        player, seen, _ = self.make()
        steps = macro.clean_macro({"steps": [
            {"t": 0, "buttons": ["LEFT"], "fixed": True}, {"t": 0.1, "buttons": [], "fixed": True},
            {"t": 0.2, "buttons": ["A"]}, {"t": 0.3, "buttons": []},
            {"t": 5.0, "buttons": ["B"]}, {"t": 5.1, "buttons": []}]}, BUTTONS)
        self.assertFalse(player.seek(1))   # nothing playing
        player.play("t", steps)
        time.sleep(0.5)
        self.assertTrue(player.seek(4.9))
        player.wait(2)
        pressed = [b for _, b, _ in seen if b]
        self.assertEqual(pressed, [frozenset({"LEFT"}), frozenset({"A"}), frozenset({"B"})])
        self.assertLess(seen[-1][0] - seen[0][0], 1.5)   # the wait to 5.0 s was cut short
        seen.clear()
        player.play("t", steps)
        time.sleep(0.05)
        player.seek(0)   # back to the start never replays the take-out
        time.sleep(0.05)
        player.stop()
        player.wait(1)
        self.assertNotIn(frozenset({"LEFT"}), [b for _, b, _ in seen[1:]])

    def test_an_output_error_still_releases(self):
        calls = []

        def output(buttons, sticks):
            calls.append(buttons)
            if buttons == frozenset({"A"}):
                raise RuntimeError("boom")

        player = macro.MacroPlayer(output)
        player.play("t", macro.clean_macro(song((0, ["A"], {}), (0.05, [], {})), BUTTONS))
        player.wait(1)
        self.assertEqual(calls[-1], frozenset())


class CancelComboTests(unittest.TestCase):
    def test_fires_once_when_both_become_held(self):
        combo = macro.CancelCombo()
        self.assertFalse(combo.update(["L"]))
        self.assertTrue(combo.update(["L", "R"]))
        self.assertFalse(combo.update(["L", "R", "A"]))
        self.assertFalse(combo.update(["R"]))
        self.assertTrue(combo.update(["R", "L"]))

    def test_one_button_never_fires(self):
        combo = macro.CancelCombo()
        self.assertFalse(any(combo.update(p) for p in (["L"], ["R"], [], ["ZL", "ZR"])))


if __name__ == "__main__":
    unittest.main()
