# Voice Quality Evaluation & STT/TTS Recovery Service

A standalone Django service that evaluates **one voice interaction turn**
(caller audio → STT result → TTS output) and returns **one explainable
recovery decision**:

`accept` · `retry_stt` · `retry_tts` · `switch_provider` · `ask_repeat` · `safe_fallback`

Every decision comes with the policy that matched, the findings that
triggered it (observed value vs. threshold) and a trace of why earlier
policies did not match. All thresholds and policies live in
[`config/rules.yaml`](config/rules.yaml), not in the code.

- Architecture and design decisions: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)
- Production improvements: [`docs/ARCHITECTURE.md#what-i-would-improve-for-production`](docs/ARCHITECTURE.md#what-i-would-improve-for-production)

---

## Quick start

### Local (Python 3.11+)

```bash
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python manage.py migrate
python manage.py runserver
```

Optional, for the admin panel: `python manage.py createsuperuser`

### Docker

```bash
docker compose up --build
```

The SQLite database is kept in `./data`, and `./config` is mounted so
`rules.yaml` can be edited without rebuilding.

### Useful URLs

| URL | What |
|---|---|
| `http://localhost:8000/api/docs/` | Swagger UI (OpenAPI schema at `/api/schema/`) |
| `http://localhost:8000/report/` | Quality report page (filters + provider table) |
| `http://localhost:8000/admin/` | Django admin with filters and search |
| `http://localhost:8000/api/health/` | Health check + loaded rule version |

## Running the tests

```bash
python manage.py test
```

66 tests cover rule resolution and validation, each audio / STT / TTS check,
provider adapters, every policy path, and the API (JSON + multipart
submission, detail, filters, date range, stats, validation errors, report page).

Test audio is synthetic and deterministic ([`tests/audio_factory.py`](tests/audio_factory.py)).
The same clips are written to [`fixtures/audio/`](fixtures/audio) with:

```bash
python scripts/generate_audio_fixtures.py
```

| Fixture | Expected result |
|---|---|
| `clean_speech.wav` | passes all audio checks |
| `stereo_clean.wav` | passes (channels are mixed to mono) |
| `silence.wav` | `too_quiet`, `mostly_silent` → `ask_repeat` |
| `too_quiet.wav` | `too_quiet` → `ask_repeat` |
| `clipped.wav` | `clipping` → `ask_repeat` (or `retry_tts` when sent as TTS audio) |
| `noisy.wav` | `low_snr` warning only (score drops, still accepted) |
| `too_short.wav` | `too_short` → `ask_repeat` |

---

## API

Base path: `/api/`. Full, interactive documentation at `/api/docs/`.

### `POST /api/evaluations/` — evaluate a turn

At least one of `stt` or `tts` is required. Audio is optional.

| Field | Type | Notes |
|---|---|---|
| `call_id`, `bot_id` | string | required |
| `turn_id` | string | optional |
| `language` | string | `tr`, `en`, `tr-TR`…; unknown languages fall back to `default_language` |
| `audio_base64` | string | caller audio, PCM WAV, base64 (data URLs accepted) |
| `stt.provider` | string | required inside `stt` |
| `stt.transcript` | string | may be empty (that is a finding, not a validation error) |
| `stt.confidence` | float 0–1 | optional; if missing, the confidence check is skipped |
| `stt.latency_ms` | float | optional |
| `stt.attempt` | int ≥ 1 | 1 = first try; drives switch / give-up policies |
| `tts.provider` | string | selects the metadata adapter and provider thresholds |
| `tts.text` | string | text that was synthesised |
| `tts.duration_ms`, `tts.latency_ms`, `tts.sample_rate` | number | neutral fields; optional if the adapter can read them from `provider_metadata` |
| `tts.provider_metadata` | object | raw vendor metadata |
| `tts.audio_base64` | string | optional generated audio; audio checks are re-run on it |
| `tts.attempt` | int ≥ 1 | |

Multipart is also accepted: a `data` field containing the same JSON, plus
optional `audio` and `tts_audio` WAV files.

**Example request**

```bash
curl -X POST http://localhost:8000/api/evaluations/ \
  -H "Content-Type: application/json" \
  -d '{
    "call_id": "call-42", "bot_id": "support-bot", "turn_id": "turn-3", "language": "tr",
    "stt": {"provider": "demo_stt", "transcript": "siparişim nerde",
            "confidence": 0.42, "latency_ms": 900, "attempt": 1},
    "tts": {"provider": "demo_tts", "text": "Siparişiniz yarın teslim edilecek.",
            "duration_ms": 2600, "latency_ms": 650}
  }'
```

With audio files:

```bash
curl -X POST http://localhost:8000/api/evaluations/ \
  -F "data=$(cat examples/01_accept.json)" \
  -F "audio=@fixtures/audio/silence.wav"
```

**Example response** (`201 Created`, shortened: `signals` holds every
finding per component, `trace` holds every policy that was checked)

```json
{
  "id": "6a64a942-125d-4347-a0a6-09ab6872a552",
  "call_id": "call-42",
  "bot_id": "support-bot",
  "language": "tr",
  "quality_score": 0.6571,
  "scores": { "audio": null, "stt": 0.4, "tts": 1.0 },
  "decision": {
    "action": "retry_stt",
    "target": null,
    "policy": "stt_failed",
    "explanation": "The input audio is not the problem, but the transcript is not reliable; re-run STT. Evidence: STT confidence 0.42 (minimum 0.6).",
    "triggered_by": [
      {
        "rule": "stt.low_confidence", "component": "stt", "passed": false,
        "severity": "critical", "penalty": 0.6, "value": 0.42, "threshold": 0.6,
        "message": "STT confidence 0.42 (minimum 0.6)."
      }
    ],
    "trace": [
      { "policy": "repeated_failures_in_call", "matched": false,
        "conditions": [{ "condition": "prior_failures_gte", "holds": false,
                         "detail": "prior failures in call = 0 (needs >= 3)" }] },
      "…",
      { "policy": "stt_failed", "matched": true,
        "conditions": [{ "condition": "component_failed", "holds": true,
                         "detail": "failed components ['stt'] among ['stt']" }] }
    ]
  },
  "signals": { "audio": { "evaluated": false, "…": "…" }, "stt": { "…": "…" }, "tts": { "…": "…" } },
  "request_payload": { "…": "stored request; audio replaced by sha256 + size" },
  "rules_version": "2026-01",
  "rules_fingerprint": "226e5eadb633",
  "created_at": "2026-09-25T21:29:46.416461Z"
}
```

Validation errors return `400` with DRF's usual field → messages format.

Ready-made requests covering every action are in [`examples/`](examples):

```bash
for f in examples/*.json; do
  curl -s -X POST localhost:8000/api/evaluations/ -H "Content-Type: application/json" -d @"$f" \
  | python -c "import json,sys; d=json.load(sys.stdin)['decision']; print('$f', d['action'], d['target'] or '')"
done
```

### `GET /api/evaluations/{id}/` — one evaluation

Returns the same body as the POST response. `404` if the id does not exist.

### `GET /api/evaluations/` — list with filters

Filters (all optional, combinable): `call_id`, `bot_id`, `provider`
(matches STT **or** TTS provider), `stt_provider`, `tts_provider`,
`action`, `language`, `date_from`, `date_to` (`YYYY-MM-DD` or ISO datetime,
both inclusive). Paginated with `?page=N` (20 per page).

```bash
curl "http://localhost:8000/api/evaluations/?bot_id=support-bot&action=retry_stt&date_from=2026-09-01"
```

```json
{
  "count": 1, "next": null, "previous": null,
  "results": [
    {
      "id": "6a64a942-125d-4347-a0a6-09ab6872a552", "call_id": "call-42", "turn_id": "turn-3",
      "bot_id": "support-bot", "language": "tr", "stt_provider": "demo_stt", "tts_provider": "demo_tts",
      "quality_score": 0.6571, "scores": { "audio": null, "stt": 0.4, "tts": 1.0 },
      "action": "retry_stt", "action_target": "", "policy_name": "stt_failed",
      "created_at": "2026-09-25T21:29:46.416461Z"
    }
  ]
}
```

### `GET /api/evaluations/stats/` — aggregate statistics

Accepts the same filters as the list endpoint.

```json
{
  "total_evaluations": 2,
  "avg_quality_score": 0.8286,
  "avg_component_scores": { "audio": null, "stt": 0.7, "tts": 1.0 },
  "failure_rate": 0.5,
  "retry_rate": 0.5,
  "actions": [ { "action": "accept", "count": 1 }, { "action": "retry_stt", "count": 1 } ],
  "stt_providers": [
    { "provider": "demo_stt", "evaluations": 2, "avg_latency_ms": 650.0, "max_latency_ms": 900.0,
      "avg_score": 0.7, "failure_rate": 0.5 }
  ],
  "tts_providers": [
    { "provider": "demo_tts", "evaluations": 2, "avg_latency_ms": 650.0, "max_latency_ms": 650.0,
      "avg_score": 1.0, "failure_rate": 0.0 }
  ]
}
```

Definitions:

- `failure_rate` — share of turns whose action is not `accept`.
- `retry_rate` — share of turns whose action is `retry_stt` or `retry_tts`.
- provider `failure_rate` — share of turns in which **that provider's
  component** failed (a TTS provider is not blamed for an STT failure).

---

## Configuration

| Environment variable | Default | Purpose |
|---|---|---|
| `VOICE_EVAL_RULES_PATH` | `config/rules.yaml` | rule file |
| `VOICE_EVAL_MAX_AUDIO_BYTES` | 10 MB | per-file audio limit |
| `DATABASE_PATH` | `db.sqlite3` | SQLite location |
| `DJANGO_DEBUG` | `true` | set `false` in production |
| `DJANGO_SECRET_KEY` | dev key | **must** be set in production |
| `DJANGO_ALLOWED_HOSTS` | `*` | comma separated |

### Changing the rules

Edit `config/rules.yaml`. Changes are picked up without a restart (the file
is cached by modification time) and each evaluation stores the rule
`version` and a content `fingerprint`, so results stay traceable. The file
is validated on load; an unknown action, severity or policy condition fails
fast with a clear error.

Examples of what can be changed without touching code:

```yaml
# Stricter confidence for one STT provider
providers:
  stt:
    my_provider:
      checks:
        low_confidence:
          min_confidence: 0.75

# Disable a check
stt:
  checks:
    repeated_tokens:
      enabled: false
```

## Project layout

```
config/rules.yaml              thresholds, lexicons, provider overrides, policy
evaluations/
  services/                    pure Python, no Django imports
    rules.py                   load / validate / resolve the rule file
    audio.py                   WAV decoding + audio checks
    stt.py                     transcript checks (TR/EN aware)
    tts.py                     TTS checks
    providers.py               provider-neutral TTS metadata adapters
    policy.py                  ordered, explainable policy engine
    pipeline.py                rules -> evaluators -> score -> policy
    types.py                   Finding, ComponentResult, scoring
  repository.py                persistence, filters, statistics (ORM)
  models.py / serializers.py / views.py / urls.py / admin.py
  templates/evaluations/report.html
tests/                         66 tests + synthetic audio factory
fixtures/audio/                generated WAV fixtures
examples/                      request bodies for every action
scripts/generate_audio_fixtures.py
docs/ARCHITECTURE.md
```
