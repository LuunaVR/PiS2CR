# Chat Control and Macros

Reference for the Twitch tab and the Fun tab. Setup is in the [README](../README.md).

## Chat control

Chat control is **off by default**.

| Mode | Behaviour |
|---|---|
| Open | Anyone in chat can send commands. |
| Redeem | Chat gets control while a redeem's timer runs. |
| Off | Nobody can control anything, and anything chat is holding is released. |

**Redeems:** times are not added. A redeem is accepted only if it gives more time than is left, and then the timer is set to that reward's time. Any other redeem (or any redeem while the mode is Off or Open) is refunded after two seconds. Redeems made while the Pi was off are refunded, not honoured. Open mode and the Others group are never restored after a reboot.

**Who can send commands:** Everyone (default), VIPs (VIPs and you, plus the Listed Users list) or Listed Users Only. The streamer is recognised by Twitch's broadcaster badge, VIPs by the VIP badge, and listed users by Twitch username. Redeems still work for anyone, and the dashboard's test console is never restricted. Messages from people who are not allowed are ignored without a log entry. An external bot using `/twitch/event` can send `badges` (a list such as `["vip"]`) and `login` with each message.

### Commands

A message is one command, optionally followed by seconds: `left`, `left 1.5`, `dl .5`. The time can also follow the name directly: `y5`, `left1.5`. The shortest hold is 0.05 s. A longer ask is shortened to the Longest Hold setting (default 3 s). Anything else in the message and the whole message is ignored.

| Group | Commands (short forms after the slash) | Default |
|---|---|---|
| Move (left stick) | `forward / f / fwd / forwards`, `back / bk / backward / backwards`, `left / l`, `right / r` | On |
| Look (right stick) | `lookup / lu`, `lookdown / ld`, `lookleft / ll`, `lookright / lr` | On |
| Face Buttons | `a`, `b`, `x`, `y` | On |
| D-Pad | `dpadup / dup / du`, `dpaddown / ddown / dd`, `dpadleft / dleft / dl`, `dpadright / dright / dr` | On |
| Bumpers and Triggers | `leftbumper / lb`, `rightbumper / rb`, `zl / lt / lefttrigger`, `zr / rt / righttrigger` | On |
| Stick Clicks | `ls`, `rs` | On |
| Others | `plus / start`, `minus / select`, `home`, `capture` | Off, needs confirmation |

Every group can be switched off, and so can every single command (click it in the Commands card). **Custom Names** adds game-specific names for commands, for example `jump` for `b`. Names are letters only and cannot reuse a built-in command or short form. A custom name does exactly what its command does.

**Multiple commands in one message** (off by default): `dup dup ddown ddown dleft dright dleft dright b a`. They run one after another, never at once, and pressing the same one twice (`a a`) gets a 0.05 s release in between. Every word has to be a command or a time, otherwise the whole message is ignored. A command that is turned off is skipped and the rest still run. One person cannot start a new multi-command message while their last one is running. The length limit defaults to 12.

**Limits** (type a value, or use the sliders for the usual range): Default Tap Duration 0.5 s, Longest Hold 3 s, Cooldown per viewer 0.5 s, Max Simultaneous Commands 6.

Settings save as soon as they change, in `~/.config/pis2cr/chat.json`. Overlay settings are in `overlays.json` in the same folder.

## Macros and the Fun tab

A macro is a saved list of button and stick presses with exact timing, played by the Pi, so timing does not depend on the network. The Fun tab lists them with Play, Stop and Delete.

- **While one plays:** chat commands are paused and chat messages are refused.
- **Seek bar:** click or drag to jump to any point, or use the -10 s and +10 s buttons.
- **Cancel:** pressing LB and RB together on the real controller stops the running macro. Macro button presses do not count.
- **Speed:** each macro has its own speed (50% to 400%). Waits are divided by the speed. Steps marked `"fixed": true` keep their following wait (for example a game's own animation). `min_release` (seconds) keeps a minimum gap before the same button is pressed again.
- **Chat triggers:** a keyword that starts a macro. It counts anywhere in a message as a whole word, so "lets roll" starts a macro with the trigger `roll` but "rolling" does not. It follows the same rules as chat commands (mode, who can send). Let Chat Start Macros is off by default, and a cooldown (default 120 s) counts from the start of the last macro. The word cannot be a command or a custom name.
- **Import:** paste `{"steps": [{"t": seconds, "buttons": [names], "left": [x, y]}, ...]}`. The last step must release everything. Macros are stored in `~/.config/pis2cr/macros.json`.

### Ocarina tools

`fun/ocarina/` holds PC-side tools that turn a song into a macro for a game's ocarina minigame. They are not installed on the Pi, and no audio files are included.

- `calibrate.py` measures which pitch each button and stick position makes in the game (needs a virtual audio cable and the dashboard running).
- `make_song.py song.mp3 --name x --label "X" [--range LOW HIGH] [--start s --end s] [--upload]` makes a macro from any mp3 using the calibration from `calibrate.py`. It finds the 16th-note grid, follows the dominant pitch in a midi range (default 60 to 93; `--range 28 59` follows the bass), folds notes into the ocarina's range and maps them to button and stick. It writes `out/x_macro.json`, `x_notes.json` and `x_preview.wav`. It tracks one line only, so it works best on a clear lead.
