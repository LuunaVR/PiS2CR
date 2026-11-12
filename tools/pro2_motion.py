"""Decode the Pro Controller 2's packed multi-sample motion block and convert it to Switch 1 IMU samples.

Bit layouts follow the PicoSwitch2 research notes, selected by block byte 3 (0x0D high-rate, 0x0E normal,
0x0F catch-up). Only the normal layout is verified against this controller's captures.
"""

GYRO_LSB_PER_DPS = 16.384  # +-2000 dps over 16 bits; swept angles in a guided capture were plausible at this scale
# Gyro zero offsets and sensitivity coefficient that the backend serves as the emulated controller's SPI calibration.
SWITCH_GYRO_ORIGIN = (25, -35, -36)
SWITCH_GYRO_COEFF = 13371

# (kind, start bit in the 288-bit payload, bits per axis, divisor to reach ordinary sensor counts)
LAYOUTS = {
    0x0D: (("a", 74, 22, 256), ("g", 140, 22, 128), ("a", 206, 22, 256)),
    0x0E: (("a", 68, 14, 1), ("g", 110, 13, 0.5), ("a", 149, 13, 0.5), ("g", 188, 14, 1), ("a", 230, 14, 1)),
    0x0F: (("a", 68, 14, 1), ("g", 110, 16, 4), ("a", 158, 13, 0.5), ("g", 197, 16, 4), ("a", 245, 14, 1)),
}


def _signed(value, width):
    return value - (1 << width) if value >> (width - 1) else value


def decode(block):
    """Return ([accel xyz...], [gyro xyz...]) in sensor counts (4096 per g, GYRO_LSB_PER_DPS per dps), or None."""
    layout = LAYOUTS.get(block[3]) if len(block) == 40 else None
    if layout is None:
        return None
    payload = int.from_bytes(block[4:], "little")
    accels, gyros = [], []
    for kind, start, width, divisor in layout:
        vector = tuple(_signed((payload >> (start + i * width)) & ((1 << width) - 1), width) / divisor
                       for i in range(3))
        (accels if kind == "a" else gyros).append(vector)
    return accels, gyros


def _mean(a, b):
    return tuple((x + y) / 2 for x, y in zip(a, b))


def triplet(accels, gyros):
    """Arrange a block's samples as the three (accel, gyro) pairs a Switch 1 input report carries."""
    if len(gyros) == 2:
        return [(accels[0], gyros[0]), (accels[1], _mean(gyros[0], gyros[1])), (accels[2], gyros[1])]
    return [(accels[0], gyros[0]), (_mean(accels[0], accels[1]), gyros[0]), (accels[1], gyros[0])]


def _clamp16(value):
    return max(-32768, min(32767, round(value)))


class SwitchImu:
    """Converts decoded Pro 2 samples to Switch 1 raw IMU values, tracking the gyro zero offset while still."""

    def __init__(self):
        self.bias = [0.0, 0.0, 0.0]
        self.still = 0

    def convert(self, accels, gyros):
        """Return (18 ints for the backend, bias-corrected gyro in dps, mean accel in g), all in Pro 2 axes."""
        mean_gyro = tuple(sum(g[i] for g in gyros) / len(gyros) for i in range(3))
        if max(abs(mean_gyro[i] - self.bias[i]) for i in range(3)) < 40:
            self.still += 1
            rate = 0.2 if self.still < 20 else 0.02
            for i in range(3):
                self.bias[i] += (mean_gyro[i] - self.bias[i]) * rate
        else:
            self.still = 0
        values = []
        for accel, gyro in triplet(accels, gyros):
            gx, gy, gz = (gyro[i] - self.bias[i] for i in range(3))
            # Pro 2 axes (x right, y forward, z up) to Switch 1 Pro axes (x forward, y left, z up).
            switch_accel = (accel[1], -accel[0], accel[2])
            switch_gyro = (gy, -gx, gz)
            values.extend(_clamp16(v) for v in switch_accel)
            values.extend(
                _clamp16(c / GYRO_LSB_PER_DPS * (SWITCH_GYRO_COEFF - origin) / 936 + origin)
                for c, origin in zip(switch_gyro, SWITCH_GYRO_ORIGIN))
        gyro_dps = tuple((mean_gyro[i] - self.bias[i]) / GYRO_LSB_PER_DPS for i in range(3))
        accel_g = tuple(sum(a[i] for a in accels) / len(accels) / 4096 for i in range(3))
        return values, gyro_dps, accel_g


def switch_battery_byte(power):
    """Map the Pro 2 power byte (level 0-9 in bits 2-5, charging in bit 1) to the Switch 1 battery nibble byte."""
    level = (power >> 2) & 0xF
    nibble = 8 if level >= 8 else 6 if level >= 5 else 4 if level >= 3 else 2 if level >= 1 else 0
    return (nibble << 4) | (0x10 if power & 2 else 0)
