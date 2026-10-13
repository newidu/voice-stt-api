#!/usr/bin/env python3
"""
server.py — the voice API. The WhatsApp agent sends a voice note here and gets the text back.

  GET  /health                     → what is installed (ffmpeg, SpeechRecognition, googletrans)
  POST /transcribe                 → body = the raw audio file bytes
        header  X-Api-Token: <API_TOKEN>        (required when API_TOKEN is set in config.py)
        query   ?languages=si-LK,en-US,ta-LK    (optional, default from config.py)
        reply   {"ok": true, "text": "...", "language": "en-US", "detected": "en", "confidence": 0.93}
                {"ok": false, "error": "..."}   with HTTP 4xx/5xx

Run:  venv/bin/python server.py
"""
import hmac
import json
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import config
import stt
from stt import VoiceError

VERSION = '1.2.0'
LOOPBACK = ('127.0.0.1', 'localhost', '::1')


def log(msg):
    print(time.strftime('%Y-%m-%d %H:%M:%S'), msg, flush=True)


class Handler(BaseHTTPRequestHandler):
    server_version = 'voice-stt/' + VERSION
    protocol_version = 'HTTP/1.1'

    def log_message(self, fmt, *args):
        log(self.address_string() + ' ' + (fmt % args))

    def _send(self, status, obj):
        body = json.dumps(obj, ensure_ascii=False).encode('utf8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Connection', 'close')
        self.end_headers()
        self.wfile.write(body)
        self.close_connection = True

    def do_GET(self):
        path = urlparse(self.path).path
        if path == '/version':
            return self._send(200, {'ok': True, 'version': VERSION})
        if path in ('/', '/health'):
            return self._send(200, stt.health())
        self._send(404, {'ok': False, 'error': 'not found'})

    def do_POST(self):
        url = urlparse(self.path)
        if url.path != '/transcribe':
            return self._send(404, {'ok': False, 'error': 'not found'})

        if config.API_TOKEN:
            given = self.headers.get('X-Api-Token', '')
            if not hmac.compare_digest(given.encode('utf8'), config.API_TOKEN.encode('utf8')):
                log('AUTH REJECTED from ' + self.address_string())
                return self._send(401, {'ok': False, 'error': 'bad or missing X-Api-Token'})

        if self.headers.get('Transfer-Encoding'):
            return self._send(411, {'ok': False, 'error': 'send a Content-Length header (no chunked upload)'})
        length = self.headers.get('Content-Length', '')
        if not length.isdigit():
            return self._send(411, {'ok': False, 'error': 'Content-Length header required'})
        size = int(length)
        if size <= 0:
            return self._send(400, {'ok': False, 'error': 'empty body'})
        if size > int(config.MAX_UPLOAD_MB) * 1024 * 1024:
            return self._send(413, {'ok': False, 'error': f'file too large (max {config.MAX_UPLOAD_MB} MB)'})

        data = self.rfile.read(size)
        if len(data) != size:
            return self._send(400, {'ok': False, 'error': 'upload was cut short'})

        try:
            langs = stt.parse_languages(parse_qs(url.query).get('languages', [''])[0])
            res = stt.transcribe(data, langs)
        except VoiceError as e:
            log(f'FAIL {e.status} {e.message[:160]}')
            return self._send(e.status, {'ok': False, 'error': e.message})
        except Exception as e:  # never leak a traceback to the caller
            log('ERROR ' + repr(e)[:300])
            return self._send(500, {'ok': False, 'error': 'internal error (see server log)'})

        # log lengths only, never the spoken text
        cands = ' '.join(f"{c['language']}={c['confidence'] if c['confidence'] is not None else 'n/a'}"
                         f"{'*' if c['chosen'] else ''}" for c in res['candidates'])
        log(f"OK lang={res['language']} detected={res['detected']} chars={len(res['text'])} ms={res['ms']} [{cands}]")
        self._send(200, {k: res[k] for k in ('ok', 'text', 'language', 'detected', 'confidence', 'candidates')})


def make_server(host=None, port=None):
    srv = ThreadingHTTPServer((host or config.HOST, config.PORT if port is None else port), Handler)
    srv.daemon_threads = True
    return srv


def main():
    host = config.HOST
    if not config.API_TOKEN and host not in LOOPBACK:
        print('REFUSED TO START: API_TOKEN is empty but the server listens on a public address.\n'
              'Set API_TOKEN in config.py first. Make one with:\n'
              '  python3 -c "import secrets; print(secrets.token_urlsafe(32))"', file=sys.stderr)
        sys.exit(2)
    srv = make_server()
    log(f'voice API {VERSION} on {host}:{srv.server_address[1]} · token={"on" if config.API_TOKEN else "off (local only)"}')
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


if __name__ == '__main__':
    main()
