"""Make a macro that closes the ocarina (B), takes it out again (D-Pad Left) and taps A repeatedly, to see when it is ready.

Run:  python fun/ocarina/readiness_macro.py   then   python fun/ocarina/record_macro.py fun/ocarina/out/readiness_macro.json ready-test
The first tap (ocarina already out) shows the path delay; the taps after taking it out show when notes start to sound.
"""
import json
from pathlib import Path

HERE = Path(__file__).parent


def build():
    steps = [{"t": 0.0, "buttons": ["A"]}, {"t": 0.3, "buttons": []},
             {"t": 1.0, "buttons": ["B"]}, {"t": 1.12, "buttons": []},
             {"t": 3.5, "buttons": ["LEFT"]}, {"t": 3.62, "buttons": []}]
    for i in range(24):
        start = 3.7 + i * 0.22
        steps += [{"t": round(start, 3), "buttons": ["A"]}, {"t": round(start + 0.12, 3), "buttons": []}]
    return {"label": "Ocarina readiness test", "steps": steps}


if __name__ == "__main__":
    out = HERE / "out" / "readiness_macro.json"
    out.write_text(json.dumps(build()))
    print(out)
