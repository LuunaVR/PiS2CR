"""Turn any mp3 into an ocarina macro, using the existing calibration (no playing in the game is needed).

Run:  python fun/ocarina/make_song.py song.mp3 --name say_so --label "Say So" [--start 10 --end 40] [--bpm 111] [--upload]
Steps: decode, find the tempo grid, pick the dominant melody pitch per 16th note (harmonic summation on a CQT,
smoothed with a Viterbi pass), fold every note into the ocarina range (octave choice keeps jumps small), map notes to
button + stick with score.py, and write out/<name>_macro.json, out/<name>_notes.json and out/<name>_preview.wav.
Quality depends on the song: a clear lead (vocals, solo instrument) works best; dense mixes give an approximate line.
"""
import argparse
import json
import subprocess
import urllib.request
from pathlib import Path

import imageio_ffmpeg
import librosa
import numpy as np
import soundfile as sf

import export_macro
import score
import synth

HERE = Path(__file__).parent
SR, HOP = 22050, 256
CQT_BINS_PER_SEMITONE, CQT_TOP = 3, 112
HARMONICS = [(0, 1.0), (36, 0.75), (57, 0.5), (72, 0.35)]   # bins above the fundamental at 36 bins per octave
DEFAULT_RANGE = (60, 93)      # midi range of the line to follow; the bass is about (28, 59)
PLAY_RANGE = (72.5, 88.0)     # the ocarina covers 72.25 to 88.25 continuously with the stick
SWITCH_COST = 0.25            # Viterbi penalty for changing pitch class between 16ths
SAME_BUTTON_GAP_S = 0.09      # the game needs about 85 ms between two presses of one button
SILENCE_LEVEL = 0.15          # of the 90th percentile level
ONSET_PERCENTILE = 85         # a repeat of the same pitch is re-struck on onsets above this


def decode(mp3, start, end):
    wav = HERE / "audio" / "song_decoded.wav"
    command = [imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error", "-i", str(mp3), "-ac", "1", "-ar", str(SR)]
    if start:
        command += ["-ss", str(start)]
    if end:
        command += ["-to", str(end)]
    subprocess.run(command + [str(wav)], check=True)
    samples, _ = librosa.load(str(wav), sr=SR)
    return samples


def fit_grid(onset, frame_t, duration, bpm=None):
    """Best (16th-note length, first 16th time): the grid whose points land on the strongest onsets."""
    def grid_score(step, offset):
        return float(np.interp(np.arange(offset, duration, step), frame_t, onset).mean())

    def best_offset(step, resolution):
        return max(np.arange(0, step, resolution), key=lambda o: grid_score(step, o))

    if bpm:
        step = 15 / bpm
        return step, best_offset(step, 0.002)
    coarse = max(np.arange(90, 140, 0.1), key=lambda b: grid_score(15 / b, best_offset(15 / b, 0.01)))
    fine = max(np.arange(coarse - 0.15, coarse + 0.15, 0.01), key=lambda b: grid_score(15 / b, best_offset(15 / b, 0.004)))
    step = 15 / fine
    return step, best_offset(step, 0.002)


def salience(harmonic, f0_range):
    """Per frame and per semitone in f0_range: harmonic-summed CQT strength. Returns (tuning, array[semitone, frame])."""
    low, high = f0_range
    tuning = float(librosa.estimate_tuning(y=harmonic, sr=SR, bins_per_octave=12))
    bins = CQT_BINS_PER_SEMITONE * min(high - low + 25, CQT_TOP - low)
    cqt = np.abs(librosa.cqt(harmonic, sr=SR, hop_length=HOP, fmin=librosa.midi_to_hz(low + tuning), n_bins=bins,
                             bins_per_octave=12 * CQT_BINS_PER_SEMITONE))
    cqt = np.sqrt(cqt / (cqt.max() + 1e-9))
    rows = []
    for semitone in range(low, high + 1):
        base = CQT_BINS_PER_SEMITONE * (semitone - low)
        total = np.zeros(cqt.shape[1])
        for offset, weight in HARMONICS:
            if base + offset < bins:
                total += weight * cqt[base + offset]
        rows.append(total)
    return tuning, np.array(rows)


def pick_pitch_classes(slot_s, low):
    """Viterbi over 12 pitch classes per slot, so a chord's tones do not make the line flicker."""
    rows_of = [[i for i in range(slot_s.shape[0]) if (low + i) % 12 == c] for c in range(12)]
    pc = np.stack([slot_s[rows].max(axis=0) for rows in rows_of])   # [12, slots]
    emit = pc / (pc.max(axis=0, keepdims=True) + 1e-9)
    n = emit.shape[1]
    best = np.zeros((n, 12))
    back = np.zeros((n, 12), int)
    best[0] = emit[:, 0]
    for k in range(1, n):
        stay = best[k - 1]
        top = int(np.argmax(stay))
        for c in range(12):
            from_other = stay[top] - SWITCH_COST
            if stay[c] >= from_other:
                best[k, c], back[k, c] = stay[c] + emit[c, k], c
            else:
                best[k, c], back[k, c] = from_other + emit[c, k], top
    path = [int(np.argmax(best[-1]))]
    for k in range(n - 1, 0, -1):
        path.append(int(back[k, path[-1]]))
    return path[::-1]


def extract_notes(samples, bpm=None, f0_range=DEFAULT_RANGE):
    low = f0_range[0]
    harmonic, _ = librosa.effects.hpss(samples)
    duration = len(samples) / SR
    onset_all = librosa.onset.onset_strength(y=samples, sr=SR, hop_length=HOP)
    onset_harm = librosa.onset.onset_strength(y=harmonic, sr=SR, hop_length=HOP)
    frame_t = librosa.times_like(onset_all, sr=SR, hop_length=HOP)
    step, offset = fit_grid(onset_all, frame_t, duration, bpm)
    tuning, sal = salience(harmonic, f0_range)
    n = int((duration - offset) / step)
    slot_s = np.zeros((sal.shape[0], n))
    strike = np.zeros(n)
    for k in range(n):
        t0 = offset + k * step
        window = (frame_t >= t0 + 0.2 * step) & (frame_t < t0 + step)
        if window.any():
            slot_s[:, k] = sal[:, window].mean(axis=1)
        near = (frame_t >= t0 - 0.03) & (frame_t < t0 + 0.4 * step)
        strike[k] = onset_harm[near].max() if near.any() else 0.0
    level = slot_s.max(axis=0)
    quiet = level < SILENCE_LEVEL * np.percentile(level, 90)
    classes = pick_pitch_classes(slot_s, low)
    strike_floor = np.percentile(strike, ONSET_PERCENTILE)
    notes = []
    for k in range(n):
        if quiet[k]:
            continue
        pc = classes[k]
        rows = [i for i in range(sal.shape[0]) if (low + i) % 12 == pc]
        original = low + max(rows, key=lambda i: slot_s[i, k])
        previous = notes[-1] if notes else None
        continues = (previous is not None and previous["end_slot"] == k and previous["pc"] == pc
                     and strike[k] < strike_floor)
        if continues:
            previous["end_slot"] = k + 1
        else:
            notes.append({"start_slot": k, "end_slot": k + 1, "pc": pc, "original": original})
    for note in notes:
        note["start"] = round(offset + note["start_slot"] * step, 4)
        note["end"] = round(offset + note["end_slot"] * step, 4)
    return notes, {"step": step, "bpm": 15 / step, "offset": offset, "tuning": tuning, "slots": n,
                   "quiet_slots": int(quiet.sum())}


def fold(notes):
    """Place every note in the ocarina range; the cheapest path keeps jumps small and stays near the original octave."""
    lo, hi = PLAY_RANGE
    options = []
    for note in notes:
        found = [p for p in range(int(np.ceil(lo)), int(hi) + 1) if p % 12 == note["pc"]]
        options.append(found)
    cost = [[0.15 * abs(p - notes[0]["original"]) for p in options[0]]]
    back = [[0] * len(options[0])]
    for i in range(1, len(notes)):
        row, links = [], []
        for p in options[i]:
            totals = [cost[i - 1][j] + abs(p - q) for j, q in enumerate(options[i - 1])]
            j = int(np.argmin(totals))
            row.append(totals[j] + 0.15 * abs(p - notes[i]["original"]))
            links.append(j)
        cost.append(row)
        back.append(links)
    j = int(np.argmin(cost[-1]))
    path = [j]
    for i in range(len(notes) - 1, 0, -1):
        j = back[i][j]
        path.append(j)
    path.reverse()
    for note, opts, j in zip(notes, options, path):
        note["midi"] = opts[j]


def map_to_inputs(notes):
    inputs, bends = score.load_inputs(), score.load_bends()
    options = [score.candidates(n["midi"], inputs, bends) for n in notes]
    missing = [i for i, o in enumerate(options) if not o]
    if missing:
        raise SystemExit(f"No input for notes {missing}")
    chosen = score.best_path(options)
    played = []
    for note, pick in zip(notes, chosen):
        played.append({"start": note["start"], "end": note["end"], "button": pick["button"], "stick_y": pick["y"],
                       "stick": pick["stick"], "target_midi": note["midi"], "error_cents": pick["error_cents"]})
    for note, following in zip(played, played[1:]):
        if following["start"] - note["end"] < 0.0005 and following["button"] == note["button"]:
            note["end"] = round(max(following["start"] - SAME_BUTTON_GAP_S, note["start"] + 0.04), 4)
    return played


def upload(name, macro):
    body = json.dumps({"name": name, "macro": macro}).encode()
    request = urllib.request.Request("http://127.0.0.1:8000/macro/save", body, {"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=10) as reply:
        print("saved on the Pi:", reply.read().decode())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mp3")
    parser.add_argument("--name", default="song")
    parser.add_argument("--label", default=None)
    parser.add_argument("--start", type=float, default=0.0)
    parser.add_argument("--end", type=float, default=0.0)
    parser.add_argument("--bpm", type=float, default=None)
    parser.add_argument("--range", type=int, nargs=2, default=DEFAULT_RANGE, metavar=("LOW", "HIGH"),
                        help="midi range of the line to follow, e.g. 28 59 for the bass")
    parser.add_argument("--upload", action="store_true")
    args = parser.parse_args()
    label = (args.label or args.name)[:40]

    samples = decode(args.mp3, args.start, args.end)
    notes, info = extract_notes(samples, args.bpm, tuple(args.range))
    print(f"tempo {info['bpm']:.2f} bpm (16th {info['step'] * 1000:.0f} ms), tuning {info['tuning']:+.2f} semitone, "
          f"{info['slots']} slots, {info['quiet_slots']} quiet, {len(notes)} notes")
    fold(notes)
    played = map_to_inputs(notes)
    steps = export_macro.build_steps(played)
    macro = {"label": label, "min_release": export_macro.MIN_RELEASE, "steps": steps}
    out = HERE / "out"
    (out / f"{args.name}_macro.json").write_text(json.dumps(macro))
    (out / f"{args.name}_notes.json").write_text(json.dumps({"notes": [
        {"start": n["start"], "end": n["end"], "midi": n["target_midi"]} for n in played]}, indent=1))
    sf.write(out / f"{args.name}_preview.wav",
             synth.render([{"start": n["start"] - played[0]["start"], "end": n["end"] - played[0]["start"],
                            "midi": n["target_midi"]} for n in played], played[-1]["end"] - played[0]["start"]), synth.RATE)
    changes = sum(1 for a, b in zip(played, played[1:]) if a["stick"] != b["stick"])
    worst = max(abs(n["error_cents"]) for n in played)
    print(f"{len(steps)} steps, {steps[-1]['t']:.1f} s, worst pitch error {worst} cents, {changes} stick changes")
    print("macro:", out / f"{args.name}_macro.json", "| preview:", out / f"{args.name}_preview.wav")
    if args.upload:
        upload(args.name, macro)


if __name__ == "__main__":
    main()
