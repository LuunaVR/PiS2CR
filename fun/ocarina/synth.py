"""Play a note list as a clean ocarina-like tone, to check a transcription by ear.

Run:  python fun/ocarina/synth.py [notes.json] [out.wav]
Defaults: fun/ocarina/out/reference_notes.json -> fun/ocarina/out/reference_transcription_preview.wav
"""
import json
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

HERE = Path(__file__).parent
RATE = 44100


def render(notes, seconds, rate=RATE):
    out = np.zeros(int((seconds + 0.5) * rate), np.float32)
    for note in notes:
        hz = 440.0 * 2 ** ((note["midi"] - 69) / 12)
        start, end = int(note["start"] * rate), int(note["end"] * rate)
        t = np.arange(end - start) / rate
        tone = np.sin(2 * np.pi * hz * t) + 0.15 * np.sin(4 * np.pi * hz * t)
        fade = min(len(t) // 4, int(0.012 * rate))
        envelope = np.ones(len(t))
        if fade:
            envelope[:fade] = np.linspace(0, 1, fade)
            envelope[-fade:] = np.linspace(1, 0, fade)
        out[start:end] += (0.4 * tone * envelope).astype(np.float32)
    return out


def main():
    source = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "out" / "reference_notes.json"
    target = Path(sys.argv[2]) if len(sys.argv) > 2 else HERE / "out" / "reference_transcription_preview.wav"
    data = json.loads(source.read_text())
    sf.write(target, render(data["notes"], data["notes"][-1]["end"]), RATE)
    print(f"{len(data['notes'])} notes -> {target}")


if __name__ == "__main__":
    main()
