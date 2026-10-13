"""
config.py — settings for the voice API.

Values can be overridden with environment variables for hosted deployments.
"""
import os


# Render and other hosts provide PORT dynamically.
HOST = os.getenv('HOST', '0.0.0.0')
PORT = int(os.getenv('PORT', '8787'))

# Shared secret sent as the X-Api-Token header.
# Required when the server listens on a public address.
API_TOKEN = os.getenv('API_TOKEN', '')

# Comma-separated language tags, for example: si-LK,en-US
DEFAULT_LANGUAGES = [
    lang.strip() for lang in os.getenv('DEFAULT_LANGUAGES', 'si-LK,en-US').split(',')
    if lang.strip()
]

MAX_UPLOAD_MB = int(os.getenv('MAX_UPLOAD_MB', '25'))
FFMPEG = os.getenv('FFMPEG', 'ffmpeg')
CONVERT_TIMEOUT = int(os.getenv('CONVERT_TIMEOUT', '60'))

# "auto" uses Gemini when GEMINI_API_KEY is configured, otherwise the free
# Google fallback. Set TRANSCRIPTION_PROVIDER=gemini to require Gemini.
TRANSCRIPTION_PROVIDER = os.getenv('TRANSCRIPTION_PROVIDER', 'auto').strip().lower()
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY', '').strip()
GEMINI_MODEL = os.getenv('GEMINI_MODEL', 'gemini-3.5-transcribe').strip()
