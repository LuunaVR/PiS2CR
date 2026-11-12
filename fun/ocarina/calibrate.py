"""Measure which pitch each ocarina input makes in the game, by pressing it through the dashboard and listening.

Only the audio device named "... (Virtual Audio Cable)" is ever opened. Devices named CABLE Input / CABLE Output are
never touched. The script refuses to run if it cannot find the Virtual Audio Cable recording device.

Run from the repo root (the dashboard tunnel must be up, the game open with the ocarina out):
  python fun/ocarina/calibrate.py --check     record 3 s and print the level, nothing is pressed
  python fun/ocarina/calibrate.py             press every note button with every stick position, then analyse
Writes fun/ocarina/audio/calibration.wav and fun/ocarina/out/calibration.json.
"""
import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path

import numpy as np
import sounddevice as sd
import soundfile as sf

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
DASHBOARD = "http://127.0.0.1:8000"
BUTTONS = ["Y", "X", "A", "L", "R"]                      # the note buttons: Y X A LB RB
STICK_STATES = [("neutral", None), ("up", (0, 1)), ("up half", (0, .5)), ("down", (0, -1)), ("down half", (0, -.5)),
                ("left", (-1, 0)), ("right", (1, 0))]
HOLD_S, REST_S, SETTLE_S = 1.2, 0.7, 0.25
HOST_API_ORDER = ["Windows WASAPI", "MME", "Windows DirectSound"]
LOCAL = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def find_device():
    """The Virtual Audio Cable recording device. Never anything with CABLE Input or CABLE Output in its name."""
    choices = []
    for index, info in enumerate(sd.query_devices()):
        name = info["name"].lower()
        if "(virtual audio cable)" not in name or "cable input" in name or "cable output" in name:
            continue
        if info["max_input_channels"] < 1:
            continue
        api = sd.query_hostapis(info["hostapi"])["name"]
        if api in HOST_API_ORDER:
            choices.append((HOST_API_ORDER.index(api), index, info, api))
    if not choices:
        sys.exit("No recording device named (Virtual Audio Cable) was found. Nothing was opened.")
    _, index, info, api = sorted(choices, key=lambda c: c[0])[0]
    return index, info, api


class Recorder:
    def __init__(self, index, info):
        self.rate = int(info["default_samplerate"])
        self.channels = min(2, info["max_input_channels"])
        self.chunks = []
        self.started = None
        self.stream = sd.InputStream(device=index, channels=self.channels, samplerate=self.rate, callback=self._take)

    def _take(self, data, frames, time_info, status):
        if self.started is None:
            self.started = time.perf_counter() - frames / self.rate
        self.chunks.append(data.copy())

    def __enter__(self):
        self.stream.start()
        return self

    def __exit__(self, *exc):
        self.stream.stop()
        self.stream.close()

    def samples(self):
        data = np.concatenate(self.chunks) if self.chunks else np.zeros((0, self.channels), np.float32)
        return data.mean(axis=1).astype(np.float32)


def post(path, body):
    request = urllib.request.Request(DASHBOARD + path, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
    with LOCAL.open(request, timeout=3) as reply:
        return reply.status


def release_everything():
    for button in BUTTONS:
        post("/press", {"button": button, "down": False})
    post("/stick", {"stick": "left", "x": 0, "y": 0, "active": False})


def run_schedule():
    events = []
    time.sleep(1.0)   # the first second is the silent baseline
    for stick_name, stick in STICK_STATES:
        for button in BUTTONS:
            if stick:
                post("/stick", {"stick": "left", "x": stick[0], "y": stick[1], "active": True})
            time.sleep(SETTLE_S)
            down = time.perf_counter()
            post("/press", {"button": button, "down": True})
            time.sleep(HOLD_S)
            up = time.perf_counter()
            post("/press", {"button": button, "down": False})
            events.append({"button": button, "stick": stick_name, "down": down, "up": up})
            time.sleep(0.15)
            post("/stick", {"stick": "left", "x": 0, "y": 0, "active": False})
            time.sleep(REST_S)
            print(f"  {stick_name:>9} {button}", flush=True)
    time.sleep(1.0)
    return events


def tone_segments(samples, rate, min_length=0.5):
    """(start, end) seconds of every stretch where a note is sounding, found from the loudness of the 350-2800 Hz band."""
    from scipy import signal
    sos = signal.butter(4, [350, 2800], btype="band", fs=rate, output="sos")
    band = signal.sosfilt(sos, samples)
    frame = int(0.01 * rate)
    env = np.sqrt(np.array([np.mean(band[i:i + frame] ** 2) for i in range(0, len(band) - frame, frame)]))
    base = float(np.median(env[:int(0.9 / 0.01)]))
    threshold = max(base * 8, float(env.max()) * 0.08)
    segments, start = [], None
    for i, loud in enumerate(env > threshold):
        if loud and start is None:
            start = i
        elif not loud and start is not None:
            if (i - start) * 0.01 >= min_length:
                segments.append((start * 0.01, (i - 1) * 0.01))
            start = None
    return segments


def analyse(samples, rate, events, started):
    """Pitch of every note and its delay (input to sound) from the recording. Notes are matched to presses in order."""
    import librosa
    segments = tone_segments(samples, rate)
    if len(segments) != len(events):
        print(f"warning: heard {len(segments)} notes for {len(events)} presses; matching in order anyway")
    results = []
    for event, (heard_start, heard_end) in zip(events, segments):
        entry = {"button": event["button"], "stick": event["stick"]}
        chunk = samples[int((heard_start + 0.15) * rate):int((heard_end - 0.1) * rate)]
        f0, voiced, _ = librosa.pyin(chunk, fmin=200, fmax=2400, sr=rate, frame_length=4096, hop_length=512, fill_na=np.nan)
        pitched = f0[voiced & ~np.isnan(f0)]
        if len(pitched) < 8:
            results.append({**entry, "error": "no steady tone heard"})
            continue
        hz = float(np.median(pitched))
        entry.update({"hz": round(hz, 1), "midi": round(float(librosa.hz_to_midi(hz)), 2),
                      "wobble_cents": round(float(np.std(librosa.hz_to_midi(pitched)) * 100), 1),
                      "onset_latency_ms": round((heard_start - (event["down"] - started)) * 1000),
                      "release_latency_ms": round((heard_end - (event["up"] - started)) * 1000)})
        results.append(entry)
    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="record 3 s from the Virtual Audio Cable and print the level")
    args = parser.parse_args()
    index, info, api = find_device()
    print(f"Recording from: {info['name']}  ({api}, {int(info['default_samplerate'])} Hz)")
    if args.check:
        with Recorder(index, info) as recorder:
            time.sleep(3)
        samples = recorder.samples()
        print(f"{len(samples) / recorder.rate:.1f} s recorded, peak {np.abs(samples).max():.4f}, rms {np.sqrt((samples ** 2).mean()):.4f}")
        return
    release_everything()
    try:
        with Recorder(index, info) as recorder:
            events = run_schedule()
            time.sleep(0.5)
    finally:
        release_everything()
    samples = recorder.samples()
    sf.write(HERE / "audio" / "calibration.wav", samples, recorder.rate)
    print(f"recorded {len(samples) / recorder.rate:.1f} s, peak {np.abs(samples).max():.3f}; analysing")
    results = analyse(samples, recorder.rate, events, recorder.started)
    heard = [r for r in results if "error" not in r]
    summary = {}
    if heard:
        on = np.array([r["onset_latency_ms"] for r in heard])
        off = np.array([r["release_latency_ms"] for r in heard])
        summary = {"onset_latency_ms_mean": round(float(on.mean())), "onset_latency_ms_std": round(float(on.std())),
                   "release_latency_ms_mean": round(float(off.mean())), "release_latency_ms_std": round(float(off.std()))}
        print("delay:", summary)
    (HERE / "out" / "calibration.json").write_text(json.dumps({"rate": recorder.rate, "summary": summary, "results": results}, indent=1))
    for r in results:
        if "error" in r:
            print(f"{r['stick']:>9} {r['button']}: {r['error']}")
        else:
            print(f"{r['stick']:>9} {r['button']}: {r['hz']:7.1f} Hz  midi {r['midi']:6.2f}  wobble {r['wobble_cents']:5.1f} c  "
                  f"on +{r['onset_latency_ms']} ms  off +{r['release_latency_ms']} ms")


if __name__ == "__main__":
    main()
