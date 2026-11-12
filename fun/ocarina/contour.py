"""Show how the pitch moves inside each reference note (glides, scoops, vibrato) in 20 ms steps.

Run:  python fun/ocarina/contour.py [first_note] [last_note]
"""
import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import audio_tools as at


def track(samples, rate, start, end, window_s=0.02, hop_s=0.01):
    win, size = int(window_s * rate), 16384
    freqs = np.fft.rfftfreq(size, 1 / rate)
    band = np.flatnonzero((freqs > 450) & (freqs < 1400))
    taper = np.hanning(win)
    points = []
    t = start
    while t + window_s <= end:
        s = int(t * rate)
        spectrum = np.abs(np.fft.rfft(samples[s:s + win] * taper, size))[band]
        k = int(np.argmax(spectrum))
        shift = 0.0
        if 0 < k < len(spectrum) - 1:
            a, b, c = np.log(spectrum[k - 1] + 1e-12), np.log(spectrum[k] + 1e-12), np.log(spectrum[k + 1] + 1e-12)
            shift = 0.5 * (a - c) / (a - 2 * b + c) if (a - 2 * b + c) else 0.0
        hz = freqs[band][k] + shift * (freqs[1] - freqs[0])
        points.append((t + window_s / 2, 69 + 12 * np.log2(hz / 440.0), float(spectrum[k])))
        t += hop_s
    return points


def main():
    samples, rate = at.load_mono(HERE / "audio" / "reference.wav")
    notes = json.loads((HERE / "out" / "reference_notes.json").read_text())["notes"]
    first = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    last = int(sys.argv[2]) if len(sys.argv) > 2 else len(notes) - 1
    for n in notes[first:last + 1]:
        pts = track(samples, rate, n["start"], n["end"])
        if not pts:
            continue
        midi = np.array([p[1] for p in pts])
        level = np.array([p[2] for p in pts])
        rel = (midi - n["midi"]) * 100
        print(f"{n['index']:2d} {n['name']:>3} {n['start']:6.2f}-{n['end']:5.2f}  "
              f"first {rel[:3].mean():+5.0f}c  mid {rel[len(rel)//3:2*len(rel)//3+1].mean():+5.0f}c  last {rel[-3:].mean():+5.0f}c  "
              f"range {rel.min():+5.0f}..{rel.max():+5.0f}  level start/peak/end {level[0]/level.max():.2f}/1/{level[-1]/level.max():.2f}")


if __name__ == "__main__":
    main()
