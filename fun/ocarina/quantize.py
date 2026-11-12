"""Fit a rhythm grid to the transcribed notes: tempo, and each note's start and length in sixteenth notes.

Run after transcribe.py:  python fun/ocarina/quantize.py
Reads fun/ocarina/out/reference_notes.json, writes fun/ocarina/out/reference_score.json.
"""
import json
from pathlib import Path

import numpy as np

HERE = Path(__file__).parent


def fit_grid(onsets, low=0.120, high=0.145, steps=4000):
    """The grid step (seconds per sixteenth) and offset that put every onset closest to a grid line.

    The range is limited to a sensible tempo (104 to 125 BPM): a finer grid would always fit better."""
    best = None
    for step in np.linspace(low, high, steps):
        phase = (onsets / step) % 1.0
        # Circular mean of the fractional position gives the best offset for this step.
        angle = np.angle(np.exp(2j * np.pi * phase).mean())
        offset = (angle / (2 * np.pi)) * step
        residual = ((onsets - offset) / step)
        error = float(np.abs(residual - np.round(residual)).mean() * step)
        if best is None or error < best[0]:
            best = (error, step, offset)
    return best


def main():
    data = json.loads((HERE / "out" / "reference_notes.json").read_text())
    notes = data["notes"]
    onsets = np.array([n["start"] for n in notes])
    error, step, offset = fit_grid(onsets)
    print(f"sixteenth = {step * 1000:.1f} ms  ->  {15 / step:.1f} BPM, grid offset {offset * 1000:.0f} ms, "
          f"mean onset error {error * 1000:.1f} ms")
    score = []
    worst = 0.0
    for note in notes:
        start_step = int(round((note["start"] - offset) / step))
        end_step = int(round((note["end"] - offset) / step))
        worst = max(worst, abs(note["start"] - (offset + start_step * step)))
        score.append({"index": note["index"], "name": note["name"], "midi": note["midi"],
                      "start_step": start_step, "steps": max(1, end_step - start_step),
                      "start": note["start"], "end": note["end"]})
    print(f"largest onset error {worst * 1000:.0f} ms")
    out = HERE / "out" / "reference_score.json"
    out.write_text(json.dumps({"step_seconds": round(step, 5), "offset_seconds": round(offset, 4), "notes": score}, indent=1))
    print(f"{len(score)} notes -> {out}")
    line, last_end = [], None
    for note in score:
        if last_end is not None and note["start_step"] - last_end >= 3:
            print("  ".join(line))
            line = []
        line.append(f"{note['name']}[{note['steps']}]")
        last_end = note["start_step"] + note["steps"]
    print("  ".join(line))


if __name__ == "__main__":
    main()
