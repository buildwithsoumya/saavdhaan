"""Serve Saavdhaan over HTTPS on the local network, for a phone.

    python -m app.serve        # then open the printed link on the phone, once

Phones only allow the microphone on HTTPS pages. This makes a self-signed
certificate for this machine's LAN address (the phone will warn once; choose
"proceed"), and refuses to start without APP_TOKEN so nobody else on the
network can spend API credits through it.

Listening works after "proceed". Chrome does not run service workers on a page
with a certificate warning, so "Add to Home screen" gives a shortcut rather
than an offline-capable app unless the phone trusts the certificate.
"""
import hashlib
import shutil
import socket
import ssl
import subprocess
import sys
import time
from pathlib import Path

import uvicorn

from app.config import settings

PORT = 8443
GIT_OPENSSL = Path(r"C:\Program Files\Git\usr\bin\openssl.exe")


def lan_ip() -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.connect(("10.255.255.255", 1))  # no packet is sent; this just picks the LAN interface
        return s.getsockname()[0]


def cert_cmd(openssl: str, ip: str, key: Path, cert: Path) -> list[str]:
    return [openssl, "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "30",
            "-keyout", str(key), "-out", str(cert), "-subj", "/CN=saavdhaan",
            "-addext", f"subjectAltName=IP:{ip},DNS:localhost"]


def ensure_cert(ip: str) -> tuple[Path, Path]:
    d = Path(settings.data_dir) / "tls"
    d.mkdir(parents=True, exist_ok=True)
    key, cert = d / f"{ip}.key", d / f"{ip}.crt"
    stale = cert.exists() and time.time() - cert.stat().st_mtime > 25 * 86400  # certs last 30 days
    if not cert.exists() or stale:
        openssl = shutil.which("openssl") or (str(GIT_OPENSSL) if GIT_OPENSSL.exists() else None)
        if not openssl:
            raise SystemExit("openssl not found (it ships with Git for Windows)")
        subprocess.run(cert_cmd(openssl, ip, key, cert), check=True, capture_output=True)
    return key, cert


def keep_awake() -> None:
    """Stop Windows from idle-sleeping while serving: a sleeping laptop drops Wi-Fi and the
    phone shows "Cannot reach the laptop". Ends with this process. Closing the lid still sleeps."""
    if sys.platform == "win32":
        import ctypes
        ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)


def fingerprint(cert: Path) -> str:
    """SHA-256 of the certificate, as the Android app shows it before trusting this laptop."""
    der = ssl.PEM_cert_to_DER_cert(cert.read_text())
    return ":".join(f"{b:02X}" for b in hashlib.sha256(der).digest())


if __name__ == "__main__":
    if not settings.app_token:
        raise SystemExit("Set APP_TOKEN in .env first (any long random string).")
    ip = lan_ip()
    key, cert = ensure_cert(ip)
    print(f"\n  Open on the phone:  https://{ip}:{PORT}/?t={settings.app_token}\n"
          "  (accept the certificate warning once, then 'Add to Home screen' for a shortcut)\n"
          f"  Android app: paste the same link; it should show this fingerprint:\n  {fingerprint(cert)}\n"
          "  The laptop stays awake while this runs. Keep the lid open and this window running.\n")
    keep_awake()
    uvicorn.run("app.main:app", host="0.0.0.0", port=PORT, ssl_keyfile=str(key), ssl_certfile=str(cert))
