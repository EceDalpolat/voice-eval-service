"""Provider-neutral TTS metadata.

Every TTS vendor reports duration, latency and format differently
(seconds vs milliseconds, different key names, nested objects). The rest
of the service only works with `NormalizedTTS`. Supporting a new vendor
means writing one small adapter and registering it; nothing else changes.

NOTE: the vendor-shaped adapters below are illustrative examples of the
pattern. Their metadata keys are not copied from real vendor schemas and
must be adjusted to the actual API responses in production.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Callable

@dataclass
class NormalizedTTS:
    provider: str
    text: str
    duration_ms: float | None
    latency_ms: float | None
    sample_rate: int | None
    attempt: int

    def to_dict(self) -> dict:
        return asdict(self)


Adapter = Callable[[dict], NormalizedTTS]
_ADAPTERS: dict[str, Adapter] = {}


def register(*names: str):
    def decorator(fn: Adapter) -> Adapter:
        for name in names:
            _ADAPTERS[name] = fn
        return fn
    return decorator


def _first(*values):
    return next((v for v in values if v is not None), None)


@register("generic")
def generic_adapter(tts: dict) -> NormalizedTTS:
    """Default: the request already uses the neutral field names."""
    return NormalizedTTS(
        provider=tts.get("provider", "unknown"),
        text=tts.get("text") or "",
        duration_ms=tts.get("duration_ms"),
        latency_ms=tts.get("latency_ms"),
        sample_rate=tts.get("sample_rate"),
        attempt=tts.get("attempt", 1),
    )


@register("seconds_vendor_example")
def seconds_vendor_adapter(tts: dict) -> NormalizedTTS:
    """Example vendor that reports duration in seconds and latency under
    another key. Explicit neutral fields in the request still win."""
    base = generic_adapter(tts)
    meta = tts.get("provider_metadata") or {}
    seconds = meta.get("audio_length_seconds")
    base.duration_ms = _first(base.duration_ms, seconds * 1000 if seconds is not None else None)
    base.latency_ms = _first(base.latency_ms, meta.get("time_to_first_byte_ms"))
    base.sample_rate = _first(base.sample_rate, meta.get("output_sample_rate"))
    return base


@register("nested_vendor_example")
def nested_vendor_adapter(tts: dict) -> NormalizedTTS:
    """Example vendor that nests audio information."""
    base = generic_adapter(tts)
    audio = (tts.get("provider_metadata") or {}).get("audio") or {}
    base.duration_ms = _first(base.duration_ms, audio.get("durationMs"))
    base.sample_rate = _first(base.sample_rate, audio.get("sampleRateHz"))
    base.latency_ms = _first(base.latency_ms, (tts.get("provider_metadata") or {}).get("processingMs"))
    return base


def normalize_tts(tts: dict) -> NormalizedTTS:
    adapter = _ADAPTERS.get(tts.get("provider", ""), generic_adapter)
    return adapter(tts)


def registered_adapters() -> list[str]:
    return sorted(_ADAPTERS)
