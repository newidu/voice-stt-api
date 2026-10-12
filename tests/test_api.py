"""
Tests for the voice API. Run from the project folder:
    venv/bin/python -m unittest discover -s tests -v

The real-audio test needs internet (Google's speech endpoint). If Google cannot be reached it is skipped.
"""
import http.client
import json
import os
import sys
import threading
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import config  # noqa: E402
import server  # noqa: E402
import stt  # noqa: E402

TOKEN = 'test-token-123'
SAMPLE = os.path.join(HERE, 'fixtures', 'en-sample.ogg')
_saved = {}


def setUpModule():
    for k in ('API_TOKEN', 'MAX_UPLOAD_MB', 'FFMPEG'):
        _saved[k] = getattr(config, k)
    config.API_TOKEN = TOKEN


def tearDownModule():
    for k, v in _saved.items():
        setattr(config, k, v)


class ApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = server.make_server('127.0.0.1', 0)
        cls.port = cls.srv.server_address[1]
        cls.thread = threading.Thread(target=cls.srv.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def call(self, method, path, body=None, token=TOKEN):
        conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=120)
        headers = {}
        if token is not None:
            headers['X-Api-Token'] = token
        if body is not None:
            headers['Content-Type'] = 'application/octet-stream'
        conn.request(method, path, body=body, headers=headers)
        r = conn.getresponse()
        data = r.read()
        conn.close()
        try:
            return r.status, json.loads(data.decode('utf8'))
        except ValueError:
            return r.status, {'raw': data[:200]}

    # ---- plain checks
    def test_health_reports_parts(self):
        status, j = self.call('GET', '/health', token=None)
        self.assertEqual(status, 200)
        for key in ('ok', 'ffmpeg', 'speech_recognition', 'googletrans', 'languages', 'max_upload_mb'):
            self.assertIn(key, j)

    def test_unknown_path_is_404(self):
        status, _ = self.call('GET', '/nope', token=None)
        self.assertEqual(status, 404)

    def test_missing_token_is_401(self):
        status, j = self.call('POST', '/transcribe', body=b'abc', token=None)
        self.assertEqual(status, 401)
        self.assertFalse(j['ok'])

    def test_wrong_token_is_401(self):
        status, _ = self.call('POST', '/transcribe', body=b'abc', token='wrong')
        self.assertEqual(status, 401)

    def test_empty_body_is_400(self):
        status, j = self.call('POST', '/transcribe', body=b'')
        self.assertEqual(status, 400)
        self.assertIn('empty', j['error'])

    def test_bad_language_tag_is_400(self):
        status, j = self.call('POST', '/transcribe?languages=si-LK;drop', body=b'abc')
        self.assertEqual(status, 400)
        self.assertIn('bad language', j['error'])

    def test_too_large_is_413(self):
        old = config.MAX_UPLOAD_MB
        config.MAX_UPLOAD_MB = 0
        try:
            status, j = self.call('POST', '/transcribe', body=b'x' * 10)
        finally:
            config.MAX_UPLOAD_MB = old
        self.assertEqual(status, 413)

    def test_not_audio_is_422(self):
        status, j = self.call('POST', '/transcribe', body=b'this is plain text, not audio ' * 200)
        self.assertEqual(status, 422, j)
        self.assertFalse(j['ok'])

    def test_missing_ffmpeg_gives_clear_500(self):
        old = config.FFMPEG
        config.FFMPEG = '/nonexistent/ffmpeg-for-test'
        try:
            status, j = self.call('POST', '/transcribe', body=b'x' * 5000)
        finally:
            config.FFMPEG = old
        self.assertEqual(status, 500)
        self.assertIn('ffmpeg is not installed', j['error'])

    # ---- real audio
    def test_real_voice_note_is_transcribed(self):
        with open(SAMPLE, 'rb') as f:
            data = f.read()
        status, j = self.call('POST', '/transcribe?languages=en-US,si-LK', body=data)
        if status == 502:
            self.skipTest('Google speech endpoint not reachable from here')
        self.assertEqual(status, 200, j)
        self.assertTrue(j['ok'])
        self.assertEqual(j['language'], 'en-US')
        self.assertEqual(j['detected'], 'en')
        self.assertIn('voice', j['text'].lower())
        self.assertIn('weather', j['text'].lower())


class ParseTest(unittest.TestCase):
    def test_defaults_when_empty(self):
        self.assertEqual(stt.parse_languages(''), list(config.DEFAULT_LANGUAGES))

    def test_list_is_split_and_trimmed(self):
        self.assertEqual(stt.parse_languages(' si-LK , en-US '), ['si-LK', 'en-US'])

    def test_bad_tag_raises(self):
        with self.assertRaises(stt.VoiceError):
            stt.parse_languages('en-US,<script>')


class PickBestTest(unittest.TestCase):
    def cand(self, lang, text='', conf=None, err=''):
        return {'language': lang, 'text': text, 'confidence': conf, 'error': err}

    def test_missing_confidence_gets_neutral_score(self):
        # the real case: Sinhala (no confidence) beats English garbage at 0.39
        best = stt.pick_best([self.cand('si-LK', 'මම අද'), self.cand('en-US', 'mama at the Columbus', 0.385)])
        self.assertEqual(best['language'], 'si-LK')

    def test_higher_confidence_wins(self):
        best = stt.pick_best([self.cand('si-LK', 'x'), self.cand('en-US', 'hello', 0.93)])
        self.assertEqual(best['language'], 'en-US')

    def test_errors_and_empty_are_skipped(self):
        best = stt.pick_best([self.cand('ta-LK', err='no speech'), self.cand('si-LK', '   '), self.cand('en-US', 'hi', 0.2)])
        self.assertEqual(best['language'], 'en-US')

    def test_nothing_usable_returns_none(self):
        self.assertIsNone(stt.pick_best([self.cand('si-LK', err='no speech'), self.cand('en-US')]))

    def test_tie_keeps_the_earlier_language(self):
        best = stt.pick_best([self.cand('si-LK', 'a'), self.cand('en-US', 'b')])
        self.assertEqual(best['language'], 'si-LK')

    def test_sinhala_script_beats_confident_phonetic_english(self):
        best = stt.pick_best([
            self.cand('si-LK', 'මම අද office යනවා'),
            self.cand('en-US', 'mama ada office yanawa', 0.95),
        ])
        self.assertEqual(best['language'], 'si-LK')

    def test_mixed_script_is_detected_as_si_en(self):
        self.assertEqual(stt.detect_language('මම අද office යනවා'), 'si-en')


if __name__ == '__main__':
    unittest.main()
