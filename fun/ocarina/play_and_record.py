"""Upload a macro to the Pi, play it, record the game's sound from the Virtual Audio Cable, and compare it to the reference.

Only the (Virtual Audio Cable) recording device is ever opened (see calibrate.py). The game must be open; the full macro
takes the ocarina out itself, so start with it put away.
Run:  python fun/ocarina/play_and_record.py [macro.json] [name] [speed-percent]
"""
import json
import sys
import time
from pathlib import Path

import numpy as np
import soundfile as sf

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import calibrate as cal
import compare


def main():
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "out" / "roll_macro.json"
    name = sys.argv[2] if len(sys.argv) > 2 else "roll"
    speed = float(sys.argv[3]) / 100 if len(sys.argv) > 3 else 1.0
    macro = json.loads(path.read_text())
    cal.post("/macro/save", {"name": name, "macro": macro})
    cal.post("/macro/speed", {"name": name, "speed": speed})
    index, info, api = cal.find_device()
    print(f"Recording from: {info['name']} ({api}, {int(info['default_samplerate'])} Hz); speed {speed * 100:g}%")
    play_time = macro["steps"][-1]["t"] / speed
    with cal.Recorder(index, info) as recorder:
        time.sleep(1.0)
        cal.post("/macro/play", {"name": name})
        time.sleep(play_time + 2.0)
    samples = recorder.samples()
    target = HERE / "audio" / "playback.wav"
    sf.write(target, samples, recorder.rate)
    print(f"recorded {len(samples) / recorder.rate:.1f} s, peak {np.abs(samples).max():.3f} -> {target}")
    compare.report(compare.compare(target, HERE / "out" / "reference_notes.json", speed))


if __name__ == "__main__":
    main()
