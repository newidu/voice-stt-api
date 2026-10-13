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
