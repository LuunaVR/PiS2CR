"""Turn the reference ocarina clip into a note list: pitch, start and length of every note.

Run from the repo root:  python fun/ocarina/transcribe.py [audio.wav]
Writes fun/ocarina/out/reference_notes.json and prints a table.
"""
import json
import sys
from pathlib import Path

import numpy as np
import librosa

sys.path.insert(0, str(Path(__file__).parent))
import audio_tools as at

HERE = Path(__file__).parent
FMIN, FMAX = 200.0, 1800.0       # the ocarina's range, with room to spare
HOP = 256                         # about 5.8 ms at 44.1 kHz
MIN_NOTE_S = 0.04                 # anything shorter is a glitch, not a note
SPLIT_CENTS = 60                  # a pitch jump this big starts a new note
MERGE_GAP_S = 0.03                # a dropout this short inside one steady pitch is not a new note


def midi_name(midi):
    names = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
    midi = int(round(midi))
    return f"{names[midi % 12]}{midi // 12 - 1}"


def track_pitch(samples, rate, fmin=FMIN, fmax=FMAX):
    f0, voiced, prob = librosa.pyin(samples, fmin=fmin, fmax=fmax, sr=rate, frame_length=4096, hop_length=HOP,
                                    fill_na=np.nan)
    times = librosa.times_like(f0, sr=rate, hop_length=HOP)
    return times, f0, voiced, prob


def segment(times, f0, voiced):
    """Group consecutive voiced frames of steady pitch into notes."""
    midi = librosa.hz_to_midi(np.where(voiced & ~np.isnan(f0), f0, np.nan))
    notes, start, values = [], None, []

    def close(end_index):
        nonlocal start, values
        if start is not None and values:
            begin, end = times[start], times[end_index]
            if end - begin >= MIN_NOTE_S:
                notes.append({"start": round(float(begin), 3), "end": round(float(end), 3),
                              "midi": round(float(np.median(values)), 2)})
        start, values = None, []

    for i, value in enumerate(midi):
        if np.isnan(value):
            close(i)
            continue
        if start is not None and abs(value - np.median(values[-8:])) * 100 > SPLIT_CENTS:
            close(i)
        if start is None:
            start = i
        values.append(value)
    close(len(times) - 1)
    return merge_dropouts(notes)


def split_at_dips(samples, rate, notes, dip_ratio=0.4, min_part=0.05):
    """Two presses of one button have the same pitch, so only a dip in loudness separates them: split notes there."""
    from scipy import signal
    frame = int(0.005 * rate)
    result = []
    for note in notes:
        hz = 440.0 * 2 ** ((note["midi"] - 69) / 12)
        begin = max(0, int((note["start"] - 0.02) * rate))
        chunk = samples[begin:int((note["end"] + 0.02) * rate)]
        if len(chunk) < frame * 16:
            result.append(note)
            continue
        sos = signal.butter(3, [hz * 2 ** (-1 / 12), hz * 2 ** (1 / 12)], btype="band", fs=rate, output="sos")
        band = signal.sosfiltfilt(sos, chunk)
        count = len(band) // frame
        env = np.sqrt((band[:count * frame].reshape(count, frame) ** 2).mean(axis=1))
        env = np.convolve(env, np.ones(3) / 3, mode="same")
        cuts, i = [], 4
        while i < count - 4:
            window = env[i - 4:i + 5]
            if env[i] == window.min():
                left, right = env[:i].max(), env[i + 1:].max()
                ceiling = min(left, right)
                if env[i] < dip_ratio * ceiling:
                    lo = i
                    while lo > 0 and env[lo - 1] < 0.5 * ceiling:
                        lo -= 1
                    hi = i
                    while hi < count - 1 and env[hi + 1] < 0.5 * ceiling:
                        hi += 1
                    cuts.append((lo, hi))
                    i = hi + 1
                    continue
            i += 1
        if not cuts:
            result.append(note)
            continue
        edges = [(0, cuts[0][0])] + [(cuts[k][1], cuts[k + 1][0]) for k in range(len(cuts) - 1)] + [(cuts[-1][1], count)]
        for first, last in edges:
            start = begin / rate + first * frame / rate
            end = begin / rate + last * frame / rate
            if end - start >= min_part:
                result.append({**note, "start": round(max(start, note["start"]), 3), "end": round(min(end, note["end"]), 3)})
    return result


def fast_notes(samples, rate, window_s=0.02, hop_s=0.004, min_frames=3):
    """Notes from a strongest-tone track over short windows; pyin's 93 ms frames cannot resolve notes under about 60 ms.

    Same-pitch repeats are split where the level dips; a pitch jump of more than half a semitone starts a new note."""
    win, hop = int(window_s * rate), int(hop_s * rate)
    size = 8192
    freqs = np.fft.rfftfreq(size, 1 / rate)
    band = np.flatnonzero((freqs > 450) & (freqs < 1400))   # the game hums a low background tone
    taper = np.hanning(win)
    frames = []
    for start in range(0, len(samples) - win, hop):
        spectrum = np.abs(np.fft.rfft(samples[start:start + win] * taper, size))[band]
        k = int(np.argmax(spectrum))
        shift = 0.0
        if 0 < k < len(spectrum) - 1:
            a, b, c = np.log(spectrum[k - 1] + 1e-12), np.log(spectrum[k] + 1e-12), np.log(spectrum[k + 1] + 1e-12)
            shift = 0.5 * (a - c) / (a - 2 * b + c) if (a - 2 * b + c) else 0.0
        frames.append(((start + win / 2) / rate, float(freqs[band][k] + shift * (freqs[1] - freqs[0])), float(spectrum[k])))
    if not frames:
        return []
    levels = np.array([f[2] for f in frames])
    floor = float(np.percentile(levels, 98)) * 0.12
    notes, current = [], None

    def close():
        if current and len(current["f"]) >= min_frames:
            trim = len(current["f"]) // 5   # the first and last frames are transitions
            freq = float(np.median(current["f"][trim:len(current["f"]) - trim]))
            notes.append({"start": current["t0"] - hop_s / 2, "end": current["t1"] + hop_s / 2,
                          "midi": 69 + 12 * float(np.log2(freq / 440.0)), "freq": freq})

    for time_s, freq, level in frames:
        if level < floor:
            close()
            current = None
            continue
        if current and abs(12 * np.log2(freq / np.median(current["f"]))) > 0.5:
            close()
            current = None
        if current and level > current["peak"] * 0.7 and current["dipped"]:
            close()
            current = None
        if current is None:
            current = {"t0": time_s, "t1": time_s, "f": [freq], "peak": level, "dipped": False}
            continue
        current["t1"] = time_s
        current["f"].append(freq)
        if level < current["peak"] * 0.4:
            current["dipped"] = True
        else:
            current["peak"] = max(current["peak"], level)
    close()
    return notes


GLITCH_S = 0.06       # a tone shorter than this between notes is a transition, not a note
SAME_NOTE_GAP_S = 0.1  # a loudness dip this short inside one pitch is not a new press


def clean_fast_notes(notes):
    """Drop transition blips and rejoin one note split by a brief dip."""
    notes = [dict(n) for n in notes if n["end"] - n["start"] >= GLITCH_S]
    merged = []
    for note in notes:
        last = merged[-1] if merged else None
        if last and note["start"] - last["end"] <= SAME_NOTE_GAP_S and abs(note["midi"] - last["midi"]) < 0.5:
            if note["end"] - note["start"] > last["end"] - last["start"]:
                last["midi"], last["freq"] = note["midi"], note["freq"]
            last["end"] = note["end"]
        else:
            merged.append(note)
    return merged


def merge_dropouts(notes):
    merged = []
    for note in notes:
        last = merged[-1] if merged else None
        if last and note["start"] - last["end"] <= MERGE_GAP_S and abs(note["midi"] - last["midi"]) < 0.5:
            last["midi"] = round((last["midi"] + note["midi"]) / 2, 2)
            last["end"] = note["end"]
        else:
            merged.append(dict(note))
    return merged


def main():
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "audio" / "reference.wav"
    samples, rate = at.load_mono(path)
    notes = [n for n in clean_fast_notes(fast_notes(samples, rate)) if 70 <= n["midi"] <= 90]
    for index, note in enumerate(notes):
        note["start"], note["end"], note["midi"] = round(note["start"], 3), round(note["end"], 3), round(note["midi"], 2)
        note.pop("freq", None)
        note["length"] = round(note["end"] - note["start"], 3)
        note["name"] = midi_name(note["midi"])
        note["cents_off"] = round((note["midi"] - round(note["midi"])) * 100)
        note["index"] = index
    out = HERE / "out" / "reference_notes.json"
    out.write_text(json.dumps({"source": path.name, "seconds": round(len(samples) / rate, 2), "notes": notes}, indent=1))
    print(f"{len(notes)} notes in {len(samples) / rate:.2f} s -> {out}")
    for note in notes:
        print(f"{note['index']:3d}  {note['start']:6.2f}-{note['end']:6.2f}  {note['length']:5.2f}s  "
              f"{note['name']:>4}  {note['midi']:6.2f}  ({note['cents_off']:+d} c)")


if __name__ == "__main__":
    main()
