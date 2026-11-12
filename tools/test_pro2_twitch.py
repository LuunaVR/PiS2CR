import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import pro2_twitch as tw


def just_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class FakeChat:
    def __init__(self, ids=("r1",), status="FULFILLED"):
        self.ids, self.status, self.redeems, self.messages = list(ids), status, [], []
        self.settings = []

    def update_settings(self, patch, confirm_restricted=False):
        self.settings.append(patch)

    def reward_ids(self):
        return self.ids

    def redeem(self, user, reward_id, source="twitch", redemption_id=None):
        self.redeems.append((user, reward_id, redemption_id))
        return {"status": self.status}

    def handle_message(self, user, text, source="chat", run=True, login=None, badges=()):
        self.messages.append((user, text, source))
        self.senders = getattr(self, "senders", []) + [(login, set(badges))]


class FakeHttp:
    """Answers by (method, path); records calls. A reply may be a list that is consumed one call at a time."""

    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def __call__(self, method, url, headers=None, form=None, body=None, timeout=10):
        path = url.split("?")[0].replace(tw.API_URL, "API").replace(tw.ID_URL, "ID")
        self.calls.append((method, path, url, headers, form, body))
        reply = self.routes[(method, path)]
        if isinstance(reply, list):
            reply = reply.pop(0)
        return reply


def make(routes, chat=None, **kwargs):
    folder = tempfile.mkdtemp()
    http = FakeHttp(routes)
    bridge = tw.TwitchBridge(chat or FakeChat(), client_id="cid", token_path=Path(folder) / "twitch.json",
                             http=http, sleep=lambda s: None, run_threads=False, **kwargs)
    return bridge, http


def signed_in(routes, chat=None, **kwargs):
    bridge, http = make(routes, chat, **kwargs)
    bridge._tokens = {"access_token": "tok", "refresh_token": "ref", "expires_at": 1e12, "user_id": "42", "login": "me"}
    bridge._state = "connected"
    return bridge, http


class ChatLineTests(unittest.TestCase):
    def test_privmsg_with_tags(self):
        line = "@badges=;display-name=Some\\sOne;id=1 :someone!someone@someone.tmi.twitch.tv PRIVMSG #me :forward 2"
        self.assertEqual(tw.parse_privmsg(line), ("Some One", "forward 2"))

    def test_privmsg_without_tags_and_other_lines(self):
        self.assertEqual(tw.parse_privmsg(":bob!bob@bob.tmi.twitch.tv PRIVMSG #me :a"), ("bob", "a"))
        self.assertIsNone(tw.parse_privmsg(":tmi.twitch.tv 001 justinfan1 :Welcome"))

    def test_ping_and_message_handling(self):
        bridge, _ = make({})
        sent = []
        bridge.handle_irc_line("PING :tmi.twitch.tv", sent.append)
        bridge.handle_irc_line(":bob!bob@x PRIVMSG #me :left", sent.append)
        self.assertEqual(sent, ["PONG :tmi.twitch.tv"])
        self.assertEqual(bridge._chat.messages, [("bob", "left", "chat")])

    def test_login_and_badges_reach_chat_control(self):
        bridge, _ = make({})
        line = "@badges=broadcaster/1,vip/1,subscriber/12;display-name=Bob :bob!bob@x PRIVMSG #me :a"
        bridge.handle_irc_line(line, lambda text: None)
        self.assertEqual(bridge._chat.senders, [("bob", {"broadcaster", "vip", "subscriber"})])
        self.assertEqual(tw.parse_chat_line(":bob!bob@x PRIVMSG #me :a")["badges"], set())


class SignInTests(unittest.TestCase):
    def test_not_configured_without_client_id(self):
        bridge = tw.TwitchBridge(FakeChat(), client_id="", token_path=None, run_threads=False)
        self.assertEqual(bridge.status()["state"], "not_configured")
        with self.assertRaises(tw.TwitchError):
            bridge.connect()

    def test_device_flow(self):
        routes = {
            ("POST", "ID/device"): (200, {"device_code": "dev", "user_code": "ABCD", "verification_uri": "https://t/x",
                                          "expires_in": 600, "interval": 5}),
            ("POST", "ID/token"): [(400, {"message": "authorization_pending"}),
                                   (200, {"access_token": "a", "refresh_token": "r", "expires_in": 3600})],
            ("GET", "ID/validate"): (200, {"login": "me", "user_id": "42"}),
            ("GET", "API/users"): (200, {"data": [{"display_name": "Me", "profile_image_url": "https://static-cdn.jtvnw.net/jtv_user_pictures/x.png"}]}),
        }
        bridge, http = make(routes)
        bridge.connect(background=False)
        status = bridge.status()
        self.assertEqual((status["state"], status["login"], status["name"]), ("connected", "me", "Me"))
        self.assertTrue(status["avatar"].startswith("https://static-cdn.jtvnw.net/"))
        self.assertEqual(bridge._chat.settings, [{"mode": "open"}])
        self.assertEqual(http.calls[0][4], {"client_id": "cid", "scopes": tw.SCOPES})
        saved = json.loads(bridge._token_path.read_text())
        self.assertEqual((saved["access_token"], saved["login"]), ("a", "me"))
        if os.name == "posix":
            self.assertEqual(bridge._token_path.stat().st_mode & 0o777, 0o600)

    def test_connecting_status_shows_code(self):
        routes = {("POST", "ID/device"): (200, {"device_code": "d", "user_code": "WXYZ", "verification_uri": "https://t/x",
                                                "expires_in": 0})}
        bridge, _ = make(routes)
        bridge._http("POST", tw.ID_URL + "/device")
        bridge._flow = {"user_code": "WXYZ", "uri": "https://t/x"}
        bridge._state = "connecting"
        self.assertEqual((bridge.status()["code"], bridge.status()["url"]), ("WXYZ", "https://t/x"))

    def test_denied_sign_in(self):
        routes = {
            ("POST", "ID/device"): (200, {"device_code": "d", "user_code": "U", "verification_uri": "https://t/x"}),
            ("POST", "ID/token"): (400, {"message": "access_denied"}),
        }
        bridge, _ = make(routes)
        bridge.connect(background=False)
        self.assertEqual(bridge.status()["state"], "disconnected")

    def test_refresh_when_expired_and_on_401(self):
        routes = {
            ("POST", "ID/token"): [(200, {"access_token": "new", "refresh_token": "r2", "expires_in": 3600})],
            ("GET", "API/channel_points/custom_rewards"): [(401, {}), (200, {"data": []}), (200, {"data": []})],
        }
        bridge, http = signed_in(routes)
        bridge.list_rewards()
        self.assertEqual(bridge._tokens["access_token"], "new")
        self.assertEqual(bridge._tokens["refresh_token"], "r2")

    def test_failed_refresh_signs_out(self):
        bridge, _ = signed_in({("POST", "ID/token"): (400, {"message": "Invalid refresh token"})})
        bridge._tokens["expires_at"] = 0
        with self.assertRaises(tw.TwitchError):
            bridge.list_rewards()
        self.assertEqual(bridge.status()["state"], "disconnected")
        self.assertIsNone(bridge._tokens)

    def test_disconnect_forgets_tokens(self):
        bridge, http = signed_in({("POST", "ID/revoke"): (200, {})})
        bridge._save_tokens()
        bridge.disconnect()
        self.assertFalse(bridge._token_path.exists())
        self.assertEqual(bridge.status()["state"], "disconnected")


class RewardTests(unittest.TestCase):
    RAW = {"id": "r1", "title": "Chat Control", "prompt": "p", "cost": 500, "is_enabled": True,
           "global_cooldown_setting": {"is_enabled": True, "global_cooldown_seconds": 60}}

    def test_list_flags_manageable(self):
        other = {**self.RAW, "id": "r2", "title": "Made On Twitch"}
        routes = {("GET", "API/channel_points/custom_rewards"): [(200, {"data": [self.RAW, other]}),
                                                                (200, {"data": [self.RAW]})]}
        bridge, http = signed_in(routes)
        rewards = bridge.list_rewards()["rewards"]
        self.assertEqual([(r["id"], r["manageable"]) for r in rewards], [("r1", True), ("r2", False)])
        self.assertEqual(rewards[0]["cooldown_seconds"], 60)
        self.assertIn("only_manageable_rewards=true", http.calls[1][2])

    def test_list_when_disconnected(self):
        bridge, _ = make({})
        self.assertEqual(bridge.list_rewards(), {"connected": False, "rewards": []})

    def test_create_body(self):
        routes = {("POST", "API/channel_points/custom_rewards"): (200, {"data": [self.RAW]})}
        bridge, http = signed_in(routes)
        reward = bridge.create_reward({"title": " Chat Control ", "cost": 500, "cooldown_seconds": 60})
        self.assertEqual(http.calls[0][5], {"is_enabled": True, "title": "Chat Control", "cost": 500,
                                            "is_global_cooldown_enabled": True, "global_cooldown_seconds": 60})
        self.assertTrue(reward["manageable"])

    def test_update_and_delete(self):
        routes = {("PATCH", "API/channel_points/custom_rewards"): (200, {"data": [self.RAW]}),
                  ("DELETE", "API/channel_points/custom_rewards"): (204, {})}
        bridge, http = signed_in(routes)
        bridge.update_reward("r1", {"enabled": False, "cooldown_seconds": 0})
        self.assertEqual(http.calls[0][5], {"is_enabled": False, "is_global_cooldown_enabled": False})
        bridge.delete_reward("r1")
        self.assertIn("id=r1", http.calls[1][2])

    def test_duplicate_name_message(self):
        bridge, _ = signed_in({("POST", "API/channel_points/custom_rewards"):
                               (400, {"message": "CREATE_CUSTOM_REWARD_DUPLICATE_REWARD"})})
        with self.assertRaisesRegex(tw.TwitchError, "already exists"):
            bridge.create_reward({"title": "x", "cost": 1})

    def test_too_many_rewards_message(self):
        bridge, _ = signed_in({("POST", "API/channel_points/custom_rewards"):
                               (400, {"message": "CREATE_CUSTOM_REWARD_TOO_MANY_REWARDS"})})
        with self.assertRaisesRegex(tw.TwitchError, "limit of 50"):
            bridge.create_reward({"title": "x", "cost": 1})

    def test_validation(self):
        for bad in ({"title": "", "cost": 5}, {"title": "x" * 46, "cost": 5}, {"title": "x", "cost": 0},
                    {"title": "x", "cost": True}, {"title": "x", "cost": 5, "cooldown_seconds": -1}):
            with self.assertRaises(tw.TwitchError):
                tw.clean_reward_fields(bad, creating=True)
        with self.assertRaises(tw.TwitchError):
            tw.clean_reward_fields({"enabled": "yes"}, creating=False)


class RedemptionTests(unittest.TestCase):
    def routes(self, rows):
        return {("GET", "API/channel_points/custom_rewards/redemptions"): (200, {"data": rows}),
                ("PATCH", "API/channel_points/custom_rewards/redemptions"): (200, {})}

    def test_fulfils_and_does_not_repeat(self):
        row = {"id": "x1", "user_name": "Bob", "redeemed_at": just_now()}
        chat = FakeChat()
        bridge, http = signed_in(self.routes([row]), chat)
        bridge.poll_redemptions()
        bridge.poll_redemptions()
        self.assertEqual(chat.redeems, [("Bob", "r1", "x1")])
        patches = [c for c in http.calls if c[0] == "PATCH"]
        self.assertEqual(len(patches), 1)
        self.assertEqual(patches[0][5], {"status": "FULFILLED"})

    def test_denied_is_refunded_only_after_two_seconds(self):
        now = [1000.0]
        bridge, http = signed_in(self.routes([{"id": "x2", "user_name": "Amy"}]), FakeChat(status="CANCELED"), clock=lambda: now[0])
        bridge.poll_redemptions()
        bridge.settle_refunds()
        self.assertEqual([c for c in http.calls if c[0] == "PATCH"], [])
        now[0] += 2.1
        bridge.settle_refunds()
        bridge.settle_refunds()
        self.assertEqual([c[5] for c in http.calls if c[0] == "PATCH"], [{"status": "CANCELED"}])

    def test_old_redeems_are_refunded_not_honoured(self):
        chat = FakeChat()
        bridge, http = signed_in(self.routes([{"id": "x4", "user_name": "Old", "redeemed_at": "2020-01-01T00:00:00Z"}]), chat)
        bridge.poll_redemptions()
        bridge.settle_refunds()
        self.assertEqual(chat.redeems, [])
        self.assertEqual([c[5] for c in http.calls if c[0] == "PATCH"], [{"status": "CANCELED"}])

    def test_unrelated_reward_is_left_alone(self):
        bridge, http = signed_in(self.routes([{"id": "x3", "user_name": "Amy"}]), FakeChat(status=None))
        bridge.poll_redemptions()
        self.assertEqual([c for c in http.calls if c[0] == "PATCH"], [])


class ResumeTests(unittest.TestCase):
    def test_a_saved_sign_in_is_retried_until_the_network_is_back(self):
        down = [tw.TwitchError("Can't reach Twitch"), tw.TwitchError("Can't reach Twitch")]

        def http(method, url, headers=None, form=None, body=None, timeout=10):
            if down:
                raise down.pop()
            if url.endswith("/validate"):
                return 200, {"login": "me", "user_id": "42"}
            return 200, {"data": []}

        path = Path(tempfile.mkdtemp()) / "twitch.json"
        path.write_text(json.dumps({"access_token": "a", "refresh_token": "r", "expires_at": 1e12}))
        bridge = tw.TwitchBridge(FakeChat(), client_id="cid", token_path=path, http=http, sleep=lambda s: None, run_threads=False)
        self.assertEqual(bridge.status()["state"], "connecting")
        bridge._resume()
        self.assertEqual(bridge.status()["state"], "connected")


if __name__ == "__main__":
    unittest.main()
