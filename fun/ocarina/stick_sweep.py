"""Measure how far the left stick bends each ocarina note, in small steps, to tune pitches more finely than up / down / half.

Same audio rule as calibrate.py: only the Virtual Audio Cable input is opened. The ocarina must be AWAY when this starts: the
script takes it out with D-Pad Left and puts it away with B. The dashboard tunnel must be up.
Run:  python fun/ocarina/stick_sweep.py [--check]
Writes fun/ocarina/audio/stick_sweep.wav and fun/ocarina/out/stick_sweep.json.
"""
import json
import sys
import time
from pathlib import Path

import numpy as np
import soundfile as sf

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import calibrate as cal

BUTTONS = ["L", "R", "A", "X", "Y"]
STEPS = [-1.0, -0.875, -0.75, -0.625, -0.5, -0.375, -0.25, -0.125, 0.0, 0.125, 0.25, 0.375, 0.5, 0.625, 0.75, 0.875, 1.0]
HOLD_S = 0.9


def tap(button, seconds):
    cal.post("/press", {"button": button, "down": True})
    time.sleep(seconds)
    cal.post("/press", {"button": button, "down": False})


def schedule():
    events = []
    time.sleep(1.0)   # silent baseline for the analysis
    tap("LEFT", 0.15)
    time.sleep(1.2)
    for button in BUTTONS:
        for y in STEPS:
            cal.post("/stick", {"stick": "left", "x": 0, "y": y, "active": True})
            time.sleep(0.25)
            down = time.perf_counter()
            cal.post("/press", {"button": button, "down": True})
            time.sleep(HOLD_S)
            up = time.perf_counter()
            cal.post("/press", {"button": button, "down": False})
            events.append({"button": button, "stick": f"{y:+.3f}", "down": down, "up": up})
            time.sleep(0.15)
            cal.post("/stick", {"stick": "left", "x": 0, "y": 0, "active": False})
            time.sleep(0.5)
        print(f"  {button} done", flush=True)
    tap("B", 0.15)
    time.sleep(1.0)
    return events


def main():
    index, info, api = cal.find_device()
    print(f"Recording from: {info['name']}  ({api})")
    if "--check" in sys.argv:
        return
    try:
        with cal.Recorder(index, info) as recorder:
            events = schedule()
    finally:
        cal.release_everything()
        cal.post("/press", {"button": "LEFT", "down": False})
    samples = recorder.samples()
    sf.write(HERE / "audio" / "stick_sweep.wav", samples, recorder.rate)
    results = cal.analyse(samples, recorder.rate, events, recorder.started)
    for r in results:
        r["y"] = float(r["stick"])
    (HERE / "out" / "stick_sweep.json").write_text(json.dumps(results, indent=1))
    for button in BUTTONS:
        row = [r for r in results if r["button"] == button]
        print(button, " ".join(f"{r['y']:+.2f}:{r.get('midi', float('nan')):.2f}" for r in row))


if __name__ == "__main__":
    main()
