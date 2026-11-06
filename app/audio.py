import subprocess


def ffmpeg(args, data: bytes = b"") -> bytes:
    """Run ffmpeg reading stdin and writing stdout. Only the pipe protocol is allowed,
    so an uploaded playlist-style file cannot make ffmpeg open other files or URLs."""
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-protocol_whitelist", "pipe",
           "-i", "pipe:0", *args, "pipe:1"]
    return subprocess.run(cmd, input=data, capture_output=True, check=True).stdout


def to_pcm(audio: bytes, rate=16000) -> bytes:
    """Any audio file -> raw 16-bit mono PCM at `rate`."""
    return ffmpeg(["-ac", "1", "-ar", str(rate), "-f", "s16le"], audio)


def to_wav(audio: bytes, rate=16000, max_s: float | None = None) -> bytes:
    """Any audio file -> mono WAV, optionally cut to `max_s` seconds."""
    limit = ["-t", str(max_s)] if max_s else []
    return ffmpeg([*limit, "-ac", "1", "-ar", str(rate), "-f", "wav"], audio)
