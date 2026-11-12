"""Play a macro file on the Pi and record the game's sound from the Virtual Audio Cable; print every sound heard.

Only the (Virtual Audio Cable) recording device is opened (see calibrate.py). Times are relative to the first sound heard,
so a macro that starts with a known tap shows the path delay for free.
Run:  python fun/ocarina/record_macro.py macro.json name [seconds-to-wait]
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


def main():
    path, name = Path(sys.argv[1]), sys.argv[2]
    macro = json.loads(path.read_text())
    cal.post("/macro/save", {"name": name, "macro": macro})
    index, info, api = cal.find_device()
    print(f"Recording from: {info['name']} ({api}, {int(info['default_samplerate'])} Hz)")
    play_time = macro["steps"][-1]["t"] / macro.get("speed", 1.0)
    with cal.Recorder(index, info) as recorder:
        time.sleep(1.0)
        cal.post("/macro/play", {"name": name})
        time.sleep(play_time + 2.0)
    samples = recorder.samples()
    target = HERE / "audio" / f"{name}.wav"
    sf.write(target, samples, recorder.rate)
    segments = cal.tone_segments(samples, recorder.rate, min_length=0.05)
    print(f"recorded {len(samples) / recorder.rate:.1f} s -> {target}; {len(segments)} sounds heard")
    if segments:
        first = segments[0][0]
        for start, end in segments:
            print(f"  {start - first:6.2f} - {end - first:6.2f}  ({end - start:.2f} s)")


if __name__ == "__main__":
    main()
