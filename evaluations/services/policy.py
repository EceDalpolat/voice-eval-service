"""Explainable recovery policy engine.

Policies come from the rule file as an ordered list. The first policy whose
conditions all hold decides the action. The engine returns, besides the
action, WHICH policy matched, WHICH findings triggered it, and a trace of
why every earlier policy did not match. That makes each decision auditable.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

from .types import ComponentResult


@dataclass
class PolicyContext:
    components: dict[str, ComponentResult]
    attempts: dict[str, int]
    prior_failures: int = 0

    def failed_rules(self) -> dict[str, dict]:
        return {
            f.rule: f.to_dict()
            for c in self.components.values()
            for f in c.failed_findings
        }


@dataclass
class Decision:
    action: str
    target: str | None
    policy: str
    explanation: str
    triggered_by: list[dict] = field(default_factory=list)
    trace: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _as_list(value) -> list:
    return value if isinstance(value, list) else [value]


def _join(messages) -> str:
    return "; ".join(m.rstrip(".") for m in messages) + "."


def check_condition(name: str, expected, ctx: PolicyContext) -> tuple[bool, str, list[dict]]:
    """Return (holds, human readable reason, findings that support it)."""
    failed = ctx.failed_rules()

    if name == "prior_failures_gte":
        ok = ctx.prior_failures >= expected
        return ok, f"prior failures in call = {ctx.prior_failures} (needs >= {expected})", []

    if name == "component_failed":
        hits = [c for c in _as_list(expected) if ctx.components.get(c) and ctx.components[c].failed]
        evidence = [f for f in failed.values() if f["component"] in hits]
        return bool(hits), f"failed components {hits or 'none'} among {_as_list(expected)}", evidence

    if name == "any_failed":
        hits = [r for r in _as_list(expected) if r in failed]
        return bool(hits), f"failed findings {hits or 'none'} among {_as_list(expected)}", [failed[r] for r in hits]

    if name == "attempt_gte":
        parts, ok = [], True
        for comp, n in expected.items():
            current = ctx.attempts.get(comp, 1)
            parts.append(f"{comp} attempt = {current} (needs >= {n})")
            ok = ok and current >= n
        return ok, "; ".join(parts), []

    raise ValueError(f"Unsupported condition: {name}")


def decide(ctx: PolicyContext, policy_cfg: dict) -> Decision:
    trace = []
    for rule in policy_cfg.get("rules", []):
        results = [(cond, *check_condition(cond, exp, ctx)) for cond, exp in rule["when"].items()]
        matched = all(ok for _, ok, _, _ in results)
        trace.append({
            "policy": rule["name"],
            "matched": matched,
            "conditions": [{"condition": c, "holds": ok, "detail": d} for c, ok, d, _ in results],
        })
        if matched:
            evidence = {f["rule"]: f for _, _, _, fs in results for f in fs}
            triggered = list(evidence.values())
            reasons = _join(f["message"] for f in triggered) if triggered else ""
            explanation = rule.get("description", rule["name"])
            if reasons:
                explanation += f" Evidence: {reasons}"
            else:
                explanation += " Evidence: " + _join(d for _, _, d, _ in results)
            return Decision(
                action=rule["action"], target=rule.get("target"), policy=rule["name"],
                explanation=explanation, triggered_by=triggered, trace=trace,
            )

    warnings = [f for f in ctx.failed_rules().values()]
    explanation = "All evaluated components passed their critical checks."
    if warnings:
        explanation += " Non-blocking warnings: " + _join(f["message"] for f in warnings)
    return Decision(
        action=policy_cfg.get("default_action", "accept"), target=None, policy="default",
        explanation=explanation, triggered_by=warnings, trace=trace,
    )
