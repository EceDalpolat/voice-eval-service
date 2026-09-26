"""Audio quality evaluation.

Only the standard-library `wave` module and numpy are used, so there are no
native dependencies. Supported input: PCM WAV (8/16/32-bit, mono or multi-
channel; channels are averaged to mono).
"""
from __future__ import annotations

import io
import wave

import numpy as np

from .types import CheckRunner, ComponentResult

EPS = 1e-10


class AudioDecodeError(ValueError):
    pass


def decode_wav(data: bytes) -> tuple[np.ndarray, int]:
    """Return mono float32 samples in [-1, 1] and the sample rate."""
    try:
        with wave.open(io.BytesIO(data), "rb") as wav:
            channels = wav.getnchannels()
            width = wav.getsampwidth()
            rate = wav.getframerate()
            raw = wav.readframes(wav.getnframes())
    except (wave.Error, EOFError) as exc:
        raise AudioDecodeError(f"Not a readable PCM WAV file: {exc}") from exc

    if width == 1:
        samples = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    elif width == 2:
        samples = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    elif width == 4:
        samples = np.frombuffer(raw, dtype="<i4").astype(np.float32) / 2147483648.0
    else:
        raise AudioDecodeError(f"Unsupported sample width: {width * 8} bit")

    if channels > 1:
        usable = len(samples) - len(samples) % channels
        samples = samples[:usable].reshape(-1, channels).mean(axis=1)
    return samples, rate


def to_db(x: float) -> float:
    return float(20.0 * np.log10(max(x, EPS)))


def compute_signals(samples: np.ndarray, rate: int, frame_ms: int, silence_db: float,
                    clip_level: float) -> dict:
    n = len(samples)
    duration_ms = n / rate * 1000.0 if rate else 0.0
    if n == 0:
        return {
            "duration_ms": 0.0, "sample_rate": rate, "rms_dbfs": to_db(0), "peak": 0.0,
            "clipping_ratio": 0.0, "silence_ratio": 1.0, "snr_db": None,
        }

    frame_len = max(1, int(rate * frame_ms / 1000))
    n_frames = n // frame_len
    if n_frames > 0:
        frames = samples[: n_frames * frame_len].reshape(n_frames, frame_len)
        frame_db = np.array([to_db(v) for v in np.sqrt(np.mean(frames ** 2, axis=1))])
        silence_ratio = float(np.mean(frame_db < silence_db))
        # Rough SNR: loud frames (speech) vs quiet frames (noise floor).
        snr_db = float(np.percentile(frame_db, 95) - np.percentile(frame_db, 10)) if n_frames >= 5 else None
    else:
        silence_ratio, snr_db = 1.0, None

    return {
        "duration_ms": round(duration_ms, 1),
        "sample_rate": rate,
        "rms_dbfs": round(to_db(float(np.sqrt(np.mean(samples ** 2)))), 2),
        "peak": round(float(np.max(np.abs(samples))), 4),
        "clipping_ratio": round(float(np.mean(np.abs(samples) >= clip_level)), 5),
        "silence_ratio": round(silence_ratio, 4),
        "snr_db": None if snr_db is None else round(snr_db, 2),
    }


def evaluate_audio(data: bytes | None, rules: dict, *, component: str = "audio",
                   prefix: str | None = None, only_checks: list[str] | None = None) -> ComponentResult:
    """Evaluate an audio clip.

    `component`/`prefix`/`only_checks` let the TTS evaluator re-use this
    function on generated speech (findings then look like `tts.audio.clipping`).
    """
    if not data:
        return ComponentResult.skipped(component, "no audio provided")

    cfg = rules["audio"]
    checks = cfg["checks"]
    if only_checks is not None:
        checks = {k: v for k, v in checks.items() if k in only_checks or k == "unreadable"}
    runner = CheckRunner(component, checks, prefix=prefix)
    result = ComponentResult(component=component, evaluated=True)

    try:
        samples, rate = decode_wav(data)
    except AudioDecodeError as exc:
        runner.add("unreadable", False, None, "PCM WAV", str(exc))
        result.findings = runner.findings
        result.signals = {"decode_error": str(exc)}
        return result

    clip_level = checks.get("clipping", {}).get("clip_level", 0.99)
    s = compute_signals(samples, rate, cfg.get("frame_ms", 20), cfg.get("silence_frame_dbfs", -45), clip_level)
    result.signals = s

    if c := runner.cfg("too_short"):
        t = c["min_duration_ms"]
        runner.add("too_short", s["duration_ms"] >= t, s["duration_ms"], t,
                   f"Audio duration {s['duration_ms']} ms (minimum {t} ms).")
    if c := runner.cfg("too_quiet"):
        t = c["min_rms_dbfs"]
        runner.add("too_quiet", s["rms_dbfs"] >= t, s["rms_dbfs"], t,
                   f"Loudness {s['rms_dbfs']} dBFS (minimum {t} dBFS).")
    if c := runner.cfg("mostly_silent"):
        t = c["max_silence_ratio"]
        runner.add("mostly_silent", s["silence_ratio"] <= t, s["silence_ratio"], t,
                   f"{s['silence_ratio']:.0%} of the audio is silence (maximum {t:.0%}).")
    if c := runner.cfg("clipping"):
        t = c["max_clipping_ratio"]
        runner.add("clipping", s["clipping_ratio"] <= t, s["clipping_ratio"], t,
                   f"{s['clipping_ratio']:.2%} of samples are clipped (maximum {t:.2%}).")
    if (c := runner.cfg("low_snr")) and s["snr_db"] is not None:
        t = c["min_snr_db"]
        runner.add("low_snr", s["snr_db"] >= t, s["snr_db"], t,
                   f"Estimated SNR {s['snr_db']} dB (minimum {t} dB).")

    result.findings = runner.findings
    return result
