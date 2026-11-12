"""Exploration: pyin on the harmonic part of a song, printed as note events."""
import sys

import librosa
import numpy as np

NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def name(m):
    m = int(round(m))
    return f"{NAMES[m % 12]}{m // 12 - 1}"


def main():
    start, end = float(sys.argv[1]), float(sys.argv[2])
    fmin = sys.argv[3] if len(sys.argv) > 3 else "C4"
    y, sr = librosa.load("fun/ocarina/audio/song_probe.wav", sr=22050, offset=start, duration=end - start)
    harm, _ = librosa.effects.hpss(y)
    hop = 256
    f0, voiced, prob = librosa.pyin(harm, fmin=librosa.note_to_hz(fmin), fmax=librosa.note_to_hz("E6"), sr=sr, frame_length=2048, hop_length=hop)
    t = librosa.times_like(f0, sr=sr, hop_length=hop) + start
    print(f"voiced {np.mean(voiced):.0%}")
    cur, begin, vals = None, 0, []
    for i in range(len(f0) + 1):
        m = round(float(librosa.hz_to_midi(f0[i]))) if i < len(f0) and voiced[i] and not np.isnan(f0[i]) else None
        if m != cur:
            if cur is not None and i - begin >= 3:
                print(f"{t[begin]:7.2f}-{t[i - 1]:7.2f} {name(cur):4s} ({cur})")
            cur, begin = m, i


if __name__ == "__main__":
    main()
