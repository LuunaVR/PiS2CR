import json
import tempfile
import unittest
from pathlib import Path

import pis2cr_launcher as launcher

NETSTAT = """
Active Connections

  Proto  Local Address          Foreign Address        State           PID
  TCP    0.0.0.0:135            0.0.0.0:0              LISTENING       1100
  TCP    127.0.0.1:8000         0.0.0.0:0              LISTENING       34528
  TCP    [::1]:8000             [::]:0                 LISTENING       34528
  TCP    127.0.0.1:8000         127.0.0.1:52000        ESTABLISHED     34528
  TCP    127.0.0.1:52000        127.0.0.1:8000         ESTABLISHED     9000
  TCP    0.0.0.0:80000          0.0.0.0:0              LISTENING       77
  TCP    0.0.0.0:18000          0.0.0.0:0              LISTENING       88
"""


class ListenerTests(unittest.TestCase):
    def test_only_listening_rows_on_the_exact_port(self):
        self.assertEqual(launcher.parse_listeners(NETSTAT, 8000), [34528])

    def test_other_ports_and_empty_output(self):
        self.assertEqual(launcher.parse_listeners(NETSTAT, 135), [1100])
        self.assertEqual(launcher.parse_listeners("", 8000), [])

    def test_works_when_the_state_word_is_translated(self):
        text = "  TCP    127.0.0.1:8000         0.0.0.0:0              ABH\u00d6REN        42"
        self.assertEqual(launcher.parse_listeners(text, 8000), [42])


class SettingsTests(unittest.TestCase):
    def test_first_run_creates_the_folder_and_defaults(self):
        folder = Path(tempfile.mkdtemp()) / "PiS2CR-Data"
        settings, note = launcher.load_settings(folder)
        self.assertEqual((settings, note), (launcher.DEFAULTS, ""))
        self.assertEqual(json.loads((folder / "settings.json").read_text()), launcher.DEFAULTS)

    def test_edited_values_are_used(self):
        folder = Path(tempfile.mkdtemp())
        (folder / "settings.json").write_text(json.dumps({"host": "me@pi.local", "port": 9001, "open_browser": False}))
        settings, note = launcher.load_settings(folder)
        self.assertEqual((settings["host"], settings["port"], settings["open_browser"], note), ("me@pi.local", 9001, False, ""))

    def test_bad_values_fall_back(self):
        folder = Path(tempfile.mkdtemp())
        (folder / "settings.json").write_text(json.dumps({"host": "-oProxyCommand=x", "port": 99999, "open_browser": "yes"}))
        settings, note = launcher.load_settings(folder)
        self.assertEqual(settings, launcher.DEFAULTS)
        self.assertTrue(note)

    def test_broken_file_uses_defaults(self):
        folder = Path(tempfile.mkdtemp())
        (folder / "settings.json").write_text("{not json")
        settings, note = launcher.load_settings(folder)
        self.assertEqual(settings, launcher.DEFAULTS)
        self.assertIn("Using defaults", note)


class LogTests(unittest.TestCase):
    def test_log_is_written_and_rotated(self):
        folder = Path(tempfile.mkdtemp())
        launcher.FileLog(folder).write("hello")
        self.assertIn("hello", (folder / "launcher.log").read_text())
        (folder / "launcher.log").write_text("x" * (launcher.MAX_LOG_BYTES + 1))
        launcher.FileLog(folder)
        self.assertTrue((folder / "launcher.log.old").exists())
        self.assertFalse((folder / "launcher.log").exists())


if __name__ == "__main__":
    unittest.main()
