"""
stt.py — speech-to-text core: audio bytes → text.

Steps:
  1. ffmpeg converts the audio (WhatsApp sends .ogg/opus) to 16 kHz mono WAV.
  2. SpeechRecognition sends it to Google's free Web Speech endpoint (no API key) once per language.
     The transcript with the highest confidence wins.
  3. googletrans labels the language of the text (label only, the text is not translated).
"""
import os
import re
import subprocess
import tempfile
import threading
import time

import config

LANG_RE = re.compile(r'^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})?$')


class VoiceError(Exception):
    """An error with the HTTP status the server should answer with."""

    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


def parse_languages(raw):
    """'si-LK,en-US' → ['si-LK', 'en-US']. Empty → the defaults from config.py."""
    langs = [x.strip() for x in (raw or '').split(',') if x.strip()] or list(config.DEFAULT_LANGUAGES)
    bad = [x for x in langs if not LANG_RE.match(x)]
    if bad:
        raise VoiceError(400, 'bad language tag: ' + ', '.join(bad)[:80])
    return langs[:8]


def health():
    """Which parts are installed. Used by GET /health and by the agent's install check."""
    ff = False
    try:
        r = subprocess.run([config.FFMPEG, '-version'], capture_output=True, timeout=10)
        ff = r.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass
    try:
        import speech_recognition  # noqa: F401
        sr_ok = True
    except ImportError:
        sr_ok = False
    try:
        import googletrans  # noqa: F401
        gt_ok = True
    except Exception:
        gt_ok = False
    return {
        'ok': ff and sr_ok,
        'ffmpeg': ff,
        'speech_recognition': sr_ok,
        'googletrans': gt_ok,
        'languages': list(config.DEFAULT_LANGUAGES),
        'max_upload_mb': int(config.MAX_UPLOAD_MB),
    }


def _temp_path(suffix):
    fd, path = tempfile.mkstemp(prefix='stt-', suffix=suffix)
    os.close(fd)
    return path


def to_wav(data):
    """Write the audio to a temp file, convert it to 16 kHz mono WAV. Returns the WAV path."""
    src = _temp_path('.in')
    dst = _temp_path('.wav')
    try:
        with open(src, 'wb') as f:
            f.write(data)
        cmd = [config.FFMPEG, '-y', '-loglevel', 'error', '-i', src, '-ac', '1', '-ar', '16000', dst]
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=config.CONVERT_TIMEOUT)
        except FileNotFoundError:
            raise VoiceError(500, 'ffmpeg is not installed on this server (see README)')
        except subprocess.TimeoutExpired:
            raise VoiceError(422, 'audio conversion took too long (file too long or broken)')
        if r.returncode != 0:
            raise VoiceError(422, 'could not read this audio file (not audio, or broken)')
        if not os.path.exists(dst) or os.path.getsize(dst) < 1000:
            raise VoiceError(422, 'the audio is empty')
        return dst
    finally:
        try:
            os.unlink(src)
        except OSError:
            pass


_translator = None
_translator_lock = threading.Lock()


def _get_translator():
    """One shared googletrans client (creating one per request leaks sockets)."""
    global _translator
    with _translator_lock:
        if _translator is None:
            from googletrans import Translator
            _translator = Translator()
        return _translator


def detect_language(text):
    """googletrans label. If googletrans fails (it is unofficial and can be rate-limited), a script check is used."""
    try:
        lang = _get_translator().detect(text).lang   # lock is only held while the client is created
        if lang:
            return str(lang)
    except Exception:
        pass
    if any('\u0d80' <= ch <= '\u0dff' for ch in text):
        return 'si'
    if any('\u0b80' <= ch <= '\u0bff' for ch in text):
        return 'ta'
    return 'en'


# Google does not always report a confidence (for example si-LK never does). A candidate without one
# gets this neutral score. Confidence values are not comparable across languages, so keep the list short.
NO_CONFIDENCE = 0.5


def pick_best(candidates):
    """candidates: [{'language', 'text', 'confidence' (float or None), 'error' (str or '')}] in priority order.
    Returns the chosen candidate dict, or None. Ties keep the earlier language in the list."""
    best = None
    for c in candidates:
        if c.get('error') or not str(c.get('text') or '').strip():
            continue
        score = c['confidence'] if c.get('confidence') is not None else NO_CONFIDENCE
        if best is None or score > best[0]:
            best = (score, c)
    return best[1] if best else None


def transcribe(data, languages):
    """Audio bytes → {'text', 'language', 'detected', 'confidence', 'candidates', 'ms'}. Raises VoiceError."""
    if not data:
        raise VoiceError(400, 'empty body: send the audio file as the request body')
    try:
        import speech_recognition as sr
    except ImportError:
        raise VoiceError(500, 'SpeechRecognition is not installed on this server (see README)')

    started = time.time()
    wav = to_wav(data)
    candidates = []
    try:
        rec = sr.Recognizer()
        with sr.AudioFile(wav) as source:
            audio = rec.record(source)
        for lang in languages:
            cand = {'language': lang, 'text': '', 'confidence': None, 'error': ''}
            try:
                res = rec.recognize_google(audio, language=lang, show_all=True)
            except sr.UnknownValueError:
                cand['error'] = 'no speech'
                candidates.append(cand)
                continue
            except sr.RequestError as e:
                cand['error'] = 'service error (' + str(e)[:80] + ')'
                candidates.append(cand)
                continue
            alts = res.get('alternative', []) if isinstance(res, dict) else []
            top = alts[0] if alts else {}
            cand['text'] = str(top.get('transcript', '')).strip()
            if 'confidence' in top:
                cand['confidence'] = round(float(top['confidence']), 3)
            if not cand['text']:
                cand['error'] = 'empty'
            candidates.append(cand)
    finally:
        try:
            os.unlink(wav)
        except OSError:
            pass

    best = pick_best(candidates)
    if best is None:
        if candidates and all('service error' in c['error'] for c in candidates):
            raise VoiceError(502, 'Google speech service did not answer. Try again in a minute.')
        reasons = '; '.join(f"{c['language']}: {c['error'] or 'empty'}" for c in candidates)
        raise VoiceError(422, 'no speech could be recognised (' + reasons[:300] + ')')

    # short summary for the logs and for debugging: no full text of the losers
    summary = [{'language': c['language'], 'confidence': c['confidence'], 'chars': len(c['text']),
                'chosen': c is best, 'error': c['error']} for c in candidates]
    out = {
        'ok': True,
        'text': best['text'],
        'language': best['language'],
        'confidence': best['confidence'] if best['confidence'] is not None else NO_CONFIDENCE,
        'detected': detect_language(best['text']),
        'candidates': summary,
        'ms': int((time.time() - started) * 1000),
    }
    return out
