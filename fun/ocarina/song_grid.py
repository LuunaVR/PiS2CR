"""Exploration: strongest pitch per 8th note on a tempo grid, one bar per line, to see the repeating riff."""
import sys

import librosa
import numpy as np

NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
SR, HOP = 22050, 256


def main():
    bpm = float(sys.argv[1])
    first_bar, bars = int(sys.argv[2]), int(sys.argv[3])
    fmin = sys.argv[4] if len(sys.argv) > 4 else "C4"
    y, _ = librosa.load("fun/ocarina/audio/song_probe.wav", sr=SR)
    harm, _ = librosa.effects.hpss(y)
    onset = librosa.onset.onset_strength(y=y, sr=SR, hop_length=HOP)
    beat = 60 / bpm
    frame_t = librosa.times_like(onset, sr=SR, hop_length=HOP)
    best = max(np.arange(0, beat, 0.005), key=lambda o: np.interp(np.arange(o, len(y) / SR, beat), frame_t, onset).sum())
    print(f"beat {beat:.3f} s, first beat at {best:.3f} s")
    lo = librosa.note_to_midi(fmin)
    nbins = 88 - lo
    cqt = np.abs(librosa.cqt(harm, sr=SR, hop_length=HOP, fmin=librosa.midi_to_hz(lo), n_bins=nbins, bins_per_octave=12))
    cqt = librosa.amplitude_to_db(cqt, ref=np.max)
    ct = librosa.times_like(cqt[0], sr=SR, hop_length=HOP)
    for bar in range(first_bar, first_bar + bars):
        cells = []
        for k in range(8):
            t0 = best + (bar * 4 + k / 2) * beat
            sel = (ct >= t0 + 0.02) & (ct < t0 + beat / 2 - 0.02)
            col = cqt[:, sel].mean(axis=1)
            top = np.argsort(col)[::-1][:2]
            cells.append("/".join(f"{NAMES[(lo + t) % 12]}{(lo + t) // 12 - 1}" for t in top))
        print(f"bar {bar:3d} ({best + bar * 4 * beat:6.1f}s): " + "  ".join(f"{c:7s}" for c in cells))


if __name__ == "__main__":
    main()
