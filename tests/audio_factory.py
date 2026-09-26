"""Deterministic synthetic audio used by the tests and by
scripts/generate_audio_fixtures.py. Real recordings are not needed: each
clip is built to trigger (or not trigger) specific audio checks."""
from __future__ import annotations

import io
import wave

import numpy as np

RATE = 16000


def _db_to_amp(db: float) -> float:
    return 10 ** (db / 20)


def to_wav_bytes(samples: np.ndarray, rate: int = RATE, channels: int = 1) -> bytes:
    pcm = (np.clip(samples, -1.0, 1.0) * 32767).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()


def speech_like(seconds: float = 2.0, level_db: float = -14, noise_db: float = -65, seed: int = 0) -> np.ndarray:
    """Syllable-like bursts (harmonic tone with an envelope) separated by
    short pauses, over a very low noise floor."""
    rng = np.random.default_rng(seed)
    t = np.arange(int(seconds * RATE)) / RATE
    voice = sum(np.sin(2 * np.pi * f * t) / (i + 1) for i, f in enumerate([180, 360, 540, 900]))
    voice = voice / np.max(np.abs(voice))
    syllable = 0.25  # seconds per on/off cycle
    envelope = (np.sin(np.pi * (t % syllable) / syllable) ** 2) * ((t // syllable) % 3 != 2)
    signal = voice * envelope * _db_to_amp(level_db) * 1.8
    noise = rng.normal(0, _db_to_amp(noise_db), len(t))
    return signal + noise


def clean_speech() -> bytes:
    return to_wav_bytes(speech_like())


def silence(seconds: float = 2.0) -> bytes:
    rng = np.random.default_rng(1)
    return to_wav_bytes(rng.normal(0, _db_to_amp(-75), int(seconds * RATE)))


def too_quiet() -> bytes:
    return to_wav_bytes(speech_like(level_db=-52, noise_db=-80))


def clipped() -> bytes:
    return to_wav_bytes(np.clip(speech_like(level_db=-14) * 12, -1, 1))


def noisy() -> bytes:
    rng = np.random.default_rng(2)
    base = speech_like(level_db=-16, noise_db=-90)
    return to_wav_bytes(base + rng.normal(0, _db_to_amp(-19), len(base)))


def too_short() -> bytes:
    return to_wav_bytes(speech_like(seconds=0.15))


def stereo_clean() -> bytes:
    mono = speech_like()
    return to_wav_bytes(np.repeat(mono, 2), channels=2)


def not_a_wav() -> bytes:
    return b"this is definitely not audio"


FIXTURES = {
    "clean_speech.wav": clean_speech,
    "silence.wav": silence,
    "too_quiet.wav": too_quiet,
    "clipped.wav": clipped,
    "noisy.wav": noisy,
    "too_short.wav": too_short,
    "stereo_clean.wav": stereo_clean,
}
