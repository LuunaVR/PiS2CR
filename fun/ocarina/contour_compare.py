"""Show, for chosen notes, the pitch in 20 ms steps in the reference and in the last recording (offset in cents from the target).

Run:  python fun/ocarina/contour_compare.py 18 19 31 [--rec audio/playback.wav] [--speed 1]
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import audio_tools as at
from contour import track


def main():
    args = sys.argv[1:]
    speed = float(args[args.index("--speed") + 1]) if "--speed" in args else 1.0
    rec = Path(args[args.index("--rec") + 1]) if "--rec" in args else HERE / "audio" / "playback.wav"
    wanted = [int(a) for a in args if a.isdigit()]
    notes = json.loads((HERE / "out" / "reference_notes.json").read_text())["notes"]
    delay = json.loads((HERE / "out" / "playback_notes.json").read_text())["delay_s"]
    ref_audio, ref_rate = at.load_mono(HERE / "audio" / "reference.wav")
    got_audio, got_rate = at.load_mono(rec)
    for i in wanted:
        n = notes[i]
        start, end = n["start"] / speed, n["end"] / speed
        a = track(ref_audio, ref_rate, n["start"], n["end"])
        b = track(got_audio, got_rate, start + delay - 0.02, end + delay + 0.02)
        print(f"note {i} {n['name']} target {n['midi']:.2f}, {n['start']:.2f}-{n['end']:.2f}")
        print("  ref ", " ".join(f"{(p[1] - n['midi']) * 100:+.0f}" for p in a))
        print("  game", " ".join(f"{(p[1] - n['midi']) * 100:+.0f}" for p in b))


if __name__ == "__main__":
    main()
