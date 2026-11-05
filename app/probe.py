"""Small live check of the Gnani APIs (uses a few credits).

    python -m app.probe                   # TTS one tagged line, stream it back through STT
    python -m app.probe --clone ref.wav   # also: clone a voice and test audio tags with it
"""
import argparse
import asyncio
import time
from pathlib import Path

from app import gnani
from app.audio import to_pcm

LINE = "<slow> ध्यान से। कोई भी पुलिस वाला फ़ोन पर अरेस्ट नहीं करता।"


async def stream_back(wav: bytes):
    done = asyncio.Event()

    async def on_event(msg):
        print("  stt:", msg.get("type"), msg.get("detected_language_code", ""), msg.get("text") or msg.get("message", ""))
        if msg.get("type") == "transcript":
            done.set()

    stream = gnani.SttStream(on_event, keyterms=["police", "arrest"])
    await stream.start()
    pcm = to_pcm(wav) + b"\0" * 32000  # 1 s of silence closes the utterance
    for i in range(0, len(pcm), gnani.FRAME_BYTES):
        await stream.send_audio(pcm[i:i + gnani.FRAME_BYTES])
        await asyncio.sleep(0.032)  # real-time pacing
    await asyncio.wait_for(done.wait(), 15)
    await stream.close()


async def main(clone: Path | None):
    t = time.perf_counter()
    wav = await gnani.tts(LINE, voice="Shivani", language="hi-IN")
    print(f"tts: {len(wav)} bytes in {time.perf_counter() - t:.2f}s")
    await stream_back(wav)
    if clone:
        emb = await gnani.clone_embedding(clone.read_bytes(), clone.name)
        print("embedding shape:", emb.get("shape"))
        for text in ("<whispers> यह एक टेस्ट है।", "यह एक टेस्ट है।"):
            out = await gnani.vc_tts(text, emb)
            Path("data").mkdir(exist_ok=True)
            name = Path("data") / ("vc_tagged.wav" if "<" in text else "vc_plain.wav")
            name.write_bytes(out)
            print(f"vc: {name} ({len(out)} bytes) - listen: is the tag whispered, spoken aloud, or ignored?")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--clone", type=Path)
    asyncio.run(main(p.parse_args().clone))
