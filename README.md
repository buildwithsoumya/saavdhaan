# सावधान · Saavdhaan

**A live scam-call guardian for elderly people in India, built on Gnani's speech APIs.**

Saavdhaan listens to a phone call on speakerphone, transcribes it live in any of 10 Indian languages, follows the scam script as it unfolds (fake CBI, customs, courier, KYC, "your son is in an accident"), and steps in with a spoken warning in the caller's language before money leaves. The warning can come in the voice of the elder's own family member.

Entry for Gnani AI's *Great Indian AI Internship Challenge*. All data in this repo is synthetic.

## How it works

```
 speakerphone call ──► phone/laptop mic ──► browser (16 kHz PCM) ──► backend ──► Gnani Prisma v3.0 realtime STT
                                                                        │
                       spoken warning ◄── Gnani Timbre v3.0 / voice clone ◄── kill-chain tracker
```

### The scam kill chain

Every digital-arrest and impersonation scam walks the same five steps. Saavdhaan tracks them instead of spotting single keywords:

| Stage | What the caller does | Examples heard |
|---|---|---|
| S1 Authority | claims to be an official | CBI, police, customs, FedEx, TRAI, RBI, judge |
| S2 Fear | threatens | arrest warrant, money laundering, drugs in parcel, account blocked |
| S3 Isolation | cuts the victim off | don't tell anyone, stay on video call, don't cut the call |
| S4 Urgency | removes time to think | right now, within two hours, otherwise arrest |
| S5 Extraction | asks for money or details | transfer to safe account, OTP, UPI, AnyDesk, Aadhaar number |

- Each stage counts once in a rolling 10-minute window. The window survives reconnects and the 15-minute STT session cap.
- **Alert levels:** L1 *Caution* = 2 stages, L2 *Saavdhaan* = 3 or more, L3 *Stop* = a money ask after authority or fear.
- **Negation guard:** a genuine bank saying "never share your OTP" or "OTP किसी को मत बताइए" does not count as extraction. Reassurance like "don't worry, just tell me the OTP" still does.
- Lexicons are data (`app/data/lexicons.yaml`): a shared Latin loanword list (Prisma `codemix` keeps CBI, OTP, UPI in Latin) plus native-script lists for Hindi, English, Tamil, Bengali, Kannada, Telugu and Marathi.
- Every alert shows *why*: the stages hit, the exact phrases and when they were said.

### Features

- **Live guardian page.** A big postmark-style status (word + colour + level), five stage boxes with the phrases heard, live transcript, detected language, alert latency in ms, and a large **1930** helpline panel at L3. Labels are in Hindi and English, sized for an elderly reader.
- **Spoken warnings** in hi, en, ta, bn, kn, te, mr with Timbre v3.0 audio tags (`<slow>`, `<whispers>`, `<shouting>`). Each line is synthesised once and cached, so repeat alerts are instant and free.
- **Family voice.** A family member records 5–30 s (with explicit consent) and the L2 warning ("Listen, it's me. This is a fraud call. Hang up.") plays in their cloned voice. Deleting the voice removes the embedding and its cached audio.
- **Stage-following keyterms.** Once authority and fear are heard, the STT keyterms switch mid-call to extraction words (OTP, UPI, AnyDesk…) so the money ask is transcribed accurately.
- **Honeypot mode (opt-in).** At L2+, with family permission, Saavdhaan can answer the caller in a neutral persona voice while the elder steps away. It keeps the caller talking to collect evidence (name, "department", where money should go) using a Groq LLM. Code-enforced rules: it never speaks digits or number words and never claims money was sent. It stops when it has enough evidence and tells the elder to hang up and report.
- **Post-call report (opt-in recording).** Batch STT with speaker separation produces a one-page report: stage timeline, who said what, and a draft complaint for 1930 / Chakshu with placeholders for personal details.
- **Phone app.** Installable PWA served over HTTPS on your Wi-Fi, protected by an access token, with the screen kept awake while listening.

## Quick start

Requirements: Python 3.11+ (developed on 3.14), [ffmpeg](https://ffmpeg.org/) on `PATH`, and a Gnani API key.

```bash
uv venv .venv
uv pip install -r requirements.txt          # or: python -m venv .venv && pip install -r requirements.txt
cp .env.example .env                        # then fill in GNANI_API_KEY (and GROQ_API_KEY for honeypot mode)
```

### Run on a laptop

```bash
.venv/Scripts/python -m uvicorn app.main:app --port 8000      # Windows
# .venv/bin/python -m uvicorn app.main:app --port 8000        # macOS / Linux
```

Open <http://127.0.0.1:8000>, press **Start listening**, and put the call on speaker next to the laptop. Use `127.0.0.1` or `localhost`: browsers only allow the microphone on secure origins. If `APP_TOKEN` is set in `.env`, open `http://127.0.0.1:8000/?t=<token>` once; the browser remembers it.

### Run on a phone

Phones cannot share call audio with any app on the same phone, so Saavdhaan runs on a **second** phone (or tablet) placed next to the call phone's speaker. The laptop runs the backend.

```bash
# set APP_TOKEN in .env to a long random string first
.venv/Scripts/python -m app.serve
```

Open the printed `https://<laptop-ip>:8443/?t=<token>` link on the phone once and accept the self-signed certificate warning. "Add to Home screen" gives a shortcut. The token is remembered on that device.

### Check the API connection (a few credits)

```bash
.venv/Scripts/python -m app.probe                       # TTS one line, stream it back through STT
.venv/Scripts/python -m app.probe --clone reference.wav # also test voice cloning
```

## Configuration (`.env`)

| Variable | Purpose |
|---|---|
| `GNANI_API_KEY` | Gnani API key (required) |
| `ALERT_VOICE` | Timbre v3.0 voice for warnings (default `Shivani`) |
| `APP_TOKEN` | Access token for API, WebSocket and reports; required by `app.serve` |
| `GROQ_API_KEY`, `GROQ_MODEL` | LLM for honeypot replies (default `llama-3.3-70b-versatile`) |
| `HONEYPOT_VOICE` | Timbre v3.0 persona voice for honeypot mode (default `Vasudha`) |

Cached audio, voice embeddings, opted-in recordings, reports and honeypot logs live in `data/`, which is gitignored.

## SaavdhaanBench

A benchmark of 60 synthetic calls in `bench/calls/`:

- 6 scam templates: digital arrest, courier drugs, fake KYC, son accident, TRAI SIM block, ED virtual court.
- 6 hard negatives: a real bank fraud desk ("never share OTP"), a courier delivery code, a passport police verification, a relative urgently asking for money, a loan-collection call, a cyber-cell awareness call.
- 5 language variants each: Hindi, code-mixed Hindi-English, English, Tamil, Bengali.

Calls are rendered with Timbre v3.0 (two voices), degraded with ffmpeg (clean 16 kHz; 8 kHz G.711 phone line; pink noise at ~10 and ~5 dB SNR; random 20–60 ms dropouts) and streamed through Prisma v2.5 and v3.0, with and without keyterms and stage-following keyterms.

The headline metric is **Pre-Payment Catch Rate**: the share of scam calls flagged at L2+ *before the first money ask finishes*. Also reported: scam recall, false-alarm rate on the hard negatives, median time-to-alert, WER and keyterm recall. The alert threshold is calibrated to keep false alarms under 5%.

```bash
.venv/Scripts/python -m bench.seed                     # regenerate the call YAMLs
.venv/Scripts/python -m bench.run --text-only          # free: tracker on the scripts, no API calls
.venv/Scripts/python -m bench.run --limit 4 --dry-run  # render + degrade only (asks before spending TTS credits)
.venv/Scripts/python -m bench.run --limit 4 --conditions phone_snr10 --configs v3+kt+follow
```

Every run prints an estimated credit cost and asks before spending. Results go to `bench/out/results.csv` and `bench/out/report.html`.

**Text-only baseline** (perfect transcription, L2 threshold): scam recall 100%, false alarms 0%, Pre-Payment Catch 50%. Half of the scam scripts only reach three stages with the money ask itself, which is the gap the live system has to close. The lexicon and the scripts were written together, so treat this as an upper bound. Live STT numbers depend on an approved benchmark run.

## Project layout

```
app/
  main.py       FastAPI app: page, WebSocket bridge browser ⇄ backend ⇄ Gnani, voices/report APIs, token check
  gnani.py      Gnani clients: realtime STT (reconnect + previous_text), TTS REST/SSE, voice clone, batch STT
  tracker.py    scam kill-chain tracker, negation guard, alert levels, explanations
  alerts.py     spoken alerts per level and language, cached on disk
  enrol.py      consented family-voice enrolment
  honeypot.py   persona replies via Groq with code-enforced safety rules
  report.py     post-call report from batch STT with speaker separation
  serve.py      HTTPS on the LAN for a phone
  probe.py      small live API check
  data/         lexicons.yaml, alerts.yaml, keyterms.yaml
web/            index.html, app.js, styles.css, PWA manifest, service worker, icons, fonts (OFL)
bench/          calls/*.yaml, seed.py, render.py, degrade.py, run.py, report.py
tests/          pytest suite; every Gnani and Groq call is mocked
```

## Tests

```bash
.venv/Scripts/python -m pytest -q
```

Tests never call a live API and never read your `.env`.

## Gnani APIs used

| API | Used for |
|---|---|
| Prisma v3.0 realtime STT (`wss://api.vachana.ai/stt/v3/stream`) | live transcription: `indic-auto`, `codemix`, telephony VAD, keyterms, mid-call `update_settings`, `previous_text` across 14-minute reconnects |
| Prisma v2.5 realtime STT | benchmark comparison |
| Timbre v3.0 TTS (REST and SSE) | spoken warnings with audio tags, honeypot persona, benchmark rendering |
| Voice clone embeddings + `vachana-vc-v1` | family-voice warnings |
| Batch STT with diarization | post-call report |

## Limits and honest notes

- The non-Hindi lexicons, alert lines and benchmark scripts (ta, bn, kn, te, mr) are marked `needs_native_review` and should be checked by native speakers.
- Speakerphone audio mixes both sides of the call. The tracker does not separate the caller from the elder, and the elder must stay silent during honeypot mode.
- The self-signed certificate lets the phone use the mic but not install an offline app; a trusted certificate fixes that.
- Speaker labels in reports come from automatic diarization and can be wrong.
- Saavdhaan warns and points to the national cyber-crime helpline **1930**. It never blocks a call or acts for the user.

## Licence notes

Fonts in `web/fonts/` are Noto Sans Devanagari and Work Sans under the SIL Open Font License (licence files alongside).
