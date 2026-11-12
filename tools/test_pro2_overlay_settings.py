"""Tests for the overlay settings. Run: python -m unittest test_pro2_overlay_settings (from tools/)."""
import json
import tempfile
import unittest
from pathlib import Path

import pro2_overlay_settings as ov


class OverlaySettingsTests(unittest.TestCase):
    def test_defaults(self):
        settings = ov.OverlaySettings(path=None).snapshot()
        self.assertEqual(settings["history"]["count"], 6)
        self.assertTrue(settings["controller"]["tint"])
        self.assertEqual(settings["colors"]["chat"], "#a98bff")
        for widget in ov.WIDGETS:
            box = settings[widget]
            self.assertLessEqual(box["x"] + box["w"], 100)
            self.assertLessEqual(box["y"] + box["h"], 100)

    def test_update_changes_only_what_was_sent(self):
        store = ov.OverlaySettings(path=None)
        result = store.update("history", {"count": 3, "align": "left", "x": 10.456})
        self.assertEqual((result["history"]["count"], result["history"]["align"], result["history"]["opacity"]), (3, "left", 100))
        self.assertEqual(result["history"]["x"], 10.46)
        self.assertEqual(store.snapshot(), result)

    def test_colours_are_normalised(self):
        store = ov.OverlaySettings(path=None)
        self.assertEqual(store.update("colors", {"chat": "#AABBCC"})["colors"]["chat"], "#aabbcc")

    def test_snapping_can_be_turned_off(self):
        store = ov.OverlaySettings(path=None)
        self.assertEqual(store.snapshot()["editor"]["snap"], 1.2)
        self.assertEqual(store.update("editor", {"snap": 0})["editor"]["snap"], 0)

    def test_bad_values_are_refused_and_change_nothing(self):
        store = ov.OverlaySettings(path=None)
        before = store.snapshot()
        for group, values in (("nope", {"a": 1}), ("history", {"count": 0}), ("history", {"count": 21}),
                              ("history", {"align": "middle"}), ("history", {"sticks": "yes"}),
                              ("history", {"nonsense": 1}), ("history", {}), ("timer", {"label": "x" * 41}),
                              ("timer", {"size": 30}), ("history", {"count": 3, "backdrop": 500}),
                              ("colors", {"chat": "red"}), ("colors", {"chat": "#fff"}), ("colors", {"text": 5}),
                              ("controller", {"x": -1}), ("controller", {"w": 1}), ("controller", {"opacity": 5}),
                              ("controller", {"opacity": 101}), ("editor", {"snap": 6}), ("editor", {"snap": -1})):
            with self.assertRaises(ValueError, msg=str((group, values))):
                store.update(group, values)
        self.assertEqual(store.snapshot(), before)

    def test_reset_layout_keeps_other_options(self):
        store = ov.OverlaySettings(path=None)
        store.update("history", {"x": 5, "y": 5, "w": 10, "h": 10, "count": 3, "visible": False})
        store.update("colors", {"streamer": "#112233"})
        result = store.reset_layout()
        self.assertEqual({k: result["history"][k] for k in ("x", "y", "w", "h", "visible")},
                         {k: ov.defaults()["history"][k] for k in ("x", "y", "w", "h", "visible")})
        self.assertEqual(result["history"]["count"], 3)
        self.assertEqual(result["colors"]["streamer"], "#112233")

    def test_settings_survive_a_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "overlays.json"
            ov.OverlaySettings(path=path).update("timer", {"label": "Chat is driving", "bar": False, "x": 50})
            again = ov.OverlaySettings(path=path).snapshot()
            self.assertEqual((again["timer"]["label"], again["timer"]["bar"], again["timer"]["x"]),
                             ("Chat is driving", False, 50))

    def test_old_files_and_damaged_files_fall_back_cleanly(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "overlays.json"
            path.write_text("{broken")
            self.assertEqual(ov.OverlaySettings(path=path).snapshot(), ov.defaults())
            path.write_text(json.dumps({"history": {"count": 999}}))
            self.assertEqual(ov.OverlaySettings(path=path).snapshot(), ov.defaults())
            path.write_text(json.dumps({"controller": {"colors": True}, "history": {"count": 3}}))
            again = ov.OverlaySettings(path=path).snapshot()
            self.assertEqual(again["history"]["count"], 3)
            self.assertNotIn("colors", again["controller"])


if __name__ == "__main__":
    unittest.main()
