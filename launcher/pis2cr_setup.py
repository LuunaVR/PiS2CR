"""First-run setup for PiS2CR, driven from a page in the browser.

Finds the Pi on the network, installs PiS2CR on it over SSH, and runs the Switch 2 and controller pairing scripts.
The Pi password is only held in memory for the length of the setup; what gets saved is an SSH key in PiS2CR-Data.
"""
import hashlib
import http.server
import ipaddress
import json
import re
import secrets
import socket
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import paramiko

NO_WINDOW = 0x08000000
PI_OUIS = ("B8:27:EB", "DC:A6:32", "E4:5F:01", "D8:3A:DD", "28:CD:C1", "2C:CF:67", "88:A2:9E")
TEXT_SUFFIXES = {".py", ".sh", ".html", ".service", ".sudoers", ".conf", ".patch", ".md"}
HOST_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$")
USER_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
PUBKEY_RE = re.compile(r"^ssh-ed25519 [A-Za-z0-9+/=]+( [^\r\n']*)?$")
MAC_RE = re.compile(r"^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")
MAX_BODY = 4096
MAX_LINES = 400


def bundle_root():
    """Folder that holds tools/ and deploy/: bundled into the exe, or the repo root when run from source."""
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "bundle"
    return Path(__file__).resolve().parent.parent


def collect_bundle(root):
    """The files to copy to the Pi, as {relative path: bytes}, with Windows line endings turned into Unix ones."""
    files = {}
    for folder in ("tools", "deploy"):
        for path in sorted((Path(root) / folder).rglob("*")):
            if not path.is_file() or "__pycache__" in path.parts or path.suffix == ".pyc" or path.name.startswith("test_"):
                continue
            data = path.read_bytes()
            if path.suffix in TEXT_SUFFIXES:
                data = data.replace(b"\r\n", b"\n")
            files[path.relative_to(root).as_posix()] = data
    return files


def bundle_hash(files):
    digest = hashlib.sha256()
    for name in sorted(files):
        digest.update(name.encode() + b"\0" + hashlib.sha256(files[name]).digest())
    return digest.hexdigest()[:16]


def is_pi_mac(mac):
    return mac.upper().replace("-", ":")[:8] in PI_OUIS


def parse_arp(text):
    """{ip: MAC} from Windows `arp -a` output."""
    found = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 2 and re.fullmatch(r"\d+\.\d+\.\d+\.\d+", parts[0]) and re.fullmatch(r"([0-9a-fA-F]{2}-){5}[0-9a-fA-F]{2}", parts[1]):
            found[parts[0]] = parts[1].replace("-", ":").upper()
    return found


def local_hosts():
    """Every address on this PC's /24 network, apart from the PC itself."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.connect(("192.0.2.1", 9))   # no packet is sent; this only picks the outgoing interface
        mine = probe.getsockname()[0]
    network = ipaddress.ip_network(f"{mine}/24", strict=False)
    return [str(host) for host in network.hosts() if str(host) != mine]


def ssh_open(ip, timeout=0.5):
    try:
        with socket.create_connection((ip, 22), timeout):
            return True
    except OSError:
        return False


def reverse_name(ip):
    try:
        return socket.gethostbyaddr(ip)[0]
    except OSError:
        return ""


def find_pis(hosts=None):
    """Hosts on the local network that answer on the SSH port, Raspberry Pis first."""
    hosts = hosts if hosts is not None else local_hosts()
    with ThreadPoolExecutor(64) as pool:
        open_hosts = [ip for ip, ok in zip(hosts, pool.map(ssh_open, hosts)) if ok]
        names = list(pool.map(reverse_name, open_hosts))
    arp = parse_arp(subprocess.run(["arp", "-a"], capture_output=True, text=True, creationflags=NO_WINDOW).stdout)
    results = [{"host": ip, "name": name, "pi": is_pi_mac(arp.get(ip, ""))} for ip, name in zip(open_hosts, names)]
    return sorted(results, key=lambda item: (not item["pi"], item["host"]))


def split_lines(buffer, chunk):
    """Add a chunk of output to the buffer. Returns (complete lines, leftover text)."""
    buffer += chunk
    *lines, rest = buffer.split(b"\n")
    return [line.decode("utf-8", "replace").rstrip("\r") for line in lines], rest


def parse_remote_state(text):
    """Read the output of the state command: bundle hash, then /etc/pis2cr.conf, then whether the relay runs."""
    hash_part, _, rest = text.partition("---\n")
    conf_part, _, active = rest.partition("---\n")
    conf = dict(line.split("=", 1) for line in conf_part.splitlines() if "=" in line)
    return {"installed_hash": hash_part.strip(), "switch_mac": conf.get("PIS2CR_SWITCH_MAC", ""),
            "controller_mac": conf.get("PIS2CR_CONTROLLER_MAC", ""), "relay_running": active.strip() == "active"}


def parse_pi_info(text):
    rows = (text.splitlines() + [""] * 4)[:4]
    return {"arch": rows[0].strip(), "model": rows[1].strip(), "os": rows[2].strip(), "bits": rows[3].strip()}


class Cancelled(Exception):
    pass


class PiSession:
    """One SSH login to the Pi with a password (setup only; the saved key is used afterwards)."""

    def __init__(self, host, user, password, known_hosts):
        self.host, self.user, self.password, self.known_hosts = host, user, password, str(known_hosts)
        self.client = paramiko.SSHClient()

    def connect(self):
        keys = self.client.get_host_keys()
        if Path(self.known_hosts).exists():
            keys.load(self.known_hosts)
        if self.host in keys:   # the Pi may have been flashed again since last time
            del keys[self.host]
        self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        self.client.connect(self.host, username=self.user, password=self.password, look_for_keys=False,
                            allow_agent=False, timeout=10, banner_timeout=15, auth_timeout=15)
        Path(self.known_hosts).parent.mkdir(parents=True, exist_ok=True)
        self.client.save_host_keys(self.known_hosts)

    def close(self):
        self.client.close()
        self.password = ""

    def run(self, command, on_line=None, use_sudo=False, cancel=None):
        """Run a command and stream its output. Returns the exit code. The sudo password goes in through stdin."""
        channel = self.client.get_transport().open_session()
        channel.set_combine_stderr(True)
        channel.settimeout(1)
        channel.exec_command(f"sudo -S -p '' {command}" if use_sudo else command)
        if use_sudo:
            channel.sendall((self.password + "\n").encode())
        channel.shutdown_write()
        buffer, collected = b"", []
        while True:
            if cancel is not None and cancel.is_set():
                channel.close()
                raise Cancelled()
            try:
                chunk = channel.recv(4096)
            except socket.timeout:
                continue
            if not chunk:
                break
            lines, buffer = split_lines(buffer, chunk)
            for line in lines:
                collected.append(line)
                if on_line:
                    on_line(line)
        if buffer:
            line = buffer.decode("utf-8", "replace").rstrip("\r")
            collected.append(line)
            if on_line:
                on_line(line)
        self.last_output = "\n".join(collected)
        return channel.recv_exit_status()

    def text(self, command):
        self.run(command)
        return self.last_output

    def upload(self, files, remote_root, on_progress=None):
        sftp = self.client.open_sftp()
        try:
            made = set()
            for index, (name, data) in enumerate(files.items(), 1):
                folder = remote_root + "/" + name.rsplit("/", 1)[0]
                if folder not in made:
                    self.run(f"mkdir -p '{folder}'")
                    made.add(folder)
                with sftp.file(f"{remote_root}/{name}", "wb") as handle:
                    handle.write(data)
                if name.endswith(".sh"):
                    sftp.chmod(f"{remote_root}/{name}", 0o755)
                if on_progress:
                    on_progress(index, len(files))
        finally:
            sftp.close()


def make_key(folder):
    """Create (or reuse) the key pair that the dashboard tunnel logs in with. Returns (private key path, public key text)."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    key = folder / "pis2cr_ed25519"
    if not key.exists():
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "pis2cr-dashboard", "-f", str(key)],
                       check=True, capture_output=True, creationflags=NO_WINDOW)
    public = key.with_suffix(".pub").read_text(encoding="utf-8").strip() if key.with_suffix(".pub").exists() else ""
    if not public:
        public = subprocess.run(["ssh-keygen", "-y", "-f", str(key)], check=True, capture_output=True, text=True,
                                creationflags=NO_WINDOW).stdout.strip()
    if not PUBKEY_RE.match(public):
        raise RuntimeError("The SSH key that was made does not look right.")
    return key, public


def key_login_works(key, known_hosts, user, host):
    command = ["ssh", "-i", str(key), "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes", "-o", f"UserKnownHostsFile={known_hosts}",
               "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=10", f"{user}@{host}", "true"]
    return subprocess.run(command, capture_output=True, timeout=30, creationflags=NO_WINDOW).returncode == 0


class Setup:
    """The steps of the setup, run one at a time in the background and reported through snapshot()."""

    def __init__(self, folder, files, on_done):
        self.folder = Path(folder)
        self.files, self.bundle_id = files, bundle_hash(files)
        self.on_done = on_done
        self.lock = threading.Lock()
        self.cancel_event = threading.Event()
        self.session = None
        self.info = {}
        self.remote = {}
        self.target = {}
        self.view = {"busy": "", "progress": None, "hint": "", "error": "", "lines": [], "found": None}

    def snapshot(self):
        with self.lock:
            view = dict(self.view, lines=list(self.view["lines"]))
            connected = self.session is not None
            remote = dict(self.remote)
        installed = bool(remote.get("installed_hash"))
        return {**view, "connected": connected, "pi": dict(self.info), "target": dict(self.target),
                "installed": installed, "up_to_date": installed and remote["installed_hash"] == self.bundle_id,
                "switch_paired": bool(remote.get("switch_mac")), "controller_paired": bool(remote.get("controller_mac")),
                "relay_running": bool(remote.get("relay_running"))}

    def _set(self, **changes):
        with self.lock:
            self.view.update(changes)

    def _line(self, line):
        with self.lock:
            self.view["lines"] = (self.view["lines"] + [line])[-MAX_LINES:]
            if line.startswith("STEP "):
                match = re.match(r"STEP (\d+)/(\d+) (.*)", line)
                if match:
                    self.view["progress"] = {"step": int(match[1]), "total": int(match[2]), "label": match[3]}
            elif line.startswith(("WAITING:", "FOUND ", "READY:", "NOT_READY:")):
                self.view["hint"] = line

    def start(self, name, work):
        """Run `work` in the background; refuses to start while another step is running."""
        with self.lock:
            if self.view["busy"]:
                return False
            self.view.update(busy=name, error="", progress=None, hint="", found=None)
            if name != "scan":
                self.view["lines"] = []
        self.cancel_event.clear()

        def runner():
            try:
                work()
            except Cancelled:
                self._set(error="Cancelled.")
            except paramiko.AuthenticationException:
                self._set(error="The Pi did not accept that username and password.")
            except paramiko.SSHException as err:
                self._set(error=f"SSH problem: {err}")
            except Exception as err:   # anything else must still end the step, or the page would wait forever
                self._set(error=str(err) or err.__class__.__name__)
            finally:
                self._set(busy="")

        threading.Thread(target=runner, daemon=True).start()
        return True

    def scan(self):
        return self.start("scan", lambda: self._set(found=find_pis()))

    def connect(self, host, user, password):
        host, user = str(host).strip(), str(user).strip()
        if not HOST_RE.match(host):
            self._set(error="Enter the Pi's address, like 192.168.1.20 or raspberrypi.local.")
            return False
        if not USER_RE.match(user) or user == "root":
            self._set(error="Enter the Pi's username (not root).")
            return False
        if not password:
            self._set(error="Enter the Pi's password.")
            return False
        return self.start("connect", lambda: self._connect(host, user, password))

    def _connect(self, host, user, password):
        if self.session:
            self.session.close()
            self.session = None
        known_hosts = self.folder / "ssh" / "known_hosts"
        session = PiSession(host, user, password, known_hosts)
        session.connect()
        if session.run("true", use_sudo=True) != 0:
            session.close()
            raise RuntimeError("Logged in, but sudo did not accept that password. This user needs to be an administrator.")
        key, public = make_key(self.folder / "ssh")
        session.run("mkdir -p ~/.ssh && chmod 700 ~/.ssh && touch ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys "
                    f"&& (grep -qxF '{public}' ~/.ssh/authorized_keys || echo '{public}' >> ~/.ssh/authorized_keys)")
        if not key_login_works(key, known_hosts, user, host):
            session.close()
            raise RuntimeError("Installed the key, but logging in with it did not work.")
        self.info = parse_pi_info(session.text(
            "uname -m; tr -d '\\0' < /proc/device-tree/model 2>/dev/null; echo; . /etc/os-release; echo \"$PRETTY_NAME\"; getconf LONG_BIT"))
        self.session = session
        self.target = {"host": host, "user": user, "identity_file": str(key), "known_hosts": str(known_hosts)}
        self._refresh()

    def _refresh(self):
        text = self.session.text('cat ~/pis2cr/.bundle-hash 2>/dev/null; echo; echo ---; cat /etc/pis2cr.conf 2>/dev/null; '
                                 'echo ---; systemctl is-active pis2cr-relay 2>/dev/null')
        with self.lock:
            self.remote = parse_remote_state(text)

    def _require_session(self):
        if self.session is None:
            raise RuntimeError("Connect to the Pi first.")

    def install(self):
        return self.start("install", self._install)

    def _install(self):
        self._require_session()
        self._set(progress={"step": 0, "total": 7, "label": "Copying files to the Pi"})
        self.session.upload(self.files, "pis2cr", lambda done, total: self._set(
            progress={"step": 0, "total": 7, "label": f"Copying files ({done} of {total})"}))
        self.session.run(f"echo '{self.bundle_id}' > ~/pis2cr/.bundle-hash")
        code = self.session.run('bash -c "$HOME/pis2cr/deploy/install.sh"', self._line, use_sudo=True, cancel=self.cancel_event)
        self._refresh()
        if code != 0:
            raise RuntimeError("The install failed. The log shows where.")

    def pair(self, kind):
        if kind not in ("switch", "controller"):
            return False
        return self.start(f"pair-{kind}", lambda: self._pair(kind))

    def _pair(self, kind):
        self._require_session()
        script = "pair-switch.sh" if kind == "switch" else "pair-controller.sh"
        code = self.session.run(f'bash -c "$HOME/pis2cr/deploy/{script}"', self._line, use_sudo=True, cancel=self.cancel_event)
        self._refresh()
        if code != 0:
            lines = self.snapshot()["lines"]
            reason = next((line for line in reversed(lines) if line.startswith(("ERROR:", "PAIRING_FAILED:"))), "Pairing did not finish.")
            raise RuntimeError(reason)

    def cancel(self):
        """Stop the running step, and any pairing script left running on the Pi."""
        self.cancel_event.set()
        if self.session is not None and self.view["busy"].startswith("pair-"):
            try:
                self.session.run("pkill -TERM -f '[p]air-switch.sh|[p]is2cr-switch.sh pair|[p]air-controller.sh'", use_sudo=True)
            except (OSError, paramiko.SSHException):
                pass

    def finish(self):
        if not self.target:
            self._set(error="Connect to the Pi first.")
            return False
        if self.session:
            self.session.close()
            self.session = None
        self.on_done(dict(self.target))
        return True


def make_handler(setup, token, html_path):
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def _send(self, status, body, kind="application/json"):
            data = body if isinstance(body, bytes) else json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _allowed(self):
            host = self.headers.get("Host", "")
            return host in (f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}")

        def _authorized(self):
            return self._allowed() and secrets.compare_digest(self.headers.get("X-Setup-Token", ""), token)

        def do_GET(self):
            if not self._allowed():
                return self._send(403, {"error": "forbidden"})
            path = self.path.split("?")[0]
            if path == "/":
                return self._send(200, Path(html_path).read_bytes(), "text/html; charset=utf-8")
            if path == "/api/state":
                return self._send(200, setup.snapshot()) if self._authorized() else self._send(403, {"error": "forbidden"})
            self._send(404, {"error": "unknown"})

        def do_POST(self):
            if not self._authorized():
                return self._send(403, {"error": "forbidden"})
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                return self._send(413, {"error": "too large"})
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
                if not isinstance(body, dict):
                    raise ValueError
            except ValueError:
                return self._send(400, {"error": "bad request"})
            path = self.path.split("?")[0]
            actions = {
                "/api/scan": lambda: setup.scan(),
                "/api/connect": lambda: setup.connect(body.get("host", ""), body.get("user", ""), body.get("password", "")),
                "/api/install": lambda: setup.install(),
                "/api/pair": lambda: setup.pair(body.get("kind", "")),
                "/api/cancel": lambda: setup.cancel() or True,
                "/api/finish": lambda: setup.finish(),
            }
            if path not in actions:
                return self._send(404, {"error": "unknown"})
            self._send(200, {"ok": bool(actions[path]())})

    return Handler


class SetupServer:
    """Serves the setup page on a free local port. open_url() is the address to browse to."""

    def __init__(self, folder, on_done, html_path=None):
        self.token = secrets.token_urlsafe(24)
        self.setup = Setup(folder, collect_bundle(bundle_root()), on_done)
        html = html_path or (Path(sys._MEIPASS) / "setup.html" if getattr(sys, "frozen", False) else Path(__file__).with_name("setup.html"))
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.setup, self.token, html))
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    @property
    def open_url(self):
        return f"http://127.0.0.1:{self.httpd.server_port}/?t={self.token}"

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()
