"""Find how short the gap between two presses of the same button can be before the game stops hearing them as two notes.

Plays pairs of A taps with different gaps through the Pi's macro player and counts the notes in the recording.
Only the (Virtual Audio Cable) recording device is opened. The game must be open with the ocarina out.
"""
import json
import sys
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import calibrate as cal

GAPS = [0.03, 0.05, 0.08, 0.12, 0.2, 0.3, 0.5]
TAP = 0.25
PAIR_SPACING = 2.0


def build(button="A", stick=0.0):
    steps, t = [], 0.0
    for gap in GAPS:
        for _ in range(2):
            steps.append({"t": round(t, 4), "buttons": [button], "left": [0, stick]})
            t += TAP
            steps.append({"t": round(t, 4), "buttons": [], "left": [0, 0]})
            t += gap
        t += PAIR_SPACING - gap
    return {"label": "gap test", "steps": steps}, t


def main():
    button = sys.argv[1] if len(sys.argv) > 1 else "A"
    stick = float(sys.argv[2]) if len(sys.argv) > 2 else 0.0
    macro, total = build(button, stick)
    cal.post("/macro/save", {"name": "gap-test", "macro": macro})
    index, info, api = cal.find_device()
    print(f"Recording from: {info['name']} ({api}); button {button}, stick {stick}")
    with cal.Recorder(index, info) as recorder:
        time.sleep(1.0)
        started = time.perf_counter()
        cal.post("/macro/play", {"name": "gap-test"})
        time.sleep(total + 1.5)
    samples = recorder.samples()
    # Notes are 0.25 s long, so look for tone segments of at least 0.1 s.
    segments = cal.tone_segments(samples, recorder.rate, min_length=0.08)
    print(f"{len(segments)} notes heard for {2 * len(GAPS)} presses")
    # The first press starts at 1.0 s + the path delay; group the heard notes by pair.
    first = segments[0][0] if segments else 0
    for k, gap in enumerate(GAPS):
        lo = first + k * (2 * TAP + gap + PAIR_SPACING - gap) - 0.2
        hi = lo + 2 * TAP + gap + 0.8
        found = [s for s in segments if lo <= s[0] < hi]
        print(f"  gap {gap * 1000:4.0f} ms: heard {len(found)} note(s)  " + ", ".join(f"{b - a:.2f}s" for a, b in found))


if __name__ == "__main__":
    main()
