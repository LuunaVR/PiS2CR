"""Turn the transcribed reference notes into game inputs, using the calibration of which input makes which pitch.

Run after transcribe.py and calibrate.py:  python fun/ocarina/score.py
Reads fun/ocarina/out/reference_notes.json and calibration.json, writes fun/ocarina/out/ocarina_score.json.
Each note becomes a button (Y X A L R) and a left-stick state (neutral, up, down: up and down bend the pitch).
"""
import json
from pathlib import Path

import numpy as np

HERE = Path(__file__).parent
BUTTON_LABELS = {"Y": "Y", "X": "X", "A": "A", "L": "LB", "R": "RB"}
STICK_Y = {"neutral": 0.0}
MAX_ERROR_SEMITONES = 0.15
BEND_COST = 0.3          # prefer a plain button press over a bent one
STICK_CHANGE_COST = 1.0  # prefer to leave the stick alone between notes
ERROR_COST = 40.0        # per semitone of pitch error
MAX_BEND = 2.0           # the stick bends a note by at most 2 semitones either way
MIN_BEND = 0.04


def load_inputs():
    results = json.loads((HERE / "out" / "calibration.json").read_text())["results"]
    return [r for r in results if "error" not in r and r["stick"] in STICK_Y]


def load_bends():
    """Per button: neutral pitch and the fitted bend curve (semitones = slope * (|y| - dead zone)) from stick_sweep.py."""
    path = HERE / "out" / "stick_sweep.json"
    if not path.exists():
        return {}
    rows = [r for r in json.loads(path.read_text()) if "midi" in r]
    bends = {}
    for button in {r["button"] for r in rows}:
        mine = [r for r in rows if r["button"] == button]
        neutral = next(r["midi"] for r in mine if r["y"] == 0.0)
        fit = [(abs(r["y"]), abs(r["midi"] - neutral)) for r in mine if 0.25 <= abs(r["y"]) <= 0.75]
        slope, offset = np.polyfit([f[0] for f in fit], [f[1] for f in fit], 1)
        bends[button] = {"neutral": neutral, "slope": float(slope), "dead": float(-offset / slope)}
    return bends


def candidates(midi, inputs, bends=None):
    found = []
    for r in inputs:
        error = r["midi"] - midi
        if abs(error) <= MAX_ERROR_SEMITONES:
            found.append({"button": r["button"], "stick": "neutral", "y": 0.0, "midi": r["midi"], "hz": r["hz"],
                          "error_cents": round(error * 100), "cost": ERROR_COST * abs(error)})
    for button, b in (bends or {}).items():
        bend = midi - b["neutral"]
        if MIN_BEND <= abs(bend) <= MAX_BEND + MAX_ERROR_SEMITONES:
            reach = min(abs(bend), MAX_BEND)   # past the stick's limit the note stays at the limit pitch
            sign = float(np.sign(bend))
            y = round(sign * (reach / b["slope"] + b["dead"]), 2)
            error = sign * reach - bend
            found.append({"button": button, "stick": f"{y:+.2f}", "y": y, "midi": b["neutral"] + sign * reach,
                          "hz": 440 * 2 ** ((midi - 69) / 12), "error_cents": round(error * 100),
                          "cost": BEND_COST + 0.2 * abs(y) + ERROR_COST * abs(error)})
    return found


def best_path(options):
    """The cheapest choice for every note, counting stick changes between neighbours (dynamic programming)."""
    costs = [[o["cost"] for o in options[0]]]
    back = [[None] * len(options[0])]
    for i in range(1, len(options)):
        row, links = [], []
        for option in options[i]:
            totals = [costs[i - 1][j] + (0 if prev["stick"] == option["stick"] else STICK_CHANGE_COST)
                      for j, prev in enumerate(options[i - 1])]
            j = min(range(len(totals)), key=totals.__getitem__)
            row.append(totals[j] + option["cost"])
            links.append(j)
        costs.append(row)
        back.append(links)
    j = min(range(len(costs[-1])), key=costs[-1].__getitem__)
    path = [j]
    for i in range(len(options) - 1, 0, -1):
        j = back[i][j]
        path.append(j)
    path.reverse()
    return [options[i][j] for i, j in enumerate(path)]


def main():
    inputs = load_inputs()
    bends = load_bends()
    notes = json.loads((HERE / "out" / "reference_notes.json").read_text())["notes"]
    options = [candidates(n["midi"], inputs, bends) for n in notes]
    missing = [n["index"] for n, o in zip(notes, options) if not o]
    if missing:
        raise SystemExit(f"No input within {MAX_ERROR_SEMITONES} semitones for notes {missing}")
    chosen = best_path(options)
    score = []
    for note, pick in zip(notes, chosen):
        score.append({"start": note["start"], "end": note["end"], "button": pick["button"], "stick_y": pick["y"],
                      "stick": pick["stick"], "target_midi": note["midi"], "played_midi": pick["midi"],
                      "error_cents": pick["error_cents"]})
    out = HERE / "out" / "ocarina_score.json"
    out.write_text(json.dumps({"seconds": notes[-1]["end"], "notes": score}, indent=1))
    worst = max(abs(s["error_cents"]) for s in score)
    changes = sum(1 for a, b in zip(score, score[1:]) if a["stick"] != b["stick"])
    print(f"{len(score)} notes -> {out}; worst pitch error {worst} cents; {changes} stick changes")
    line = []
    for s in score:
        mark = "" if s["stick"] == "neutral" else f"({s['stick']})"
        line.append(f"{BUTTON_LABELS[s['button']]}{mark}")
    print(" ".join(line))


if __name__ == "__main__":
    main()
