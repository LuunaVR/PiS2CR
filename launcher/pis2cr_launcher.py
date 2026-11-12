"""PiS2CR launcher for Windows.

Frees the local dashboard port, opens the SSH tunnel to the Pi, opens the dashboard, and closes the tunnel
(and with it the port) when the window is closed or the app is killed.
"""
import argparse
import ctypes
import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
import urllib.request
import webbrowser
from ctypes import wintypes
from pathlib import Path

DATA_DIR_NAME = "PiS2CR-Data"
DEFAULTS = {"host": "",         # the Pi's address, or an SSH config alias; empty means setup has not been done yet
            "user": "",         # login name on the Pi (leave empty when host is an alias or user@host)
            "port": 8000,        # local port for the dashboard
            "identity_file": "", # private key path, passed to ssh -i (setup makes one in PiS2CR-Data\\ssh)
            "known_hosts": "",   # known_hosts file used for the tunnel (setup makes one in PiS2CR-Data\\ssh)
            "open_browser": True}
REMOTE_PORT = 8000
RETRY_S = 5
MAX_LOG_BYTES = 200_000
NO_WINDOW = 0x08000000
ALREADY_EXISTS = 183
BG, TEXT, MUTED, PURPLE, RED, GRAY = "#14161a", "#eceaf0", "#9c9aa8", "#9146ff", "#c93a4e", "#3a3e48"
LOCAL = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def data_dir():
    """PiS2CR-Data next to the exe (or next to this script when run from source)."""
    base = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
    return base / DATA_DIR_NAME


def load_settings(folder):
    """Read settings.json from the data folder, writing the defaults first if it is missing. Returns (settings, note)."""
    folder = Path(folder)
    path = folder / "settings.json"
    note = ""
    settings = dict(DEFAULTS)
    try:
        folder.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_text(json.dumps(DEFAULTS, indent=2) + "\n", encoding="utf-8")
        stored = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(stored, dict):
            raise ValueError("settings.json must be an object")
        for key, default in DEFAULTS.items():
            value = stored.get(key, default)
            valid = isinstance(value, type(default))
            if key == "port":
                valid = valid and 1 <= value <= 65535
            if key == "host":
                valid = valid and not value.startswith("-")
            if key == "user":
                valid = valid and not value.startswith("-")
            if valid:
                settings[key] = value
            else:
                note = f"Ignored a bad {key} in settings.json."
    except (OSError, ValueError) as err:
        note = f"Could not use {DATA_DIR_NAME}\\settings.json ({err}). Using defaults."
    return settings, note


def save_settings(folder, settings):
    (Path(folder) / "settings.json").write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")


def ssh_target(settings):
    return f"{settings['user']}@{settings['host']}" if settings["user"] else settings["host"]


def tunnel_command(ssh, settings, remote_port):
    command = [ssh, "-N", "-o", "BatchMode=yes", "-o", "ServerAliveInterval=15", "-o", "ExitOnForwardFailure=yes"]
    if settings["identity_file"]:
        command += ["-i", settings["identity_file"], "-o", "IdentitiesOnly=yes"]
    if settings["known_hosts"]:
        command += ["-o", f"UserKnownHostsFile={settings['known_hosts']}", "-o", "StrictHostKeyChecking=yes"]
    return command + ["-L", f"{settings['port']}:127.0.0.1:{remote_port}", ssh_target(settings)]


class FileLog:
    """A small log in the data folder; the previous one is kept once as launcher.log.old."""

    def __init__(self, folder):
        self.path = Path(folder) / "launcher.log"
        self.lock = threading.Lock()
        try:
            if self.path.exists() and self.path.stat().st_size > MAX_LOG_BYTES:
                self.path.replace(self.path.with_suffix(".log.old"))
        except OSError:
            pass

    def write(self, message):
        with self.lock:
            try:
                with self.path.open("a", encoding="utf-8") as handle:
                    handle.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}\n")
            except OSError:
                pass


def run_quiet(*args, timeout=10):
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout, creationflags=NO_WINDOW).stdout


def parse_listeners(netstat_text, port):
    """Process ids listening on a TCP port, from `netstat -ano` output. Listening rows have a foreign address ending in :0."""
    pids = set()
    for line in netstat_text.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[0] == "TCP" and parts[2].endswith(":0") and parts[1].rsplit(":", 1)[-1] == str(port):
            pids.add(int(parts[4]))
    return sorted(pids)


def listeners(port):
    return parse_listeners(run_quiet("netstat", "-ano", "-p", "TCP"), port)


def process_name(pid):
    rows = run_quiet("tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH").strip().splitlines()
    return rows[0].split('","')[0].strip('"') if rows and rows[0].startswith('"') else "unknown"


def free_port(port, log):
    """Close whatever is listening on the port. Returns True when the port is free afterwards."""
    for pid in listeners(port):
        if pid in (0, 4, os.getpid()):
            continue
        name = process_name(pid)
        run_quiet("taskkill", "/F", "/T", "/PID", str(pid))
        log(f"Closed {name} (process {pid}) that was using port {port}.")
    for _ in range(20):
        if not listeners(port):
            return True
        threading.Event().wait(0.15)
    return False


class _Basic(ctypes.Structure):
    _fields_ = [("PerProcessUserTimeLimit", wintypes.LARGE_INTEGER), ("PerJobUserTimeLimit", wintypes.LARGE_INTEGER),
                ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]


class _Io(ctypes.Structure):
    _fields_ = [(name, ctypes.c_ulonglong) for name in (
        "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
        "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]


class _Extended(ctypes.Structure):
    _fields_ = [("Basic", _Basic), ("Io", _Io), ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]


def kill_with_me(process):
    """Tie a child to this app with a job object, so Windows ends it even if this app is killed."""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    job = kernel32.CreateJobObjectW(None, None)
    info = _Extended()
    info.Basic.LimitFlags = 0x2000   # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    kernel32.SetInformationJobObject(wintypes.HANDLE(job), 9, ctypes.byref(info), ctypes.sizeof(info))
    kernel32.AssignProcessToJobObject(wintypes.HANDLE(job), wintypes.HANDLE(int(process._handle)))
    return job   # the handle must stay open for as long as this app runs


class Tunnel(threading.Thread):
    """Keeps `ssh -L` running and reports whether the Pi's dashboard answers through it."""

    def __init__(self, settings, on_status, log):
        super().__init__(daemon=True)
        self.settings, self.on_status, self.log = settings, on_status, log
        self.host, self.port = ssh_target(settings), settings["port"]
        self.stop_event = threading.Event()
        self.process = None
        self._jobs = []

    def answers(self):
        try:
            with LOCAL.open(f"http://127.0.0.1:{self.port}/state", timeout=2) as reply:
                return reply.status == 200
        except OSError:
            return False

    def launch(self):
        if not free_port(self.port, self.log):
            raise RuntimeError(f"Port {self.port} is still in use and could not be closed.")
        ssh = shutil.which("ssh") or r"C:\Windows\System32\OpenSSH\ssh.exe"
        self.process = subprocess.Popen(
            tunnel_command(ssh, self.settings, REMOTE_PORT),
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, creationflags=NO_WINDOW)
        self._jobs.append(kill_with_me(self.process))

    def run(self):
        while not self.stop_event.is_set():
            self.on_status("connecting", f"Connecting to {self.host}...")
            try:
                self.launch()
            except (RuntimeError, OSError) as err:
                self.on_status("down", str(err))
                self.stop_event.wait(RETRY_S)
                continue
            while self.process.poll() is None and not self.stop_event.is_set():
                if self.answers():
                    self.on_status("connected", f"Dashboard is open on port {self.port}.")
                else:
                    self.on_status("connecting", "Tunnel is up. Waiting for the Pi's dashboard...")
                self.stop_event.wait(2)
            if self.stop_event.is_set():
                break
            error = (self.process.stderr.read() or b"").decode(errors="replace").strip().splitlines()
            self.on_status("down", error[-1] if error else "The tunnel closed. Retrying...")
            self.stop_event.wait(RETRY_S)

    def close(self):
        """Stop the tunnel and make sure the port is released."""
        self.stop_event.set()
        if self.process and self.process.poll() is None:
            run_quiet("taskkill", "/F", "/T", "/PID", str(self.process.pid))
            try:
                self.process.wait(3)
            except subprocess.TimeoutExpired:
                pass


class App:
    def __init__(self, settings, note, filelog, folder):
        self.settings, self.folder = settings, Path(folder)
        self.port, self.open_browser, self.opened = settings["port"], settings["open_browser"], False
        self.filelog = filelog
        self.state, self.message, self.notice = "connecting", "Starting...", note
        self.last_logged = None
        self.tunnel = None
        self.setup_server = None
        self.pending = queue.SimpleQueue()   # setup runs on other threads; tkinter must only be touched from this one
        self.root = tk.Tk()
        self.root.title("PiS2CR")
        self.root.configure(bg=BG)
        self.root.geometry("420x270")
        self.root.resizable(False, False)
        tk.Label(self.root, text="PiS2CR", bg=BG, fg=TEXT, font=("Segoe UI", 16, "bold")).pack(anchor="w", padx=20, pady=(18, 0))
        self.pill = tk.Label(self.root, font=("Segoe UI", 10, "bold"), fg="white", padx=14, pady=4)
        self.pill.pack(anchor="w", padx=20, pady=(8, 6))
        self.note = tk.Label(self.root, bg=BG, fg=MUTED, font=("Segoe UI", 9), wraplength=380, justify="left")
        self.note.pack(anchor="w", padx=20)
        row = tk.Frame(self.root, bg=BG)
        row.pack(anchor="w", padx=20, pady=(18, 0))
        self.open_button = self.button(row, "Open Dashboard", self.open_dashboard, PURPLE)
        self.copy_button = self.button(row, "Copy Overlay Link", self.copy_overlay, GRAY)
        row = tk.Frame(self.root, bg=BG)
        row.pack(anchor="w", padx=20, pady=(10, 0))
        self.setup_button = self.button(row, "Set Up Or Update Pi", self.open_setup, GRAY)
        self.root.protocol("WM_DELETE_WINDOW", self.quit)
        if note:
            self.filelog.write(note)
        if settings["host"]:
            self.filelog.write(f"Started. Tunnel to {ssh_target(settings)}, local port {settings['port']}.")
            self.start_tunnel()
        else:
            self.state, self.message = "setup", "Setup is needed. A setup page is opening in your browser."
            self.filelog.write("Started without a Pi configured; opening setup.")
            self.root.after(300, self.open_setup)
        self.render()

    def start_tunnel(self):
        self.state, self.message, self.opened = "connecting", "Starting...", False
        self.tunnel = Tunnel(self.settings, self.set_status, self.log)
        self.tunnel.start()

    def open_setup(self):
        if self.setup_server:
            self.setup_server.close()
        import pis2cr_setup
        self.setup_server = pis2cr_setup.SetupServer(self.folder, self.pending.put)
        webbrowser.open(self.setup_server.open_url)
        self.filelog.write("Opened the setup page.")

    def apply_setup(self, target):
        """Setup finished: remember the Pi and (re)start the tunnel to it."""
        self.settings.update(target)
        save_settings(self.folder, {**load_settings(self.folder)[0], **target})
        self.port = self.settings["port"]
        if self.tunnel:
            self.tunnel.close()
        self.filelog.write(f"Setup finished. Tunnel to {ssh_target(self.settings)}.")
        self.start_tunnel()

    def button(self, parent, text, command, color):
        button = tk.Button(parent, text=text, command=command, bg=color, fg="white", activebackground=color, activeforeground="white",
                           relief="flat", font=("Segoe UI", 9, "bold"), padx=12, pady=6, cursor="hand2", bd=0)
        button.pack(side="left", padx=(0, 10))
        return button

    def set_status(self, state, message):
        self.state, self.message = state, message
        if (state, message) != self.last_logged:
            self.last_logged = (state, message)
            self.filelog.write(f"{state}: {message}")
        if state == "connected" and self.open_browser and not self.opened:
            self.opened = True
            webbrowser.open(f"http://127.0.0.1:{self.port}/")

    def log(self, message):
        self.notice = message
        self.filelog.write(message)

    def render(self):
        while not self.pending.empty():
            self.apply_setup(self.pending.get())
        text, color = {"connected": ("Connected", PURPLE), "connecting": ("Connecting", GRAY),
                       "setup": ("Setup Needed", GRAY)}.get(self.state, ("Not Connected", RED))
        self.pill.configure(text=text, bg=color)
        self.note.configure(text=self.message + ("\n" + self.notice if self.notice else ""))
        self.root.after(400, self.render)

    def open_dashboard(self):
        webbrowser.open(f"http://127.0.0.1:{self.port}/")

    def copy_overlay(self):
        self.root.clipboard_clear()
        self.root.clipboard_append(f"http://127.0.0.1:{self.port}/overlay")
        self.copy_button.configure(text="Copied")
        self.root.after(1500, lambda: self.copy_button.configure(text="Copy Overlay Link"))

    def quit(self):
        if self.tunnel:
            self.tunnel.close()
        if self.setup_server:
            self.setup_server.close()
        self.filelog.write("Closed. Tunnel stopped.")
        self.root.destroy()

    def run(self):
        try:
            self.root.mainloop()
        finally:
            if self.tunnel:
                self.tunnel.close()


def already_running():
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    handle = kernel32.CreateMutexW(None, False, "Local\\PiS2CR_Launcher")
    return ctypes.get_last_error() == ALREADY_EXISTS, handle


def main():
    parser = argparse.ArgumentParser(description=f"Open the PiS2CR dashboard through an SSH tunnel. Settings live in {DATA_DIR_NAME}\\settings.json.")
    parser.add_argument("--host", help="SSH host or alias of the Pi (overrides settings.json)")
    parser.add_argument("--port", type=int, help="local port for the dashboard (overrides settings.json)")
    parser.add_argument("--no-browser", action="store_true", help="do not open the browser when connected")
    args = parser.parse_args()
    running, _mutex = already_running()
    if running:
        root = tk.Tk()
        root.withdraw()
        from tkinter import messagebox
        messagebox.showinfo("PiS2CR", "PiS2CR is already running. Look for its window in the taskbar.")
        return
    folder = data_dir()
    settings, note = load_settings(folder)
    if args.host and not args.host.startswith("-"):
        settings["host"] = args.host
    if args.port and 1 <= args.port <= 65535:
        settings["port"] = args.port
    if args.no_browser:
        settings["open_browser"] = False
    App(settings, note, FileLog(folder), folder).run()


if __name__ == "__main__":
    sys.exit(main())
