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
        # Google Web Speech works best with clean speech in signed 16-bit,
        # mono 16 kHz PCM. Normalize loudness and remove inaudible rumble and
        # high-frequency codec noise without aggressive denoising that can
        # damage Sinhala consonants.
        audio_filter = 'highpass=f=70,lowpass=f=7600,loudnorm=I=-16:TP=-1.5:LRA=11'
        cmd = [config.FFMPEG, '-y', '-loglevel', 'error', '-i', src,
               '-vn', '-af', audio_filter, '-ac', '1', '-ar', '16000',
               '-sample_fmt', 's16', '-c:a', 'pcm_s16le', dst]
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


def _script_profile(text):
    """Return whether text contains Sinhala and/or Latin script characters."""
    has_si = any('\u0d80' <= ch <= '\u0dff' for ch in text)
    has_latin = any(('A' <= ch <= 'Z') or ('a' <= ch <= 'z') for ch in text)
    return has_si, has_latin


def detect_language(text):
    """Label Sinhala, English, or a Sinhala-English mixed transcript.

    googletrans is useful for single-language text, but it can collapse a mixed
    transcript to one label. Script detection is therefore authoritative when
    both Sinhala and Latin characters are present.
    """
    has_si, has_latin = _script_profile(text)
    if has_si and has_latin:
        return 'si-en'
    if has_si:
        return 'si'
    if has_latin:
        # Keep the normal English label for Latin-only responses.
        return 'en'
    try:
        lang = _get_translator().detect(text).lang   # lock is only held while the client is created
        if lang:
            return str(lang)
    except Exception:
        pass
    if any('\u0b80' <= ch <= '\u0bff' for ch in text):
        return 'ta'
    return 'en'


# Google does not always report a confidence (for example si-LK never does). A candidate without one
# gets this neutral score. Confidence values are not comparable across languages, so keep the list short.
NO_CONFIDENCE = 0.5
MIXED_CHUNK_SECONDS = 8


def pick_best(candidates):
    """candidates: [{'language', 'text', 'confidence' (float or None), 'error' (str or '')}] in priority order.
    Returns the chosen candidate dict, or None.

    For Sinhala/English auto mode, script evidence wins over a misleading
    confidence score. This prevents the English recognizer from winning with
    a confident phonetic rendering when the Sinhala recognizer returned real
    Sinhala text. Ties still keep the earlier language in the list.
    """
    best = None
    for c in candidates:
        if c.get('error') or not str(c.get('text') or '').strip():
            continue
        score = c['confidence'] if c.get('confidence') is not None else NO_CONFIDENCE
        text = str(c.get('text') or '')
        has_si, has_latin = _script_profile(text)
        lang = str(c.get('language') or '').lower()
        # Mixed-script output is the strongest evidence for a mixed-language
        # voice note. Otherwise prefer a real Sinhala-script result for si-LK.
        if has_si and has_latin:
            score += 1.0
        elif has_si and lang.startswith('si'):
            score += 0.75
        elif has_si and not lang.startswith('si'):
            score -= 0.25
        if best is None or score > best[0]:
            best = (score, c)
    return best[1] if best else None


def _choose_alternative(alternatives, language):
    """Choose the most useful Google alternative, preserving mixed scripts.

    Google often puts a mixed Sinhala/English phrase in an alternative rather
    than in the first result. Prefer that before using confidence so English
    words are not silently discarded from a Sinhala-English voice note.
    """
    usable = []
    for index, alt in enumerate(alternatives or []):
        text = str(alt.get('transcript', '')).strip()
        if not text:
            continue
        confidence = alt.get('confidence')
        try:
            confidence = round(float(confidence), 3) if confidence is not None else None
        except (TypeError, ValueError):
            confidence = None
        has_si, has_latin = _script_profile(text)
        mixed = has_si and has_latin
        # Mixed script is the primary signal. Then prefer confidence, then the
        # original Google ordering. Keep a small length preference for cases
        # where Google omits the confidence from alternatives.
        score = (1 if mixed else 0, confidence if confidence is not None else NO_CONFIDENCE,
                 min(len(text), 300) / 300, -index)
        usable.append((score, text, confidence))
    if not usable:
        return '', None
    _, text, confidence = max(usable, key=lambda item: item[0])
    return text, confidence


def _recognize_audio(audio, language, sr):
    """Recognize one AudioData object and return a normal candidate dict."""
    cand = {'language': language, 'text': '', 'confidence': None, 'error': ''}
    try:
        res = sr.Recognizer().recognize_google(audio, language=language, show_all=True)
    except sr.UnknownValueError:
        cand['error'] = 'no speech'
        return cand
    except sr.RequestError as e:
        cand['error'] = 'service error (' + str(e)[:80] + ')'
        return cand
    alts = res.get('alternative', []) if isinstance(res, dict) else []
    cand['text'], cand['confidence'] = _choose_alternative(alts, language)
    if not cand['text']:
        cand['error'] = 'empty'
    return cand


def _audio_chunks(audio, seconds=MIXED_CHUNK_SECONDS):
    """Split AudioData into ordered chunks without changing its sample format."""
    import speech_recognition as sr
    bytes_per_second = audio.sample_rate * audio.sample_width
    chunk_size = max(bytes_per_second * seconds, bytes_per_second)
    raw = audio.get_raw_data()
    return [
        sr.AudioData(raw[i:i + chunk_size], audio.sample_rate, audio.sample_width)
        for i in range(0, len(raw), chunk_size)
        if raw[i:i + chunk_size]
    ]


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
        chunks = _audio_chunks(audio)
        selected_parts = []
        selected_languages = []
        selected_confidences = []
        # Each chunk is recognized in every requested language. This lets a
        # sentence switch languages and preserves the parts instead of choosing
        # one whole-audio transcript and dropping the other language.
        for chunk in chunks:
            chunk_candidates = [_recognize_audio(chunk, lang, sr) for lang in languages]
            candidates.extend(chunk_candidates)
            best_chunk = pick_best(chunk_candidates)
            if best_chunk:
                selected_parts.append(best_chunk['text'])
                selected_languages.append(best_chunk['language'])
                if best_chunk['confidence'] is not None:
                    selected_confidences.append(best_chunk['confidence'])
    finally:
        try:
            os.unlink(wav)
        except OSError:
            pass

    if not selected_parts:
        if candidates and all('service error' in c['error'] for c in candidates):
            raise VoiceError(502, 'Google speech service did not answer. Try again in a minute.')
        reasons = '; '.join(f"{c['language']}: {c['error'] or 'empty'}" for c in candidates)
        raise VoiceError(422, 'no speech could be recognised (' + reasons[:300] + ')')

    text = ' '.join(selected_parts).strip()
    detected = detect_language(text)
    output_language = 'si-en' if len(set(selected_languages)) > 1 else selected_languages[0]
    # Short summary for logs and debugging: no full text of the alternatives.
    summary = [{'language': c['language'], 'confidence': c['confidence'], 'chars': len(c['text']),
                'chosen': False, 'error': c['error']} for c in candidates]
    out = {
        'ok': True,
        'text': text,
        'language': output_language,
        'confidence': round(sum(selected_confidences) / len(selected_confidences), 3) if selected_confidences else NO_CONFIDENCE,
        'detected': 'si-en' if output_language == 'si-en' else detected,
        'candidates': summary,
        'ms': int((time.time() - started) * 1000),
    }
    return out
