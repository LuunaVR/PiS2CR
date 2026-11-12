"""Integrate decoded gyro over each phase of a gyro_session capture to check scale and axes."""
import sys

import pro2_motion

marks = {}
rows = []
for line in open(sys.argv[1]):
    p = line.split()
    if p[0] == "#":
        marks[(p[1], p[2])] = float(p[3])
        continue
    rows.append((float(p[0]), bytes.fromhex(p[1])))
for name in ("still_flat", "yaw", "pitch", "roll", "shake"):
    a, b = marks[("START", name)], marks[("END", name)]
    angle = [0.0, 0.0, 0.0]
    lo = [0.0, 0.0, 0.0]
    hi = [0.0, 0.0, 0.0]
    peak = [0.0, 0.0, 0.0]
    last_t = None
    used = skipped = 0
    for t, r in rows:
        if not (a <= t <= b) or r[65] != 40:
            continue
        decoded = pro2_motion.decode(r[66:106])
        if decoded is None:
            skipped += 1
            continue
        used += 1
        dt = 0 if last_t is None else t - last_t
        last_t = t
        gyros = decoded[1]
        for i in range(3):
            rate = sum(g[i] for g in gyros) / len(gyros) / pro2_motion.GYRO_LSB_PER_DPS
            angle[i] += rate * dt
            lo[i] = min(lo[i], angle[i])
            hi[i] = max(hi[i], angle[i])
            peak[i] = max(peak[i], abs(rate))
    print(f"{name:10s} used={used} skipped={skipped} swept deg x/y/z=" +
          "/".join(f"{h - l:6.1f}" for h, l in zip(hi, lo)) + "  peak dps x/y/z=" + "/".join(f"{p:6.1f}" for p in peak))
