"""Tests for the setup module. Run: python -m unittest test_setup (from launcher/)."""
import http.client
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pis2cr_launcher as launcher
import pis2cr_setup as setup


class BundleTests(unittest.TestCase):
    def make_tree(self):
        root = Path(tempfile.mkdtemp())
        (root / "tools" / "__pycache__").mkdir(parents=True)
        (root / "deploy").mkdir()
        (root / "tools" / "relay.py").write_bytes(b"a = 1\r\nb = 2\r\n")
        (root / "tools" / "test_relay.py").write_bytes(b"x")
        (root / "tools" / "__pycache__" / "relay.pyc").write_bytes(b"x")
        (root / "deploy" / "install.sh").write_bytes(b"#!/bin/bash\r\necho hi\r\n")
        (root / "deploy" / "a.patch").write_bytes(b"+line\r\n")
        (root / "deploy" / "blob.bin").write_bytes(b"\r\n\x00")
        return root

    def test_files_are_collected_with_unix_line_endings(self):
        files = setup.collect_bundle(self.make_tree())
        self.assertEqual(sorted(files), ["deploy/a.patch", "deploy/blob.bin", "deploy/install.sh", "tools/relay.py"])
        self.assertEqual(files["tools/relay.py"], b"a = 1\nb = 2\n")
        self.assertEqual(files["deploy/a.patch"], b"+line\n")
        self.assertEqual(files["deploy/blob.bin"], b"\r\n\x00")

    def test_hash_follows_content_not_line_endings(self):
        root = self.make_tree()
        first = setup.bundle_hash(setup.collect_bundle(root))
        (root / "tools" / "relay.py").write_bytes(b"a = 1\nb = 2\n")
        self.assertEqual(setup.bundle_hash(setup.collect_bundle(root)), first)
        (root / "tools" / "relay.py").write_bytes(b"a = 2\n")
        self.assertNotEqual(setup.bundle_hash(setup.collect_bundle(root)), first)

    def test_the_real_repo_bundle_has_what_the_pi_needs(self):
        files = setup.collect_bundle(setup.bundle_root())
        for name in ("tools/relay_pro2.py", "tools/bond_pro2.py", "deploy/install.sh", "deploy/pair-switch.sh",
                     "deploy/pair-controller.sh", "deploy/common.sh", "deploy/backend-imu-battery.patch"):
            self.assertIn(name, files)
        self.assertFalse(any(b"\r" in data for name, data in files.items() if name.endswith((".sh", ".py", ".service"))))


class DiscoveryTests(unittest.TestCase):
    ARP = """
Interface: 192.168.1.50 --- 0x9
  Internet Address      Physical Address      Type
  192.168.1.1           aa-bb-cc-00-11-22     dynamic
  192.168.1.20          dc-a6-32-00-11-22     dynamic
  192.168.1.255         ff-ff-ff-ff-ff-ff     static
"""

    def test_arp_rows_are_parsed(self):
        self.assertEqual(setup.parse_arp(self.ARP)["192.168.1.20"], "DC:A6:32:00:11:22")
        self.assertEqual(len(setup.parse_arp(self.ARP)), 3)

    def test_pi_addresses_are_recognised(self):
        self.assertTrue(setup.is_pi_mac("DC:A6:32:00:11:22"))
        self.assertTrue(setup.is_pi_mac("b8-27-eb-00-00-01"))
        self.assertFalse(setup.is_pi_mac("AA:BB:CC:00:11:22"))

    def test_pis_are_listed_first(self):
        completed = SimpleNamespace(stdout=self.ARP)
        with mock.patch.object(setup, "ssh_open", side_effect=lambda ip, timeout=0.5: ip in ("192.168.1.1", "192.168.1.20")), \
                mock.patch.object(setup, "reverse_name", return_value=""), \
                mock.patch.object(setup.subprocess, "run", return_value=completed):
            found = setup.find_pis(["192.168.1.1", "192.168.1.20", "192.168.1.30"])
        self.assertEqual([(item["host"], item["pi"]) for item in found], [("192.168.1.20", True), ("192.168.1.1", False)])


class OutputTests(unittest.TestCase):
    def test_lines_split_across_chunks(self):
        lines, rest = setup.split_lines(b"", b"one\r\ntwo\nthr")
        self.assertEqual((lines, rest), (["one", "two"], b"thr"))
        lines, rest = setup.split_lines(rest, b"ee\n")
        self.assertEqual((lines, rest), (["three"], b""))

    def test_remote_state(self):
        state = setup.parse_remote_state("abc123\n---\nPIS2CR_USER=pi\nPIS2CR_SWITCH_MAC=AA:BB:CC:DD:EE:FF\n---\nactive\n")
        self.assertEqual(state, {"installed_hash": "abc123", "switch_mac": "AA:BB:CC:DD:EE:FF", "controller_mac": "", "relay_running": True})

    def test_remote_state_on_a_fresh_pi(self):
        state = setup.parse_remote_state("\n---\n---\ninactive\n")
        self.assertEqual(state, {"installed_hash": "", "switch_mac": "", "controller_mac": "", "relay_running": False})

    def test_pi_info(self):
        info = setup.parse_pi_info("aarch64\nRaspberry Pi 4 Model B Rev 1.5\nDebian GNU/Linux 13 (trixie)\n64\n")
        self.assertEqual((info["arch"], info["model"], info["bits"]), ("aarch64", "Raspberry Pi 4 Model B Rev 1.5", "64"))


class ValidationTests(unittest.TestCase):
    def new_setup(self):
        return setup.Setup(tempfile.mkdtemp(), {"tools/a.py": b"x"}, lambda target: None)

    def test_bad_input_is_refused_without_connecting(self):
        s = self.new_setup()
        for host, user, password in (("-oProxyCommand=x", "pi", "pw"), ("a b", "pi", "pw"), ("pi.local", "root", "pw"),
                                     ("pi.local", "Bad User", "pw"), ("pi.local", "pi", "")):
            with mock.patch.object(setup.threading, "Thread") as thread:
                self.assertFalse(s.connect(host, user, password), (host, user))
                thread.assert_not_called()
            self.assertTrue(s.snapshot()["error"])

    def test_good_input_starts_a_connection(self):
        s = self.new_setup()
        with mock.patch.object(setup.threading, "Thread") as thread:
            self.assertTrue(s.connect("raspberrypi.local", "luna", "pw"))
            thread.assert_called_once()

    def test_only_one_step_runs_at_a_time(self):
        s = self.new_setup()
        with mock.patch.object(setup.threading, "Thread"):
            self.assertTrue(s.install())
            self.assertFalse(s.pair("switch"))

    def test_progress_and_hints_come_from_script_output(self):
        s = self.new_setup()
        s._line("STEP 3/7 Fetching the Switch backend")
        s._line("WAITING: open Controllers > Change Grip/Order on the Switch")
        snap = s.snapshot()
        self.assertEqual(snap["progress"], {"step": 3, "total": 7, "label": "Fetching the Switch backend"})
        self.assertTrue(snap["hint"].startswith("WAITING:"))
        self.assertEqual(len(snap["lines"]), 2)

    def test_finish_needs_a_connection_first(self):
        done = []
        s = setup.Setup(tempfile.mkdtemp(), {}, done.append)
        self.assertFalse(s.finish())
        s.target = {"host": "pi.local", "user": "luna", "identity_file": "k", "known_hosts": "h"}
        self.assertTrue(s.finish())
        self.assertEqual(done, [s.target])


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = setup.SetupServer(tempfile.mkdtemp(), lambda target: None)
        cls.port = cls.server.httpd.server_port

    @classmethod
    def tearDownClass(cls):
        cls.server.close()

    def request(self, method, path, headers=None, body=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        connection.request(method, path, body=body, headers=headers or {})
        reply = connection.getresponse()
        data = reply.read()
        connection.close()
        return reply.status, data

    def test_page_is_served_without_a_token(self):
        status, data = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b"PiS2CR Setup", data)

    def test_state_needs_the_token(self):
        self.assertEqual(self.request("GET", "/api/state")[0], 403)
        self.assertEqual(self.request("GET", "/api/state", {"X-Setup-Token": "wrong"})[0], 403)
        status, data = self.request("GET", "/api/state", {"X-Setup-Token": self.server.token})
        self.assertEqual(status, 200)
        self.assertFalse(json.loads(data)["connected"])

    def test_actions_need_the_token_and_the_right_host(self):
        self.assertEqual(self.request("POST", "/api/finish", {}, b"{}")[0], 403)
        headers = {"X-Setup-Token": self.server.token, "Host": "evil.example"}
        self.assertEqual(self.request("POST", "/api/finish", headers, b"{}")[0], 403)
        self.assertEqual(self.request("GET", "/", {"Host": "evil.example"})[0], 403)

    def test_bad_bodies_and_unknown_paths(self):
        headers = {"X-Setup-Token": self.server.token}
        self.assertEqual(self.request("POST", "/api/nope", headers, b"{}")[0], 404)
        self.assertEqual(self.request("POST", "/api/connect", headers, b"not json")[0], 400)
        self.assertEqual(self.request("POST", "/api/connect", headers, b"[]")[0], 400)
        self.assertEqual(self.request("POST", "/api/connect", headers, b"x" * 5000)[0], 413)

    def test_the_password_never_appears_in_the_state(self):
        headers = {"X-Setup-Token": self.server.token}
        with mock.patch.object(setup.Setup, "_connect"):
            body = json.dumps({"host": "pi.local", "user": "luna", "password": "hunter2hunter2"}).encode()
            self.request("POST", "/api/connect", headers, body)
        _, data = self.request("GET", "/api/state", headers)
        self.assertNotIn(b"hunter2hunter2", data)


class TunnelCommandTests(unittest.TestCase):
    def test_an_alias_only_keeps_the_old_behaviour(self):
        settings = dict(launcher.DEFAULTS, host="pis2cr")
        command = launcher.tunnel_command("ssh", settings, 8000)
        self.assertEqual(command[-3:], ["-L", "8000:127.0.0.1:8000", "pis2cr"])
        self.assertNotIn("-i", command)
        self.assertNotIn("StrictHostKeyChecking=yes", command)

    def test_a_setup_made_login_uses_its_own_key_and_known_hosts(self):
        settings = dict(launcher.DEFAULTS, host="192.168.1.20", user="luna", identity_file="C:\\k\\id", known_hosts="C:\\k\\kh")
        command = launcher.tunnel_command("ssh", settings, 8000)
        self.assertEqual(command[-1], "luna@192.168.1.20")
        self.assertIn("C:\\k\\id", command)
        self.assertIn("UserKnownHostsFile=C:\\k\\kh", command)
        self.assertIn("StrictHostKeyChecking=yes", command)

    def test_an_empty_host_means_setup_is_needed(self):
        self.assertEqual(launcher.DEFAULTS["host"], "")


if __name__ == "__main__":
    unittest.main()
