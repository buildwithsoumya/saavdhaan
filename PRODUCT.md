# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Stack

Plain HTML + one JS + one CSS file served by the FastAPI backend; no framework (user's brief).

## Users

Elderly people in India who get phone calls from scammers posing as police, CBI, customs, couriers, banks or a relative in trouble. A family member (son, daughter, grandchild) sets Saavdhaan up once: enrols their own voice, picks the alert voice, fills in the family contact. During a call the elder only glances at the screen of a laptop or old phone placed next to their speakerphone.

## Product Purpose

Listen to the call live in any of 10 Indian languages, recognise the scam script as it unfolds (authority, fear, isolation, urgency, extraction), and step in with a spoken warning in the elder's language, before money leaves. Success is a hang-up before the first transfer.

## Positioning

It follows the scam's own sequence instead of keyword-spotting, explains every alert with the exact phrases heard, and the warning can come in the voice of the elder's own family member.

## Operating Context

Speakerphone call in a home, often noisy. The elder may be frightened, told not to hang up or tell anyone. The screen is read at arm's length, in a glance. Settings are touched only by the family member, rarely.

## Capabilities and Constraints

- Gnani Prisma v3.0 realtime STT, Timbre v3.0 TTS with audio tags, voice cloning, batch STT.
- Synthetic data only: no real phone numbers, Aadhaar, PAN, account numbers or recorded calls.
- National cyber-crime helpline 1930 is the single action the elder is pointed to.
- UI labels are bilingual Hindi + English; spoken alerts follow the detected call language.

## Product Principles

- One glance tells the elder what to do: keep talking, be careful, or hang up.
- Always show why: stages hit and the phrases heard.
- Never block the call or act on the elder's behalf; warn and point to 1930.
- Family setup is tucked away; the call view has no controls the elder can get wrong.

## Accessibility & Inclusion

Elderly users: very large type, high contrast, status never conveyed by colour alone, works at phone width, bilingual labels.
