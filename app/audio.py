import subprocess


def ffmpeg(args, data: bytes = b"") -> bytes:
    """Run ffmpeg reading stdin and writing stdout."""
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", "pipe:0", *args, "pipe:1"]
    return subprocess.run(cmd, input=data, capture_output=True, check=True).stdout


def to_pcm(audio: bytes, rate=16000) -> bytes:
    """Any audio file -> raw 16-bit mono PCM at `rate`."""
    return ffmpeg(["-ac", "1", "-ar", str(rate), "-f", "s16le"], audio)


def to_wav(audio: bytes, rate=16000) -> bytes:
    return ffmpeg(["-ac", "1", "-ar", str(rate), "-f", "wav"], audio)
