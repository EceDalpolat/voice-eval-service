"""Small data structures shared by the evaluators and the policy engine."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

CRITICAL = "critical"
WARNING = "warning"
SEVERITIES = {CRITICAL, WARNING}

ACCEPT = "accept"
RETRY_TTS = "retry_tts"
RETRY_STT = "retry_stt"
SWITCH_PROVIDER = "switch_provider"
ASK_REPEAT = "ask_repeat"
SAFE_FALLBACK = "safe_fallback"
ACTIONS = [ACCEPT, RETRY_TTS, RETRY_STT, SWITCH_PROVIDER, ASK_REPEAT, SAFE_FALLBACK]

COMPONENTS = ("audio", "stt", "tts")


@dataclass
class Finding:
    """Result of one check. Failed findings are the "reasons" of a decision."""

    rule: str            # e.g. "stt.low_confidence"
    component: str       # audio | stt | tts
    passed: bool
    severity: str        # critical | warning
    penalty: float
    value: Any
    threshold: Any
    message: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ComponentResult:
    component: str
    evaluated: bool
    signals: dict = field(default_factory=dict)
    findings: list[Finding] = field(default_factory=list)
    score: float | None = None
    failed: bool = False

    @property
    def failed_findings(self) -> list[Finding]:
        return [f for f in self.findings if not f.passed]

    def to_dict(self) -> dict:
        return {
            "component": self.component,
            "evaluated": self.evaluated,
            "score": self.score,
            "failed": self.failed,
            "signals": self.signals,
            "findings": [f.to_dict() for f in self.findings],
        }

    @classmethod
    def skipped(cls, component: str, reason: str) -> "ComponentResult":
        return cls(component=component, evaluated=False, signals={"skipped_reason": reason})


class CheckRunner:
    """Helper that turns config + an observed value into a Finding.

    Keeps evaluators short: they compute a value and a pass/fail boolean,
    the runner fills severity/penalty from config and skips disabled checks.
    """

    def __init__(self, component: str, checks_cfg: dict, prefix: str | None = None):
        self.component = component
        self.checks_cfg = checks_cfg
        self.prefix = prefix or component
        self.findings: list[Finding] = []

    def cfg(self, name: str) -> dict | None:
        cfg = self.checks_cfg.get(name)
        if not cfg or not cfg.get("enabled", True):
            return None
        return cfg

    def add(self, name: str, passed: bool, value: Any, threshold: Any, message: str) -> Finding | None:
        cfg = self.cfg(name)
        if cfg is None:
            return None
        finding = Finding(
            rule=f"{self.prefix}.{name}",
            component=self.component,
            passed=bool(passed),
            severity=cfg.get("severity", WARNING),
            penalty=float(cfg.get("penalty", 0.0)),
            value=value,
            threshold=threshold,
            message=message,
        )
        self.findings.append(finding)
        return finding


def score_component(result: ComponentResult, fail_below: float) -> ComponentResult:
    """Score = 1.0 minus penalties of failed checks, clamped to [0, 1]."""
    if not result.evaluated:
        return result
    penalty = sum(f.penalty for f in result.failed_findings)
    result.score = round(max(0.0, min(1.0, 1.0 - penalty)), 4)
    has_critical = any(f.severity == CRITICAL for f in result.failed_findings)
    result.failed = has_critical or result.score < fail_below
    return result
