import io
import wave

from app.audio import to_pcm


def test_to_pcm_resamples_to_mono_16bit():
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(48000)
        w.writeframes(b"\0\0\0\0" * 48000)  # 1 s stereo
    pcm = to_pcm(buf.getvalue(), 16000)
    assert abs(len(pcm) - 32000) <= 64  # 1 s of 16 kHz mono 16-bit
