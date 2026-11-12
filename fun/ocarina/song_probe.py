"""Look at an mp3: decode to a wav in audio/, print length, tempo, and the strongest pitches per section."""
import subprocess
import sys
from pathlib import Path

import imageio_ffmpeg
import librosa
import numpy as np

HERE = Path(__file__).parent


def decode(mp3, wav, rate=22050):
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error", "-i", str(mp3), "-ac", "1", "-ar", str(rate), str(wav)], check=True)


def main():
    mp3 = Path(sys.argv[1])
    wav = HERE / "audio" / "song_probe.wav"
    decode(mp3, wav)
    y, sr = librosa.load(str(wav), sr=22050)
    print(f"length {len(y) / sr:.1f} s")
    tempo = librosa.beat.tempo(y=y, sr=sr)
    print(f"tempo {float(np.atleast_1d(tempo)[0]):.1f} bpm")
    chroma = librosa.feature.chroma_cqt(y=y, sr=sr, hop_length=2048)
    names = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
    total = chroma.mean(axis=1)
    print("pitch classes:", " ".join(f"{n}:{v:.2f}" for n, v in zip(names, total)))
    step = int(10 * sr / 2048)
    for i in range(0, chroma.shape[1], step):
        part = chroma[:, i:i + step].mean(axis=1)
        top = np.argsort(part)[::-1][:4]
        print(f"{i * 2048 / sr:6.1f} s: " + " ".join(names[t] for t in top))


if __name__ == "__main__":
    main()
