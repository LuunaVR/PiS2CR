"""Compare a recording of the game playing the song with the reference clip: pitch and timing of every note.

Run:  python fun/ocarina/compare.py [recording.wav]      (default fun/ocarina/audio/playback.wav)
The recording starts at an unknown moment, so the notes are lined up by the median start delay first.
"""
import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import audio_tools as at
import transcribe as t


def match(reference, heard, window=0.15):
    """Pair each reference note with the heard note nearest in time after removing the overall delay.

    Returns (pairs, delay): pairs are (reference index, heard index); delay is the median start difference in seconds."""
    if not heard:
        return [], 0.0
    ref_starts = np.array([n["start"] for n in reference])
    ref_midi = np.array([n["midi"] for n in reference])
    heard_starts = np.array([n["start"] for n in heard])
    heard_midi = np.array([n["midi"] for n in heard])

    def near(i, delay, limit):
        distances = np.abs(heard_starts - (ref_starts[i] + delay))
        distances[np.abs(heard_midi - ref_midi[i]) > 1.0] = np.inf   # a pairing must be the same pitch within a semitone
        j = int(np.argmin(distances))
        return j if distances[j] < limit else None

    # The delay is the offset that lines up the most notes of the same pitch; any heard note may be the first one.
    best = (-1, 0.0)
    for first in heard_starts:
        for anchor in range(min(3, len(ref_starts))):
            candidate = float(first - ref_starts[anchor])
            hits = sum(1 for i in range(len(ref_starts)) if near(i, candidate, 0.1) is not None)
            if hits > best[0]:
                best = (hits, candidate)
    pairs = []
    used = set()
    first_delay = best[1]
    for i in range(len(ref_starts)):
        j = near(i, first_delay, window)
        if j is not None and j not in used:
            pairs.append((i, j))
            used.add(j)
    delay = float(np.median([heard_starts[j] - ref_starts[i] for i, j in pairs])) if pairs else first_delay
    return pairs, delay


def compare(recording, reference_json, speed=1.0):
    samples, rate = at.load_mono(recording)
    heard = [n for n in t.fast_notes(samples, rate) if 70 <= n["midi"] <= 90 and n["end"] - n["start"] >= 0.02]   # only the ocarina's own range
    reference = [{**n, "start": n["start"] / speed, "end": n["end"] / speed}
                 for n in json.loads(Path(reference_json).read_text())["notes"]]
    pairs, delay = match(reference, heard)
    rows = []
    for i, j in pairs:
        ref, got = reference[i], heard[j]
        rows.append({"index": i, "start_error_ms": round((got["start"] - ref["start"] - delay) * 1000),
                     "end_error_ms": round((got["end"] - ref["end"] - delay) * 1000),
                     "pitch_error_cents": round((got["midi"] - ref["midi"]) * 100),
                     "length_error_ms": round(((got["end"] - got["start"]) - (ref["end"] - ref["start"])) * 1000)})
    missing = sorted(set(range(len(reference))) - {i for i, _ in pairs})
    extra = sorted(set(range(len(heard))) - {j for _, j in pairs})
    (HERE / "out" / "playback_notes.json").write_text(json.dumps({"delay_s": round(delay, 3), "notes": heard}, indent=1))
    return {"reference_notes": len(reference), "heard_notes": len(heard), "matched": len(pairs), "missing": missing,
            "extra": extra, "delay_ms": round(delay * 1000), "rows": rows}


def report(result):
    rows = result["rows"]
    print(f"heard {result['heard_notes']} notes for {result['reference_notes']} in the reference; matched {result['matched']}, "
          f"missing {result['missing']}, extra {result['extra']}")
    print(f"overall delay {result['delay_ms']} ms")
    if not rows:
        return
    start = np.abs([r["start_error_ms"] for r in rows])
    end = np.abs([r["end_error_ms"] for r in rows])
    pitch = np.abs([r["pitch_error_cents"] for r in rows])
    length = np.abs([r["length_error_ms"] for r in rows])
    print(f"start error   mean {start.mean():.0f} ms, worst {start.max():.0f} ms")
    print(f"end error     mean {end.mean():.0f} ms, worst {end.max():.0f} ms")
    print(f"length error  mean {length.mean():.0f} ms, worst {length.max():.0f} ms")
    print(f"pitch error   mean {pitch.mean():.0f} cents, worst {pitch.max():.0f} cents; "
          f"{int((pitch <= 25).sum())} of {len(pitch)} within 25 cents")
    worst = sorted(rows, key=lambda r: -abs(r["start_error_ms"]) - abs(r["pitch_error_cents"]))[:5]
    for r in worst:
        print(f"  note {r['index']:2d}: start {r['start_error_ms']:+d} ms, end {r['end_error_ms']:+d} ms, pitch {r['pitch_error_cents']:+d} c")


def main():
    recording = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "audio" / "playback.wav"
    report(compare(recording, HERE / "out" / "reference_notes.json"))


if __name__ == "__main__":
    main()
