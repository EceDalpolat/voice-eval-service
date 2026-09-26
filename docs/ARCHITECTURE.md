# Architecture

## Request flow

```
POST /api/evaluations/
      │
      ▼
 views.py ─── EvaluationRequestSerializer (validation, base64 → bytes)
      │
      ▼
 repository.create_evaluation()
      │   prior_failures_in_call(call_id)   ← DB
      ▼
 services/pipeline.evaluate_turn()
      │
      ├─ rules.resolve(language, stt_provider, tts_provider)
      ├─ audio.evaluate_audio()   → ComponentResult(audio)
      ├─ stt.evaluate_stt()       → ComponentResult(stt)   (uses audio duration)
      ├─ tts.evaluate_tts()       → ComponentResult(tts)   (adapter + optional audio checks)
      ├─ score_component() for each, overall weighted score
      └─ policy.decide()          → Decision(action, target, policy, explanation, triggered_by, trace)
      │
      ▼
 Evaluation row saved (decision + signals + rule version) → 201 response
```

## Main decisions

**1. Pure evaluation core, thin Django shell.**
Everything in `evaluations/services/` is plain Python with no ORM or HTTP
imports. It receives dicts and bytes and returns dataclasses. This keeps the
logic unit-testable in milliseconds and lets the same `evaluate_turn()` run
in an API request, a Celery worker or a batch re-evaluation job.
`repository.py` is the only place that talks to the database.

**2. Findings as the common language.**
Every check produces a `Finding` with the rule name, observed value,
threshold, severity, penalty and a human-readable message. Scores, the
policy engine, the API response and the report page are all built from
findings, so explanations never drift from the logic that produced them.

**3. Configuration instead of hard-coded rules.**
`config/rules.yaml` holds every threshold, penalty, enable flag, language
lexicon, provider override and the recovery policy. It is resolved per
request in a fixed order: defaults → language overrides → STT provider
overrides → TTS provider overrides. The file is validated on load (unknown
actions, severities or policy conditions fail fast), cached by modification
time (edits apply without restart) and fingerprinted; the version and
fingerprint are stored on every evaluation so a past decision can always be
traced back to the exact rules that made it.

**4. Severity + penalty scoring.**
Each component starts at 1.0 and loses the penalty of every failed check.
A component *fails* if any critical check fails, or if warnings pile up
below `component_fail_below`. This separates "hard" problems (empty
transcript) from "soft" ones (slightly high latency) while still letting
many soft problems add up. The overall quality score is a weighted mean of
the components that were actually evaluated (weights are re-normalised
when, for example, no audio was sent).

**5. Ordered, first-match policy engine.**
Policies are an ordered list; the first one whose conditions all hold wins.
Order encodes priority, which is easy to reason about and to explain:

1. Too many failed turns in this call → `safe_fallback` (stop the retry loop).
2. Caller audio unusable → `ask_repeat` (re-running STT on bad audio cannot help).
3. Transcript is only filler words → `ask_repeat`.
4. STT failed: attempt 1 → `retry_stt`, attempt 2 → `switch_provider(stt)`, attempt 3 → `ask_repeat`.
5. TTS failed: attempt 1 → `retry_tts`, attempt 2 → `switch_provider(tts)`, attempt 3 → `safe_fallback`.
6. Otherwise `accept` (warnings are still listed in the explanation).

STT is checked before TTS because a TTS answer built on a wrong transcript
is wrong regardless of how good it sounds. The engine supports four small,
declarative conditions (`prior_failures_gte`, `component_failed`,
`any_failed`, `attempt_gte`). A general expression language was avoided on
purpose: it would be harder to validate and to explain. The response
includes a trace showing, for each policy checked, which condition failed.

**6. Retry state is explicit.**
The caller sends `attempt` per component; the service counts earlier
non-accepted turns in the same call from the database. The service itself
stays stateless per request, and both inputs are visible in the stored
payload and in the trace.

**7. Provider-neutral TTS metadata via adapters.**
Vendors report duration/latency in different units and shapes. A registry
of small adapter functions maps `provider_metadata` onto one
`NormalizedTTS` structure; explicit neutral fields always win. Unknown
providers fall back to the generic adapter. The two vendor-shaped adapters
in `providers.py` are illustrative and must be aligned with real vendor
responses.

**8. Lightweight, dependency-free audio analysis.**
WAV decoding uses the standard `wave` module and numpy only (no native
libraries). Signals: duration, RMS loudness (dBFS), peak, clipping ratio,
silence ratio (per 20 ms frame), and a rough SNR estimate (95th vs. 10th
percentile frame energy). The same audio checks are re-used on generated
TTS audio when it is provided.

**9. Turkish and English awareness.**
Language-specific filler words, alphabet and speaking rate live in the
rule file. Tokenisation handles Turkish casing (`I → ı`, `İ → i`), the
garbage-character check accepts `çğıöşü`, and English uses a slightly
stricter confidence threshold as an example of a language override.

**10. One table for evaluation + decision.**
They are always written and read together, and every report query filters
on both. Frequently aggregated values (scores, latencies, component
failure flags, providers, action) are denormalised into indexed columns;
full signals, the request and the trace are kept as JSON for auditing.
Raw audio is never stored, only its SHA-256 and size.

**11. Django + DRF.**
Chosen to match the preferred stack: the ORM and migrations give the
persistence deliverables for free, the admin gives a filterable back
office, and drf-spectacular generates the OpenAPI docs. The quality report
page is a single server-rendered template reusing the stats query.

## Trade-offs and limitations

- The SNR estimate is a heuristic; it is a warning, not a critical check.
- Only PCM WAV input is decoded. Other formats are reported as
  `audio.unreadable` (a finding, not an HTTP error), so a corrupt clip still
  produces a decision (`ask_repeat`).
- Evaluation is synchronous. With the current checks a request takes a few
  milliseconds plus audio decoding time.
- SQLite is used for zero-setup local runs; the ORM code is database-agnostic.
- There is no authentication on the API; it is assumed to run inside a
  private network behind the voice platform.

## What I would improve for production

**Reliability and scale**
- PostgreSQL instead of SQLite, with connection pooling; time-based
  partitioning or retention for the evaluations table.
- Asynchronous evaluation: accept the turn, return `202` with an id, run
  `evaluate_turn()` in a Celery/RQ worker (the core is already framework
  free). For the live call path, keep a fast synchronous mode that skips
  heavy audio analysis.
- Idempotency key per turn (`call_id` + `turn_id` + attempt) to avoid
  duplicate rows on client retries.
- Audio stored in object storage (S3/GCS) with a reference instead of
  being sent inline; streaming decode for long clips.

**Quality of the signals**
- Replace the SNR heuristic with a proper VAD (e.g. WebRTC VAD or Silero)
  and a trained speech-quality model (MOS estimation).
- Word-level STT confidences, and comparing STT output against the TTS
  text of the previous turn to detect echo.
- For TTS, run a quick STT pass on the generated audio and compare it with
  the input text (round-trip check) to catch mispronunciations and truncation.
- Adaptive thresholds learned from data: per provider / language / bot,
  compute rolling percentiles and flag outliers instead of fixed values.

**Operations**
- Prometheus metrics (decisions per action, per-provider latency
  histograms, rule-evaluation time) and alerts on failure-rate spikes per
  provider, which could also drive automatic `switch_provider`.
- Rule management with review: store rule sets in the database with
  versions, validate and dry-run a new set against recent traffic before
  activation, and support gradual rollout per bot.
- Circuit breaker per provider fed by the stats endpoint.
- Authentication (API keys or mTLS), rate limiting, structured JSON logs
  with `call_id` correlation, and PII handling for transcripts (masking,
  retention policy, KVKK/GDPR compliance).
- CI pipeline: lint (ruff), type check (mypy), tests, image build and
  vulnerability scan.
