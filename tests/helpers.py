from pathlib import Path

from evaluations.services.rules import load_ruleset
from evaluations.services.types import ComponentResult, Finding

RULES_PATH = Path(__file__).resolve().parent.parent / "config" / "rules.yaml"


def rules(language="tr", stt_provider=None, tts_provider=None) -> dict:
    return load_ruleset(str(RULES_PATH)).resolve(language, stt_provider, tts_provider)


def failed_rules(result: ComponentResult) -> set[str]:
    return {f.rule for f in result.failed_findings}


def component(name, failed=False, failed_rules_=(), severity="critical") -> ComponentResult:
    findings = [
        Finding(rule=r, component=name, passed=False, severity=severity, penalty=0.6,
                value=None, threshold=None, message=f"{r} failed")
        for r in failed_rules_
    ]
    return ComponentResult(component=name, evaluated=True, findings=findings, score=0.4 if failed else 1.0,
                           failed=failed)
