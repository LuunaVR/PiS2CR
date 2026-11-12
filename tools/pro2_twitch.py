"""The Twitch connection: sign-in, chat, channel point rewards and redeems. Standard library only.

Sign-in uses Twitch's device code flow, so a public client id is all the app needs and nothing is typed on the Pi.
Chat is read anonymously over IRC. Rewards are managed through the Helix API, and redeems are polled and then
fulfilled or refunded (Twitch lets an app read and settle only the rewards it created itself).
"""
import json
import os
import random
import re
import socket
import ssl
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

# Public id of the Twitch app this software ships with. It is not a secret. PIS2CR_TWITCH_CLIENT_ID overrides it.
CLIENT_ID = "fvayh7pd0hf6bhaqietndlqazabb9j"
SCOPES = "channel:manage:redemptions"
TOKEN_PATH = Path.home() / ".config" / "pis2cr" / "twitch.json"
ID_URL = "https://id.twitch.tv/oauth2"
API_URL = "https://api.twitch.tv/helix"
IRC_HOST, IRC_PORT = "irc.chat.twitch.tv", 6697
POLL_S = 2.0
DENY_DELAY_S = 2.0  # Twitch's own screens lag, so a refund waits this long after the redeem is seen
STALE_REDEEM_S = 120  # a redeem older than this was made while the Pi was off, so it is refunded, not honoured
RESUME_RETRY_S = 10
MAX_TITLE = 45
MAX_PROMPT = 200
MAX_COST = 1_000_000
MAX_COOLDOWN_S = 7 * 24 * 3600


class TwitchError(ValueError):
    """Something Twitch refused or could not do; the message is fit to show the streamer."""


def http_request(method, url, headers=None, form=None, body=None, timeout=10):
    """One HTTP call returning (status, parsed json or {}). Network trouble raises TwitchError."""
    headers = dict(headers or {})
    data = None
    if form is not None:
        data = urllib.parse.urlencode(form).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    elif body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw, status = response.read(), response.status
    except urllib.error.HTTPError as err:
        raw, status = err.read(), err.code
    except (urllib.error.URLError, OSError) as err:
        raise TwitchError(f"Can't reach Twitch ({err})") from err
    try:
        payload = json.loads(raw) if raw else {}
    except ValueError:
        payload = {}
    return status, payload


_PRIVMSG = re.compile(r"^(?:@(?P<tags>\S+) )?:(?P<nick>[^!\s]+)!\S+ PRIVMSG #\S+ :(?P<text>.*)$")


def parse_chat_line(line):
    """{name, login, text, badges} of an IRC chat line, or None for anything else. badges is a set of badge names."""
    match = _PRIVMSG.match(line.rstrip("\r\n"))
    if match is None:
        return None
    tags = dict(tag.split("=", 1) for tag in (match["tags"] or "").split(";") if "=" in tag)
    name = tags.get("display-name", "").replace("\\s", " ") or match["nick"]
    badges = {badge.split("/", 1)[0] for badge in tags.get("badges", "").split(",") if badge}
    return {"name": name, "login": match["nick"], "text": match["text"], "badges": badges}


def parse_privmsg(line):
    """(display name, text) of an IRC chat line, or None for anything else."""
    chat = parse_chat_line(line)
    return (chat["name"], chat["text"]) if chat else None


def clean_reward_fields(fields, creating):
    if not isinstance(fields, dict):
        raise TwitchError("The reward must be an object")
    out = {}
    if "title" in fields or creating:
        title = fields.get("title")
        if not isinstance(title, str) or not 1 <= len(title.strip()) <= MAX_TITLE or not title.isprintable():
            raise TwitchError(f"The name must be 1 to {MAX_TITLE} characters")
        out["title"] = title.strip()
    if "prompt" in fields:
        prompt = fields["prompt"]
        if not isinstance(prompt, str) or len(prompt) > MAX_PROMPT:
            raise TwitchError(f"The description can be up to {MAX_PROMPT} characters")
        out["prompt"] = prompt
    if "cost" in fields or creating:
        cost = fields.get("cost")
        if isinstance(cost, bool) or not isinstance(cost, int) or not 1 <= cost <= MAX_COST:
            raise TwitchError("The cost must be a whole number of points, at least 1")
        out["cost"] = cost
    if "cooldown_seconds" in fields:
        cooldown = fields["cooldown_seconds"]
        if isinstance(cooldown, bool) or not isinstance(cooldown, int) or not 0 <= cooldown <= MAX_COOLDOWN_S:
            raise TwitchError("The cooldown must be a whole number of seconds")
        out["cooldown_seconds"] = cooldown
    if "enabled" in fields:
        if not isinstance(fields["enabled"], bool):
            raise TwitchError("Enabled must be on or off")
        out["enabled"] = fields["enabled"]
    return out


def _helix_body(values):
    body = {}
    for ours, theirs in (("title", "title"), ("prompt", "prompt"), ("cost", "cost"), ("enabled", "is_enabled")):
        if ours in values:
            body[theirs] = values[ours]
    if "cooldown_seconds" in values:
        body["is_global_cooldown_enabled"] = values["cooldown_seconds"] > 0
        if values["cooldown_seconds"] > 0:
            body["global_cooldown_seconds"] = values["cooldown_seconds"]
    return body


def _reward(raw, manageable):
    cooldown = raw.get("global_cooldown_setting") or {}
    return {"id": raw["id"], "title": raw.get("title", ""), "prompt": raw.get("prompt", ""), "cost": raw.get("cost", 0),
            "cooldown_seconds": cooldown.get("global_cooldown_seconds", 0) if cooldown.get("is_enabled") else 0,
            "enabled": raw.get("is_enabled", True), "manageable": manageable}


def _reason(payload, fallback):
    message = str(payload.get("message", ""))
    if "DUPLICATE" in message:
        return "A reward with that name already exists"
    if "TOO_MANY" in message:
        return "Your channel is at Twitch's limit of 50 rewards. Delete one on Twitch (disabled ones count too) and try again"
    return message.replace("_", " ").capitalize() if message else fallback


class TwitchBridge:
    def __init__(self, chat, client_id=None, token_path=TOKEN_PATH, http=http_request, clock=time.time,
                 sleep=None, run_threads=True):
        self._chat = chat
        self.client_id = client_id if client_id is not None else os.environ.get("PIS2CR_TWITCH_CLIENT_ID", CLIENT_ID)
        self._token_path = Path(token_path) if token_path else None
        self._http = http
        self._clock = clock
        self._run_threads = run_threads
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._sleep = sleep or (lambda seconds: self._stop.wait(seconds))
        self._tokens = None
        self._flow = None
        self._state = "disconnected"
        self._detail = ""
        self._profile = {}
        self._seen = []
        self._refunds = {}
        self._load_tokens()

    # -- state ---------------------------------------------------------------------------------------------------

    @property
    def connected(self):
        with self._lock:
            return self._state == "connected"

    def status(self):
        with self._lock:
            state = self._state if self.client_id else "not_configured"
            out = {"state": state, "configured": bool(self.client_id), "login": (self._tokens or {}).get("login"),
                   "name": self._profile.get("name") or (self._tokens or {}).get("login"),
                   "avatar": self._profile.get("avatar"),
                   "detail": self._detail, "code": None, "url": None}
            if self._state == "connecting" and self._flow:
                out["code"], out["url"] = self._flow["user_code"], self._flow["uri"]
            return out

    def _set(self, state, detail=""):
        with self._lock:
            self._state, self._detail = state, detail

    # -- tokens --------------------------------------------------------------------------------------------------

    def _load_tokens(self):
        if self._token_path is None or not self._token_path.exists():
            return
        try:
            tokens = json.loads(self._token_path.read_text(encoding="utf-8"))
            if not all(isinstance(tokens.get(k), str) for k in ("access_token", "refresh_token")):
                return
        except (OSError, ValueError, AttributeError):
            return
        self._tokens = tokens
        self._state = "connecting"

    def _save_tokens(self):
        if self._token_path is None:
            return
        try:
            self._token_path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=self._token_path.parent, prefix=".twitch-", suffix=".json")
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(self._tokens, handle)
            os.chmod(tmp, 0o600)
            os.replace(tmp, self._token_path)
        except OSError as err:
            self._detail = f"Could not save the sign-in ({err})"

    def _forget_tokens(self):
        self._tokens = None
        self._profile = {}
        self._refunds = {}
        if self._token_path is not None:
            try:
                self._token_path.unlink()
            except OSError:
                pass

    def _take_token_response(self, payload, keep=None):
        tokens = dict(keep or {})
        tokens.update(access_token=payload["access_token"], refresh_token=payload.get("refresh_token", tokens.get("refresh_token", "")),
                      expires_at=self._clock() + float(payload.get("expires_in", 3600)))
        self._tokens = tokens

    def _refresh(self):
        with self._lock:
            if not self._tokens:
                raise TwitchError("Not connected to Twitch")
            status, payload = self._http("POST", f"{ID_URL}/token", form={
                "client_id": self.client_id, "grant_type": "refresh_token", "refresh_token": self._tokens["refresh_token"]})
            if status != 200 or "access_token" not in payload:
                self._forget_tokens()
                self._set("disconnected", "Twitch sign-in expired. Connect again.")
                self._stop.set()
                raise TwitchError("Twitch sign-in expired. Connect again.")
            self._take_token_response(payload, keep=self._tokens)
            self._save_tokens()

    def _fresh_token(self):
        with self._lock:
            if not self._tokens:
                raise TwitchError("Not connected to Twitch")
            if self._tokens.get("expires_at", 0) - self._clock() < 120:
                self._refresh()
            return self._tokens["access_token"]

    def _api(self, method, path, params=None, body=None, retry=True):
        url = API_URL + path + ("?" + urllib.parse.urlencode(params) if params else "")
        headers = {"Authorization": f"Bearer {self._fresh_token()}", "Client-Id": self.client_id}
        status, payload = self._http(method, url, headers, None, body)
        if status == 401 and retry:
            self._refresh()
            return self._api(method, path, params, body, retry=False)
        return status, payload

    # -- sign in -------------------------------------------------------------------------------------------------

    def connect(self, background=True):
        """Begin signing in. The streamer opens the returned link, enters the code and approves."""
        if not self.client_id:
            raise TwitchError("No Twitch app is set up in this build yet")
        if self.connected:
            return self.status()
        status, payload = self._http("POST", f"{ID_URL}/device", form={"client_id": self.client_id, "scopes": SCOPES})
        if status != 200 or "device_code" not in payload:
            raise TwitchError(_reason(payload, "Twitch would not start the sign-in"))
        with self._lock:
            self._flow = {"device_code": payload["device_code"], "user_code": payload["user_code"],
                          "uri": payload["verification_uri"], "interval": max(1, int(payload.get("interval", 5))),
                          "expires_at": self._clock() + float(payload.get("expires_in", 600))}
            self._stop = threading.Event()
            self._set("connecting", "Waiting for you to approve it on Twitch")
            flow = self._flow
        if background:
            threading.Thread(target=self._poll_token, args=(flow,), daemon=True).start()
        else:
            self._poll_token(flow)
        return self.status()

    def _poll_token(self, flow):
        interval = flow["interval"]
        while self._clock() < flow["expires_at"]:
            self._sleep(interval)
            with self._lock:
                if self._flow is not flow:
                    return
            try:
                status, payload = self._http("POST", f"{ID_URL}/token", form={
                    "client_id": self.client_id, "scopes": SCOPES, "device_code": flow["device_code"],
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code"})
            except TwitchError as err:
                self._set("disconnected", str(err))
                return
            if status == 200 and "access_token" in payload:
                with self._lock:
                    self._take_token_response(payload)
                    self._flow = None
                self._activate()
                if self.connected:
                    self._chat.update_settings({"mode": "open"})  # a fresh sign-in starts open to chat
                return
            message = str(payload.get("message", ""))
            if "slow_down" in message:
                interval += 5
            elif "authorization_pending" not in message:
                with self._lock:
                    self._flow = None
                self._set("disconnected", "Twitch turned the sign-in down. Try again.")
                return
        with self._lock:
            self._flow = None
        self._set("disconnected", "The sign-in timed out. Try again.")

    def disconnect(self):
        with self._lock:
            token = (self._tokens or {}).get("access_token")
            self._stop.set()
            self._flow = None
            self._forget_tokens()
            self._set("disconnected", "")
        if token:
            try:
                self._http("POST", f"{ID_URL}/revoke", form={"client_id": self.client_id, "token": token})
            except TwitchError:
                pass

    def start(self):
        """Pick up a saved sign-in after a restart."""
        if self._tokens and self.client_id:
            threading.Thread(target=self._resume, daemon=True).start()

    def _resume(self):
        # The network may not be up yet at boot, so keep trying until the saved sign-in works or is rejected.
        while self._tokens and not self._activate():
            if self._tokens:
                self._set("connecting", self._detail)
            self._sleep(RESUME_RETRY_S)

    def _activate(self):
        """Check the saved sign-in and start listening. Returns True when connected."""
        try:
            token = self._fresh_token()
            status, payload = self._http("GET", f"{ID_URL}/validate", {"Authorization": f"OAuth {token}"})
            if status == 401:
                self._refresh()
                status, payload = self._http("GET", f"{ID_URL}/validate", {"Authorization": f"OAuth {self._tokens['access_token']}"})
            if status != 200 or "login" not in payload:
                raise TwitchError("Twitch would not confirm the sign-in")
        except TwitchError as err:
            self._set("disconnected", str(err))
            return False
        with self._lock:
            self._tokens.update(login=payload["login"], user_id=payload["user_id"])
            self._save_tokens()
            self._stop = threading.Event()
            stop = self._stop
        self._load_profile(payload["user_id"])
        self._set("connected", "")
        if self._run_threads:
            threading.Thread(target=self._chat_loop, args=(stop, payload["login"]), daemon=True).start()
            threading.Thread(target=self._redeem_loop, args=(stop,), daemon=True).start()
        return True

    # -- rewards -------------------------------------------------------------------------------------------------

    def _load_profile(self, user_id):
        """Display name and picture for the dashboard; the login name is the fallback if Twitch won't say."""
        try:
            status, payload = self._api("GET", "/users", {"id": user_id})
            user = payload["data"][0] if status == 200 else {}
        except (TwitchError, KeyError, IndexError):
            user = {}
        avatar = user.get("profile_image_url", "")
        with self._lock:
            self._profile = {"name": user.get("display_name") or None,
                             "avatar": avatar if avatar.startswith("https://static-cdn.jtvnw.net/") else None}

    def _broadcaster(self):
        with self._lock:
            if self._state != "connected":
                raise TwitchError("Connect to Twitch first")
            return self._tokens["user_id"]

    def list_rewards(self):
        if not self.connected:
            return {"connected": False, "rewards": []}
        broadcaster = self._broadcaster()
        status, payload = self._api("GET", "/channel_points/custom_rewards", {"broadcaster_id": broadcaster})
        if status != 200:
            raise TwitchError(_reason(payload, "Could not load your rewards"))
        status, mine = self._api("GET", "/channel_points/custom_rewards",
                                 {"broadcaster_id": broadcaster, "only_manageable_rewards": "true"})
        ours = {r["id"] for r in mine.get("data", [])} if status == 200 else set()
        return {"connected": True, "rewards": [_reward(r, r["id"] in ours) for r in payload.get("data", [])]}

    def create_reward(self, fields):
        body = {"is_enabled": True, **_helix_body(clean_reward_fields(fields, creating=True))}
        status, payload = self._api("POST", "/channel_points/custom_rewards", {"broadcaster_id": self._broadcaster()}, body)
        if status != 200 or not payload.get("data"):
            raise TwitchError(_reason(payload, "Twitch would not create the reward"))
        return _reward(payload["data"][0], True)

    def update_reward(self, reward_id, fields):
        body = _helix_body(clean_reward_fields(fields, creating=False))
        if not body:
            raise TwitchError("Nothing to change")
        status, payload = self._api("PATCH", "/channel_points/custom_rewards",
                                    {"broadcaster_id": self._broadcaster(), "id": reward_id}, body)
        if status != 200 or not payload.get("data"):
            raise TwitchError(_reason(payload, "Twitch would not change the reward"))
        return _reward(payload["data"][0], True)

    def delete_reward(self, reward_id):
        status, payload = self._api("DELETE", "/channel_points/custom_rewards",
                                    {"broadcaster_id": self._broadcaster(), "id": reward_id})
        if status != 204:
            raise TwitchError(_reason(payload, "Twitch would not delete the reward"))

    # -- redeems -------------------------------------------------------------------------------------------------

    def poll_redemptions(self):
        """One pass over the configured rewards: start chat control or refund, then mark each redemption."""
        broadcaster = self._broadcaster()
        for reward_id in self._chat.reward_ids():
            status, payload = self._api("GET", "/channel_points/custom_rewards/redemptions", {
                "broadcaster_id": broadcaster, "reward_id": reward_id, "status": "UNFULFILLED", "first": 50})
            if status != 200:
                self._set("connected", "A reward could not be read. Create it from this dashboard so Twitch lets it in.")
                continue
            for redemption in sorted(payload.get("data", []), key=lambda r: r.get("redeemed_at", "")):
                if redemption["id"] in self._seen:
                    continue
                self._seen = (self._seen + [redemption["id"]])[-200:]
                if self._age_s(redemption) > STALE_REDEEM_S:
                    self._refunds[redemption["id"]] = (self._clock(), reward_id)
                    continue
                result = self._chat.redeem(redemption.get("user_name") or redemption.get("user_login", "viewer"),
                                           reward_id, source="twitch", redemption_id=redemption["id"])
                if result.get("status") == "FULFILLED":
                    self._settle(broadcaster, redemption["id"], reward_id, "FULFILLED")
                elif result.get("status") == "CANCELED":
                    self._refunds[redemption["id"]] = (self._clock() + DENY_DELAY_S, reward_id)

    def _age_s(self, redemption):
        try:
            made = datetime.fromisoformat(redemption["redeemed_at"].replace("Z", "+00:00")).timestamp()
        except (KeyError, ValueError, AttributeError):
            return 0.0
        return self._clock() - made

    def _settle(self, broadcaster, redemption_id, reward_id, status):
        self._api("PATCH", "/channel_points/custom_rewards/redemptions", {
            "id": redemption_id, "broadcaster_id": broadcaster, "reward_id": reward_id}, {"status": status})

    def settle_refunds(self):
        """Refund the denied redeems whose wait is over."""
        due = [(rid, reward) for rid, (when, reward) in self._refunds.items() if when <= self._clock()]
        for redemption_id, reward_id in due:
            del self._refunds[redemption_id]
            self._settle(self._broadcaster(), redemption_id, reward_id, "CANCELED")

    def _redeem_loop(self, stop):
        next_poll = 0.0
        while not stop.is_set():
            try:
                if self._clock() >= next_poll:
                    next_poll = self._clock() + POLL_S
                    self.poll_redemptions()
                self.settle_refunds()
            except TwitchError as err:
                self._detail = str(err)
            stop.wait(0.5)

    # -- chat ----------------------------------------------------------------------------------------------------

    def handle_irc_line(self, line, send):
        """Answer pings and pass chat messages on to chat control."""
        if line.startswith("PING"):
            send("PONG" + line[4:])
            return
        parsed = parse_chat_line(line)
        if parsed:
            self._chat.handle_message(parsed["name"], parsed["text"], source="chat", login=parsed["login"], badges=parsed["badges"])

    def _chat_loop(self, stop, login):
        delay = 2
        while not stop.is_set():
            sock = None
            try:
                raw = socket.create_connection((IRC_HOST, IRC_PORT), timeout=15)
                sock = ssl.create_default_context().wrap_socket(raw, server_hostname=IRC_HOST)
                sock.settimeout(60)
                send = lambda text: sock.sendall((text + "\r\n").encode())
                for line in ("CAP REQ :twitch.tv/tags", f"NICK justinfan{random.randint(10000, 99999)}", f"JOIN #{login}"):
                    send(line)
                delay, buffer = 2, b""
                while not stop.is_set():
                    try:
                        chunk = sock.recv(4096)
                    except socket.timeout:
                        send("PING :keepalive")
                        continue
                    if not chunk:
                        break
                    if stop.is_set():
                        break
                    buffer += chunk
                    while b"\r\n" in buffer:
                        line, buffer = buffer.split(b"\r\n", 1)
                        self.handle_irc_line(line.decode("utf-8", "replace"), send)
            except OSError:
                pass
            finally:
                if sock is not None:
                    try:
                        sock.close()
                    except OSError:
                        pass
            stop.wait(delay)
            delay = min(delay * 2, 30)
