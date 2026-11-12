# PiS2CR — Feasibility Notes

Written before the build, as background and protocol notes. The relay was built with **option B** (Bluetooth → Pi 4 → Bluetooth LE, emulating a Pro Controller 2). Option C (USB) is the fallback.

Legend: **[V]** verified in source code / primary docs · **[C]** claimed by others, not verified by us · **[A]** our assumption / inference · **[?]** unknown

## 1. Key protocol facts

- [V] Switch 2 controllers (Pro Controller 2, PID `057E:2069`) use **Bluetooth LE** with Nintendo's own GATT layout — not Classic BT, not standard BLE HID. (ndeadly/switch2_controller_research)
- [V] Their "pairing" is not BLE SMP. It is a vendor command (`0x15`) using a fixed XOR key + AES-128-ECB challenge. Reproducible; not a security barrier. Implemented independently in 4 projects.
- [V] The console runs controllers at a **5 ms** BLE connection interval (below the 7.5 ms spec minimum).
- [V] Over **USB**, the Pro Controller 2 is a composite device: HID + vendor bulk + USB audio. Same command set (`cmd,0x91,…`) as BLE.
- [V] Linux kernel driver (for-7.4) supports USB only; IMU/rumble/battery not yet present. SDL3 driver is USB-only. → BLE controller side must be written in userspace.

## 2. Reference projects

| Project | License | Direction | Relevance |
|---|---|---|---|
| trevlars/switch2-controllers-linux | MIT | Linux ← real SW2 controller (BLE) | [V] Buttons, sticks, C, IMU, battery, HD rumble, LEDs, pairing. Python/bleak. Best reference for controller side. |
| Dycool/NS-PC-Control | GPL-3.0 | Pi → Switch 2 via **USB gadget** | [V] Emulates Pro Controller 2 (`057E:2069`) on Pi 4 / Zero 2 W via Raw Gadget. Gyro, rumble feedback, NFC/amiibo, C button, audio interface. Best reference for console side. |
| wozt/pcble2gamepad | GPL-3.0 | Linux → Switch 2 via BT | [V] Switch 1 Pro emulation over Classic BT (no rumble, fake IMU). [C] Joy-Con 2 BLE emulation confirmed 2026-11-01, needs exclusive HCI adapter. |
| GP2040-CE (extra) | MIT | RP2040 → Switch 2 via USB | [V] Pro Controller 2 USB emulation; [C] tested on Switch 2. |

Licensing: copying GPL-3.0 code makes PiS2CR GPL-3.0. Using them as protocol references only does not. **Decision needed later.**

[?] No public, complete Switch 2 controller → Pi → Switch 2 relay exists. PiS2CR would be the first we know of.

## 3. Console-side options

| | A. BT Classic (Switch 1 Pro emulation) | B. BLE (Pro Controller 2 emulation) | C. USB gadget (Pro Controller 2 emulation) |
|---|---|---|---|
| Proven on Switch 2 | [C] nxbt, only with USB dongle; onboard Pi BT failed | [C] one ESP32 project; [C] wozt Joy-Con 2 (days old) | [V] code exists for Pi 4; [C] GP2040-CE tested |
| C button | No | Yes | Yes |
| HD Rumble 2 | No (Switch 1 format; must translate) | Native | Native |
| Gyro | Must translate | Native | Native (passthrough verified in code) |
| Latency | ~15 ms report rate [C] | 5 ms interval if adapter accepts it [?] | 1 ms USB polling possible |
| Radio contention | 2 BT links | 2 BLE links, exclusive adapter | Only 1 BT link |
| Extra hardware | BT dongle | BT dongle (required) | Power/data split for Pi 4 (see §5) |

**Verdict:** A cannot meet Phase 1 (no C, no native rumble). B is promising but unproven on Linux. **C is best**: same native protocol on both sides → near-transparent relay, wired determinism, one radio.

## 4. Recommended Phase 1 architecture

```
Pro Controller 2 ──BLE──► Pi (onboard BT) ──USB gadget, 057E:2069──► Switch 2 dock
        ◄── rumble/LED/cmds ──        ◄── rumble/LED/cmds ──
```

- Pi = BLE central to the controller, USB Pro Controller 2 to the console.
- Relay core runs entirely on the Pi. No PC/Wi-Fi in the input path.
- Fallback: option B (BLE→BLE) if USB fails, using a second BT adapter.

## 5. Hardware & power

- [V] Pi 4's USB-C port is both its **only power input** and its **only device-mode (gadget) port**.
- [A] A dock USB port cannot safely power a Pi 4 (needs 5 V / 3 A). Brown-outs → SD corruption.
- Safe options:
  1. **Pi Zero 2 W** (if available): separate data and power micro-USB ports. Supported by NS-PC-Control. **No purchase.**
  2. Pi 4 + **USB-C power/data splitter** that isolates VBUS from the data leg (~$10–20). Purchase.
  3. Pi 4 powered via GPIO 5 V pins — works but bypasses protection. Not recommended.
- Second BT dongle: **not needed initially** for option C. Buy only if the onboard link proves unstable.

## 6. Major risks / unknowns

1. [?] Onboard CYW43455 stability with a Switch 2 controller BLE link.
2. [?] Pi can only request 7.5 ms BLE interval (console uses 5 ms) → small added latency.
3. [?] Pairing the real controller to the Pi may replace its pairing with the console (re-pair by USB cable to restore). Console may also grab the controller if still paired.
4. [?] Rumble frame format BLE vs USB may differ in framing (same content expected) [A].
5. [?] Wake-from-sleep via a wired emulated controller.
6. [?] Audio jack / mic over BLE — out of Phase 1 scope.
7. [A] Estimated added latency: ~3–10 ms. **Must be measured.**

## 7. Phase 2 readiness

Relay core = three modules: `controller-link` (BLE) → `state merger` → `console-link` (USB). Phase 1 merger is pass-through. Phase 2 adds an injection source (local API service) feeding the merger with priority, timeouts and an emergency disable. Physical input never depends on the API.
