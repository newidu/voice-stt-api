# Voice STT API — Copy-Paste Prompt

ඔබට WhatsApp voice note එකක් text බවට පරිවර්තනය කිරීමට මෙම Voice STT API එක භාවිතා කරන්න.

## API Details

- **API URL:** `https://voice-stt-api-production.up.railway.app`
- **API Key / Token:** `1`
- **Authentication Header:** `X-Api-Token: 1`
- **Transcription Endpoint:** `POST /transcribe`
- **Health Check:** `GET /health`
- **Supported Languages:** `si-LK`, `en-US`
- **Mixed Language:** Sinhala + English voice එකකට දෙකම auto mode ලෙස try කරයි
- **High Quality:** Railway එකේ `GEMINI_API_KEY` දාලා `TRANSCRIPTION_PROVIDER=gemini` කළොත් Gemini mixed-language transcription භාවිතා කරයි

## AI Agent Prompt

```text
ඔබට user ගෙන් voice/audio file එකක් ලැබුණොත්, එය Voice STT API එකට යවා text එක ලබාගන්න.

API URL:
https://voice-stt-api-production.up.railway.app

Transcription request:
POST https://voice-stt-api-production.up.railway.app/transcribe?languages=si-LK,en-US

Headers:
X-Api-Token: 1
Content-Type: audio/ogg

Audio file එක request body එකේ raw binary data ලෙස යවන්න.

API response එකේ "ok" true නම් response එකේ "text" field එක user කියූ වචන ලෙස භාවිතා කරන්න.
"language" field එකෙන් හඳුනාගත් භාෂාව බලන්න.
"ok" false නම් error එක පරීක්ෂා කර user ට audio එක නැවත යවන්න කියන්න.

User voice එක Sinhala නම් Sinhala text එකෙන් පිළිතුරු දෙන්න.
User voice එක English නම් English text එකෙන් පිළිතුරු දෙන්න.
Sinhala සහ English mix නම් user භාවිතා කළ භාෂා mix එකට ගැළපෙන ලෙස පිළිතුරු දෙන්න.
API response එකේ "detected" field එක "si-en" නම් එය Sinhala-English mixed voice එකක් ලෙස සලකන්න.
```

## cURL Test

```bash
curl --http1.1 -X POST \
  "https://voice-stt-api-production.up.railway.app/transcribe?languages=si-LK,en-US" \
  -H "X-Api-Token: 1" \
  -H "Content-Type: audio/ogg" \
  --data-binary @voice-note.ogg
```

## PHP Example

```php
$apiUrl = 'https://voice-stt-api-production.up.railway.app';
$apiToken = '1';
$audioFile = '/path/to/voice-note.ogg';

$ch = curl_init($apiUrl . '/transcribe?languages=si-LK,en-US');
curl_setopt_array($ch, [
    CURLOPT_POST => true,
    CURLOPT_POSTFIELDS => file_get_contents($audioFile),
    CURLOPT_HTTPHEADER => [
        'X-Api-Token: ' . $apiToken,
        'Content-Type: audio/ogg',
    ],
    CURLOPT_RETURNTRANSFER => true,
    CURLOPT_TIMEOUT => 120,
]);

$response = curl_exec($ch);
$httpStatus = curl_getinfo($ch, CURLINFO_HTTP_CODE);
curl_close($ch);

$result = json_decode($response, true);

if ($httpStatus === 200 && !empty($result['ok'])) {
    $text = $result['text'];
    $language = $result['language'];
    echo $text;
} else {
    echo 'Voice transcription failed: ' . ($result['error'] ?? 'Unknown error');
}
```

## Health Check

Open this URL in a browser:

```text
https://voice-stt-api-production.up.railway.app/health
```

Expected result:

```json
{
  "ok": true,
  "ffmpeg": true,
  "speech_recognition": true,
  "googletrans": true,
  "languages": ["si-LK", "en-US"],
  "max_upload_mb": 25
}
```

> **Security note:** `API_TOKEN` එක දැනට testing සඳහා `1` ලෙස තබා ඇත. Public service එකක් නිසා production භාවිතයට දිග random token එකක් යොදා Railway Variables සහ agent configuration දෙකේම එකම value එක update කරන්න.
