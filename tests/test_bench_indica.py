import io
import tarfile

from bench import indica


def test_counts_flagged_conversations_per_language_and_label(tmp_path):
    convos = {
        "Text_hindi/scam/a.txt": "Speaker1: CBI officer bol raha hoon.\nSpeaker1: Arrest warrant hai, kisi ko mat batana.\n"
                                 "Speaker1: Turant UPI se transfer karo.",
        "Text_hindi/legit/b.txt": "Speaker1: Aapka khana 10 baje tak aa jayega.\nSpeaker2: Theek hai, dhanyavaad.",
        "Text_english/legit/c.txt": "Speaker1: Hello, is the gym open today?",
    }
    archive = tmp_path / "t.tar.gz"
    with tarfile.open(archive, "w:gz") as t:
        for name, text in convos.items():
            data = text.encode()
            info = tarfile.TarInfo(f"Text_samples/{name}")
            info.size = len(data)
            t.addfile(info, io.BytesIO(data))
    out = indica.evaluate(archive)
    assert out["hindi"]["scam"] == [1, 1] and out["hindi"]["legit"] == [1, 0]
    assert out["english"]["legit"] == [1, 0]
    assert set(indica.evaluate(archive, {"english"})) == {"english"}
