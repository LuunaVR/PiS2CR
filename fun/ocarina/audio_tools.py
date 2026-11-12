"""Small audio helpers for the ocarina tools: load a wav and draw a spectrogram PNG without matplotlib."""
import numpy as np
import soundfile as sf
from PIL import Image
from scipy import signal


def load_mono(path):
    data, rate = sf.read(path)
    if data.ndim > 1:
        data = data.mean(axis=1)
    return data.astype(np.float32), rate


def spectrogram_png(samples, rate, path, fmax=4000.0, window=4096, hop=256, seconds_per_tick=1.0, pixels_per_second=100):
    """Log-magnitude spectrogram, low frequencies at the bottom, with a mark every second along the top."""
    freqs, times, power = signal.spectrogram(samples, rate, nperseg=window, noverlap=window - hop, window="hann")
    keep = freqs <= fmax
    db = 10 * np.log10(power[keep] + 1e-12)
    db = np.clip(db, db.max() - 70, db.max())
    scaled = ((db - db.min()) / (db.max() - db.min()) * 255).astype(np.uint8)[::-1]
    # Stretch time so the image is wide enough to read.
    target_width = int(times[-1] * pixels_per_second)
    image = Image.fromarray(scaled).resize((target_width, scaled.shape[0] // 2), Image.BILINEAR).convert("RGB")
    pixels = np.asarray(image).copy()
    for second in range(0, int(times[-1]) + 1):
        pixels[:10, min(int(second * pixels_per_second), target_width - 1)] = (255, 80, 80)
    Image.fromarray(pixels).save(path)
