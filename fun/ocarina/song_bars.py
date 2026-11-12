"""Print a notes json one bar per line (16th grid), for a quick look at the line a macro will play."""
import json
import sys

NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def main():
    data = json.load(open(sys.argv[1]))["notes"]
    bpm, first, bars = float(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4])
    step = 15 / bpm
    t0 = data[0]["start"]
    for bar in range(first, first + bars):
        cells = []
        for k in range(16):
            t = t0 + (bar * 16 + k) * step + step / 2
            hit = next((n for n in data if n["start"] <= t < n["end"]), None)
            cells.append("." if hit is None else NAMES[hit["midi"] % 12] + str(hit["midi"] // 12 - 1))
        print(f"{bar:3d}: " + " ".join(f"{c:3s}" for c in cells))


if __name__ == "__main__":
    main()
