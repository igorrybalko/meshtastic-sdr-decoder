# Project Context: Meshtastic SDR Decoder

## Purpose

A hobby/learning project to decode Meshtastic LoRa packets from RTL-SDR IQ data using Python. The project is developed on Windows, mainly in VS Code. The current approach is Python-based and does not depend on GNU Radio or C++.

This file is working context for future development conversations. It is not intended to replace the public README.

## Current environment and radio setup

- SDR: RTL-SDR Blog V4
- Test band/frequency: 433 MHz; a known Meshtastic device has been used around 433.125 MHz
- Development OS: Windows
- Python: 3.14 has been used
- IQ captures and live SDR reception are both part of the workflow
- LoRa settings depend on the selected Meshtastic modem preset; do not assume one SF/BW combination applies to all presets

## Current project state

The codebase has grown beyond the original single-capture experiment and includes separate modules for signal detection, buffering, LoRa synchronization, header/payload decoding, cryptography, protocol parsing, configuration, and stream processing.

Important current files include:

- `stream_worker.py` — receives/collects IQ samples and coordinates packet detection and decoding
- `extract_header.py`, `detector.py`, `preamble.py`, `sfd.py`, `header.py` — detection and synchronization/header processing
- `payload.py`, `phy.py`, `payload_search.py` — payload demodulation/decoding helpers
- `packet_decoder.py` — dispatches packet decoding, broadcast/channel decryption and PKI processing
- `crypto.py` — cryptographic helpers
- `protocol.py` — Meshtastic packet/application parsing
- `config.py`, `app_settings.py` — configuration
- `rtl_source_async.py` — RTL-SDR source integration

The exact module boundaries may change as the project evolves. Check the current uploaded files before proposing code changes.

## Known progress

- The decoder has detected LoRa/Meshtastic-like bursts and has recovered packet headers in test captures.
- A PKI decoding path has recovered a packet candidate whose AES-CCM authentication succeeded and whose plaintext could be parsed as a Meshtastic text message.
- `packet_decoder.py` supports broadcast/channel processing and a PKI path. Broadcast results can include successful decryption, unknown channel, or decryption failure; private packets not addressed to this decoder may be returned as `foreign`.
- The current application emits decoded result dictionaries to the UI/table through `stream_worker.py`.
- Packet detection and decoding are still experimental. A parsed header or a dictionary result alone does not prove that the entire radio packet is correct.

## Important limitations and cautions

- Treat this as an early, incomplete prototype. Results may be wrong or incomplete with different captures, presets, signal levels, or radio conditions.
- Do not assume every returned result is verified. Distinguish authenticated/decrypted packets from packets with unknown keys, parse errors, or incomplete decoding.
- Successful AES-CCM authentication supports the integrity of the authenticated encrypted content, but fields not covered by authentication (for example, some routing/header metadata) must not automatically be treated as cryptographically verified.
- The payload/PHY CRC and parity validation path has been under development. Check current code and test results before claiming full radio-packet validation.
- Preset-specific capture windows are a known improvement to make. Different modem presets can require different packet durations; the current fixed window is provisional. A future implementation should select/derive a suitable window from the active preset and ensure enough IQ samples are available.
- Keep SDR acquisition responsive. Network operations (such as future Telegram publishing) should run separately from the SDR/decoder worker, preferably through a queue.

## Telegram publishing idea

A future feature is to publish selected results to a public Telegram channel. The intended policy is to publish only packets that pass explicit verification criteria. Before implementing it, establish a trustworthy verification status in the decoder and inspect the current `stream_worker.py` integration point. Do not publish every dictionary returned by `decode_packet()`.

Potential implementation: a separate `telegram_publisher.py` module, a queue, and configuration via environment variables. Never hardcode bot tokens or commit them to GitHub.

## Development approach

- Prefer small, focused changes over broad rewrites.
- Inspect the current version of relevant files before editing; uploaded files may be newer than older project notes.
- Preserve the existing table/UI output while adding new features.
- Explain changes step by step and identify exactly which file and location should be changed.
- Use test captures/regression checks when available.
- Do not describe a feature as reliable or complete until it has been tested.

## Security and repository hygiene

- Never store private keys, Telegram bot tokens, channel PSKs, or other secrets in this file or in source control.
- Keep private test captures and identifying packet data out of a public repository unless intentionally reviewed for publication.
- Use environment variables or a local, ignored configuration file for secrets.
