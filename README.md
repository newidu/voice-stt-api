# Voice API — speech to text for the WhatsApp agent

A small server that turns a voice note into text. The WhatsApp agent sends the audio here and gets the
text back. The agent then answers the text as if the user had typed it.

It runs on its **own server**, separate from the agent. Put it on any Linux box or VPS that can reach the
internet. This is for hosts where the agent (PHP) cannot run Python.

```
WhatsApp voice note → agent (PHP) → POST audio → voice API → ffmpeg → Google speech (free, no key)
                                                               ↓
WhatsApp reply  ←  agent (AI answers the text)  ←  JSON {text, language} ←┘
```

## 1. What you need on the voice server

- Linux (Debian/Ubuntu tested), **Python 3.11 or 3.12**. Python 3.13 will not work with `googletrans`.
- **ffmpeg** (`sudo apt install ffmpeg`).
- Outbound internet (Google speech and translate endpoints).
- No API key. The speech endpoint is free and unofficial (see "Limits").

## 2. Install

1. Upload the contents of this folder to the server, e.g. `/opt/voice-api/`.
2. Edit **`config.py`**:
   - `API_TOKEN`: a long random secret. Make one with
     `python3 -c "import secrets; print(secrets.token_urlsafe(32))"`.
     The server **refuses to start** on a public address (`HOST = '0.0.0.0'`) without it.
   - `DEFAULT_LANGUAGES`: keep `['si-LK', 'en-US']` unless your users mostly speak Tamil (see "Languages").
   - `PORT`, `MAX_UPLOAD_MB`, `FFMPEG` if you need them.
3. Run the setup script:
   ```
   cd /opt/voice-api
   ./setup.sh
   ```
   It builds `venv/`, installs the packages from `requirements.txt`, and checks ffmpeg. Every line says
   `[OK]` or `[FAIL]`. Fix any `[FAIL]` and run it again.
4. Run the tests:
   ```
   venv/bin/python -m unittest discover -s tests -v
   ```
   The real-audio test needs internet. If Google cannot be reached, it is skipped, not failed.

## 3. Run it

Quick start (stays up until the server reboots or is stopped):

```
cd /opt/voice-api
nohup venv/bin/python server.py > voice.log 2>&1 &
```

Better: a systemd service, so it restarts on its own. Create `/etc/systemd/system/voice-api.service`:

```
[Unit]
Description=Voice API (speech to text)
After=network-online.target

[Service]
WorkingDirectory=/opt/voice-api
ExecStart=/opt/voice-api/venv/bin/python server.py
Restart=always
User=www-data

[Install]
WantedBy=multi-user.target
```

then `sudo systemctl enable --now voice-api`.

On shared hosting, use the host's "Setup Python App" page (Python 3.11 or 3.12) and point it at `server.py`.

## 4. HTTPS (recommended)

The agent sends your API token on every request, so use HTTPS. Put the server behind a reverse proxy with a
certificate, for example Caddy (automatic HTTPS):

```
voice.example.com {
    reverse_proxy 127.0.0.1:8787
}
```

Then change `HOST` in `config.py` to `127.0.0.1` (only the proxy can reach the API). Note: with
`HOST = '127.0.0.1'` the server can start without a token. Keep the token anyway.

## 5. Connect the agent

In the agent's `config.php`:

```php
'voice' => [
    'enabled'   => true,
    'api_url'   => 'https://voice.example.com',   // no trailing slash
    'api_token' => 'THE SAME VALUE AS API_TOKEN',
    'languages' => ['si-LK', 'en-US'],
    'timeout'   => 90,
],
```

Then run the agent's `php install.php` (or open `install.php?token=…` in a browser). It calls the voice API
and reports what is missing.

## 6. API reference

| Route | What it does |
|---|---|
| `GET /health` | `{ok, ffmpeg, speech_recognition, googletrans, languages, max_upload_mb}`. No token needed. |
| `GET /version` | Returns the deployed API version, for example `{"ok": true, "version": "1.1.0"}`. No token needed. |
| `POST /transcribe?languages=si-LK,en-US` | Body = the raw audio file. Header `X-Api-Token` (when `API_TOKEN` is set). |

A successful reply:

```json
{"ok": true, "text": "මම අද කොළඹ කාලගුණය ගැන දැනගන්න ඕනේ", "language": "si-LK", "detected": "si",
 "confidence": 0.5, "candidates": [{"language": "si-LK", "confidence": null, "chars": 37, "chosen": true, "error": ""}, ...]}
```

Errors are JSON `{"ok": false, "error": "..."}` with these codes:

| Code | Meaning |
|---|---|
| 400 | empty body, or a bad `languages` tag |
| 401 | missing or wrong `X-Api-Token` |
| 411 | no `Content-Length` (chunked upload not accepted) |
| 413 | file bigger than `MAX_UPLOAD_MB` |
| 422 | not audio, broken audio, or no speech recognised in any language |
| 500 | ffmpeg or SpeechRecognition missing on this server (see `/health`) |
| 502 | Google's speech endpoint did not answer (try again later) |

## 7. How the language is chosen

Every language in the list is tried. Each result gets a score: Google's confidence if it gives one, otherwise
0.5 (for example `si-LK` never gives one). The best score wins, and ties go to the earlier language.
The server log shows every candidate, for example `[si-LK=n/a* en-US=0.385]` (`*` = chosen).

The default `si-LK,en-US` setting is mixed-language auto mode. The audio is split into ordered chunks,
preferably at natural pauses, with an 8-second maximum. Each chunk is tried in both languages, the best
language for that chunk is selected, and the selected chunks are joined back in their original order. This means a voice note can switch between
Sinhala and English and keep both parts in the returned `text`. The response uses `language: "si-en"`
when both language recognisers were selected, and `detected: "si-en"` when the final text contains both
Sinhala and Latin characters. This remains a heuristic because the free Google Web Speech endpoint does
not provide a true bilingual model; clear audio and language changes near chunk boundaries work best.

Before recognition, ffmpeg converts the upload to signed 16-bit mono 16 kHz PCM using high-quality SoX
resampling while preserving the source waveform. Google’s speech guidance warns that noise-reduction and
automatic-gain processing can reduce recognition accuracy, so this pipeline does not apply aggressive
denoising or volume pumping. Clear recording, a close microphone, no clipping, and low echo are more useful
than adding filters. This is the highest practical setup available from the free Web Speech endpoint; a
paid or dedicated multilingual model is still more accurate in noise and rapid code-switching.

**Languages.** Keep the list short. Confidence values are not comparable across languages, so a wrong
language can win. Tested with synthetic speech (gTTS):

| Spoken | `si-LK, en-US` (default) | `si-LK, en-US, ta-LK` |
|---|---|---|
| Sinhala | correct | **Tamil (wrong)**: the Tamil model reads Sinhala as confident Tamil |
| English | correct | correct |
| Tamil | wrong (no Tamil model in the list) | correct |

So add `ta-LK` only for Tamil-speaking users.

## 8. Troubleshooting

| Symptom | Fix |
|---|---|
| `setup.sh`: no Python 3.10–3.12 | Install Python 3.11 with its `venv` module, or install `uv` and run `uv venv --python 3.11 venv`. |
| `/health` shows `"ffmpeg": false` | `sudo apt install ffmpeg`, or set `FFMPEG` in `config.py` to the full path. |
| "REFUSED TO START" | Set `API_TOKEN` in `config.py` (see step 2). |
| Agent says the token was rejected | `api_token` in the agent's config.php must equal `API_TOKEN` here. |
| Agent says "not reachable" | Check the URL, that the service is running (`systemctl status voice-api`), and the firewall. |
| Many 502 errors | Google is rate-limiting this server's IP. Wait, or move the service to another IP. |
| Sinhala comes back as Tamil | Remove `ta-LK` from the language list. |

## 9. Limits (be honest with users)

- The speech endpoint is **Google's free Web Speech API** (through the SpeechRecognition library). It needs no
  key, but it is **not an official, supported API**. It can rate-limit, block an IP, or change without notice.
  For a guaranteed service later, switch `stt.py` to a paid speech API (Google Cloud STT, OpenAI Whisper, …).
- Accuracy is lower in noisy audio, for fast or mixed speech, and for names and place names.
- `googletrans` only labels the language. The text is never translated.
- No audio is stored. Each upload is written to a temp file and deleted after conversion.

## Files

```
server.py          HTTP server (GET /health, POST /transcribe)
stt.py             audio → text: ffmpeg, SpeechRecognition, language choice, googletrans label
config.py          settings (token, host, port, languages, limits)
setup.sh           builds venv/, installs packages, checks ffmpeg
requirements.txt   SpeechRecognition, googletrans
tests/test_api.py  10 API tests, 3 parser tests, 5 language-choice tests (unittest, no extra packages)
tests/fixtures/    en-sample.ogg (a short English voice note for the real-audio test)
```
