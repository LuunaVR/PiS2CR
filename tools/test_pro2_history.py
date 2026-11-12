"""Tests for the input history. Run: python -m unittest test_pro2_history (from tools/)."""
import unittest

import pro2_history as hist


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


def make():
    clock = Clock()
    history = hist.InputHistory(size=5, wall=clock)
    return history, hist.StreamerTracker(history, clock=clock), clock


def texts(history):
    return [(e["source"], e["text"]) for e in history.since(0)]


class HistoryTests(unittest.TestCase):
    def test_ids_grow_and_since_returns_only_newer(self):
        history, _, _ = make()
        first = history.add("chat", "command", "left", "viewer")
        history.add("chat", "command", "a", "viewer")
        self.assertEqual([e["text"] for e in history.since(first)], ["a"])
        self.assertEqual(history.last_id, 2)

    def test_old_entries_fall_off(self):
        history, _, _ = make()
        for i in range(8):
            history.add("chat", "command", str(i))
        self.assertEqual([e["text"] for e in history.since(0)], ["3", "4", "5", "6", "7"])
        self.assertEqual([e["text"] for e in history.recent(2)], ["6", "7"])


class ButtonTests(unittest.TestCase):
    def test_holding_a_button_makes_one_entry(self):
        history, tracker, clock = make()
        for _ in range(50):
            tracker.update_real({"A"})
            clock.now += 0.01
        tracker.update_real(set())
        self.assertEqual(texts(history), [("streamer", "A")])

    def test_pressing_again_makes_another_entry(self):
        history, tracker, clock = make()
        for held in ({"A"}, set(), {"A"}, set()):
            tracker.update_real(held)
            clock.now += 0.2
            tracker.update_real(held)
        self.assertEqual(texts(history), [("streamer", "A"), ("streamer", "A")])

    def test_buttons_pressed_together_share_an_entry(self):
        history, tracker, clock = make()
        tracker.update_real({"A"})
        clock.now += 0.03
        tracker.update_real({"A", "ZR"})
        clock.now += 0.2
        tracker.update_real({"A", "ZR"})
        self.assertEqual(texts(history), [("streamer", "ZR + A")])

    def test_buttons_pressed_apart_make_two_entries(self):
        history, tracker, clock = make()
        tracker.update_real({"A"})
        clock.now += 0.3
        tracker.update_real({"A", "B"})
        clock.now += 0.3
        tracker.update_real({"A", "B"})
        self.assertEqual(texts(history), [("streamer", "A"), ("streamer", "B")])

    def test_dashboard_clicks_count_as_the_streamer(self):
        history, tracker, clock = make()
        tracker.update_ui({"ZL"})
        clock.now += 0.2
        tracker.update_ui({"ZL"})
        self.assertEqual(texts(history), [("streamer", "ZL")])

    def test_the_same_button_from_pad_and_dashboard_is_one_entry(self):
        history, tracker, clock = make()
        tracker.update_real({"A"})
        tracker.update_ui({"A"})
        clock.now += 0.2
        tracker.update_real(set())
        self.assertEqual(len(history.since(0)), 1)

    def test_nice_labels(self):
        history, tracker, clock = make()
        tracker.update_real({"L"})
        clock.now += 0.2
        tracker.update_real({"L", "UP"})
        clock.now += 0.2
        tracker.update_real({"L", "UP"})
        self.assertEqual([e["text"] for e in history.since(0)], ["LB", "D-Pad \u2191"])


class StickTests(unittest.TestCase):
    def test_a_quick_flick_is_ignored(self):
        history, tracker, clock = make()
        for _ in range(10):
            tracker.update_real(set(), left=(0.0, 1.0))
            clock.now += 0.01
        tracker.update_real(set(), left=(0.0, 0.0))
        self.assertEqual(history.since(0), [])

    def test_holding_a_direction_makes_one_entry(self):
        history, tracker, clock = make()
        for _ in range(100):
            tracker.update_real(set(), left=(0.0, 1.0))
            clock.now += 0.01
        self.assertEqual(texts(history), [("streamer", "LS \u2191")])

    def test_changing_direction_after_settling_makes_a_new_entry(self):
        history, tracker, clock = make()
        for xy in ((0.0, 1.0), (1.0, 0.0)):
            for _ in range(40):
                tracker.update_real(set(), left=xy)
                clock.now += 0.01
        self.assertEqual([e["text"] for e in history.since(0)], ["LS \u2191", "LS \u2192"])

    def test_sweeping_through_directions_is_not_spammy(self):
        history, tracker, clock = make()
        import math
        for step in range(200):
            angle = step * 0.15
            tracker.update_real(set(), right=(math.cos(angle), math.sin(angle)))
            clock.now += 0.01
        self.assertLessEqual(len(history.since(0)), 3)

    def test_small_movements_near_the_middle_are_ignored(self):
        history, tracker, clock = make()
        for _ in range(100):
            tracker.update_real(set(), left=(0.2, 0.2))
            clock.now += 0.01
        self.assertEqual(history.since(0), [])

    def test_diagonals(self):
        history, tracker, clock = make()
        for _ in range(40):
            tracker.update_real(set(), right=(-0.7, 0.7))
            clock.now += 0.01
        self.assertEqual(texts(history), [("streamer", "RS \u2196")])


if __name__ == "__main__":
    unittest.main()
