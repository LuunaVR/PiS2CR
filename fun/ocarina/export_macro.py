"""Turn the input score into a macro the relay can play: the exact button and stick state at each moment.

Run after score.py:  python fun/ocarina/export_macro.py [--bare]
Writes fun/ocarina/out/roll_macro.json. By default the macro takes the ocarina out first (D-Pad Left), plays, and puts it
away (B). The ocarina must be away when it starts. --bare skips both and starts at the first note.
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).parent
TAKE_OUT, PUT_AWAY = "LEFT", "B"
PRESS = 0.12          # how long D-Pad Left and B are held
READY_AFTER = 0.55    # the ocarina was measured ready 0.2 to 0.42 s after the press
TAIL = 0.5            # let the last note finish ringing before putting it away
MIN_RELEASE = 0.085   # the game needs about 80 ms between two presses of one button to hear two notes


def build_steps(notes, wrap=True):
    shift = READY_AFTER if wrap else 0.0
    steps = []
    if wrap:
        steps += [{"t": 0.0, "buttons": [TAKE_OUT], "fixed": True}, {"t": PRESS, "buttons": [], "fixed": True}]
    base = notes[0]["start"]
    for i, note in enumerate(notes):
        steps.append({"t": round(note["start"] - base + shift, 4), "buttons": [note["button"]], "left": [0, note["stick_y"]]})
        following = notes[i + 1] if i + 1 < len(notes) else None
        # A note that runs straight into the next one needs no release in between; the next step replaces it.
        if following is None or following["start"] > note["end"] + 0.0005:
            steps.append({"t": round(note["end"] - base + shift, 4), "buttons": [], "left": [0, 0]})
    if wrap:
        steps[-1]["fixed"] = True   # the wait before putting the ocarina away is real time, whatever the speed
        end = steps[-1]["t"] + TAIL
        steps += [{"t": round(end, 4), "buttons": [PUT_AWAY], "fixed": True}, {"t": round(end + PRESS, 4), "buttons": []}]
    return steps


def main():
    wrap = "--bare" not in sys.argv
    notes = json.loads((HERE / "out" / "ocarina_score.json").read_text())["notes"]
    steps = build_steps(notes, wrap)
    macro = {"label": "Rickroll" if wrap else "Rickroll (notes only)", "min_release": MIN_RELEASE, "steps": steps}
    out = HERE / "out" / ("roll_macro.json" if wrap else "roll_bare_macro.json")
    out.write_text(json.dumps(macro))
    print(f"{len(notes)} notes -> {len(steps)} steps, {steps[-1]['t']:.2f} s -> {out}")


if __name__ == "__main__":
    main()
