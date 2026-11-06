"""Telephone-style degradations of a clean 16 kHz call, done with ffmpeg.

clean          16 kHz as rendered
phone          300-3400 Hz band-pass, 8 kHz, G.711 mu-law
phone_snr10    pink noise mixed at ~10 dB SNR before the phone line
phone_snr5     same at ~5 dB SNR
phone_dropout  phone line with random 20-60 ms dropouts (about one every 2 s)

Noise is synthetic: ffmpeg's `anoisesrc` pink noise with a fixed seed, so no
recorded audio is involved. SNR uses ffmpeg `volumedetect` mean levels over the
whole file (gaps included) and is applied before the 300-3400 Hz band-pass, which
removes much of the pink noise's low end, so in-band SNR during speech is higher
than the label. Treat the labels as relative noise levels.
"""
import random
import re
import subprocess
from pathlib import Path

CONDITIONS = ("clean", "phone", "phone_snr10", "phone_snr5", "phone_dropout")
PHONE_FILTER = "highpass=f=300,lowpass=f=3400"
NOISE_SEED = 7


def rate_of(condition: str) -> int:
    return 16000 if condition == "clean" else 8000


def phone_args(src, dst) -> list[str]:
    return ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-af", PHONE_FILTER,
            "-ar", "8000", "-ac", "1", "-c:a", "pcm_mulaw", str(dst)]


def noise_mix_args(src, dst, duration_s, gain_db) -> list[str]:
    noise = f"anoisesrc=color=pink:amplitude=0.5:seed={NOISE_SEED}:duration={duration_s:.2f}:sample_rate=16000"
    return ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-f", "lavfi", "-i", noise,
            "-filter_complex", f"[1]volume={gain_db:.2f}dB[n];[0][n]amix=inputs=2:duration=first:normalize=0",
            "-ar", "16000", "-ac", "1", str(dst)]


def dropout_windows(duration_s, seed, every_s=2.0) -> list[tuple[float, float]]:
    rng = random.Random(seed)
    out, t = [], rng.uniform(0.5, every_s)
    while t < duration_s:
        out.append((round(t, 3), round(t + rng.uniform(0.020, 0.060), 3)))
        t += rng.uniform(0.5 * every_s, 1.5 * every_s)
    return out


def dropout_args(src, dst, windows) -> list[str]:
    enable = "+".join(f"between(t,{a},{b})" for a, b in windows) or "0"
    return ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src),
            "-af", f"volume=0:enable='{enable}'", "-c:a", "pcm_mulaw", str(dst)]


def mean_db(path) -> float:
    r = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(path), "-af", "volumedetect", "-f", "null", "-"],
                       capture_output=True, text=True)
    return float(re.search(r"mean_volume: (-?[\d.]+) dB", r.stderr).group(1))


def noise_mean_db(duration_s) -> float:
    r = subprocess.run(["ffmpeg", "-hide_banner", "-f", "lavfi", "-i",
                        f"anoisesrc=color=pink:amplitude=0.5:seed={NOISE_SEED}:duration={duration_s:.2f}"
                        ":sample_rate=16000",
                        "-af", "volumedetect", "-f", "null", "-"], capture_output=True, text=True)
    return float(re.search(r"mean_volume: (-?[\d.]+) dB", r.stderr).group(1))


def duration_s(path) -> float:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                       capture_output=True, text=True, check=True)
    return float(r.stdout)


def degrade(clean: Path, condition: str, seed: int = 0) -> Path:
    """Return the path of `clean` under `condition`, creating it if needed."""
    if condition == "clean":
        return clean
    out_dir = clean.parent.parent / condition
    out_dir.mkdir(parents=True, exist_ok=True)
    dst = out_dir / clean.name
    if dst.exists():
        return dst
    if condition == "phone":
        subprocess.run(phone_args(clean, dst), check=True)
    elif condition.startswith("phone_snr"):
        snr = float(condition.removeprefix("phone_snr"))
        dur = duration_s(clean)
        gain = mean_db(clean) - snr - noise_mean_db(dur)
        noisy = out_dir / f"{clean.stem}.noisy.wav"
        subprocess.run(noise_mix_args(clean, noisy, dur, gain), check=True)
        subprocess.run(phone_args(noisy, dst), check=True)
        noisy.unlink()
    elif condition == "phone_dropout":
        phone = degrade(clean, "phone")
        subprocess.run(dropout_args(phone, dst, dropout_windows(duration_s(clean), seed)), check=True)
    else:
        raise ValueError(condition)
    return dst
