"""Loading, validating and resolving the YAML rule set.

The rule file is read once and cached. `resolve()` returns the effective
configuration for one request after applying language and provider
overrides, so evaluators never need to know where a threshold came from.
"""
from __future__ import annotations

import copy
import hashlib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

from .types import ACTIONS, COMPONENTS, SEVERITIES


class RuleConfigError(ValueError):
    """Raised when the rule file is malformed. Fails fast at startup/first use."""


SUPPORTED_CONDITIONS = {"prior_failures_gte", "component_failed", "any_failed", "attempt_gte"}


def deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge `override` into a copy of `base`."""
    result = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def normalize_language(language: str | None) -> str:
    """'tr-TR' -> 'tr', None -> ''."""
    return (language or "").split("-")[0].split("_")[0].lower()


@dataclass(frozen=True)
class RuleSet:
    raw: dict
    version: str
    fingerprint: str  # short hash of the file, stored with each evaluation

    def resolve(self, language: str | None, stt_provider: str | None = None,
                tts_provider: str | None = None) -> dict:
        lang = normalize_language(language)
        languages = self.raw.get("languages", {})
        if lang not in languages:
            lang = self.raw.get("default_language", "en")
        lang_cfg = languages.get(lang, {})

        resolved = {k: v for k, v in self.raw.items() if k not in {"languages", "providers"}}
        resolved = deep_merge(resolved, lang_cfg.get("overrides") or {})

        providers = self.raw.get("providers", {})
        if stt_provider and stt_provider in providers.get("stt", {}):
            resolved["stt"] = deep_merge(resolved.get("stt", {}), providers["stt"][stt_provider])
        if tts_provider and tts_provider in providers.get("tts", {}):
            resolved["tts"] = deep_merge(resolved.get("tts", {}), providers["tts"][tts_provider])

        resolved["language"] = lang
        resolved["lexicon"] = copy.deepcopy(lang_cfg.get("lexicon", {}))
        return resolved


def validate(raw: dict) -> None:
    if not isinstance(raw, dict):
        raise RuleConfigError("Rule file must be a mapping.")
    for section in COMPONENTS:
        checks = raw.get(section, {}).get("checks")
        if not isinstance(checks, dict):
            raise RuleConfigError(f"Section '{section}.checks' is missing.")
        for name, cfg in checks.items():
            if cfg.get("severity") not in SEVERITIES:
                raise RuleConfigError(f"{section}.checks.{name}: severity must be one of {sorted(SEVERITIES)}.")
    weights = raw.get("scoring", {}).get("component_weights", {})
    if set(weights) - set(COMPONENTS):
        raise RuleConfigError("scoring.component_weights has unknown components.")

    policy = raw.get("policy", {})
    if policy.get("default_action") not in ACTIONS:
        raise RuleConfigError("policy.default_action must be a valid action.")
    names = set()
    for rule in policy.get("rules", []):
        name = rule.get("name")
        if not name or name in names:
            raise RuleConfigError(f"Policy rule name missing or duplicated: {name!r}.")
        names.add(name)
        if rule.get("action") not in ACTIONS:
            raise RuleConfigError(f"Policy '{name}': unknown action {rule.get('action')!r}.")
        unknown = set(rule.get("when", {})) - SUPPORTED_CONDITIONS
        if unknown:
            raise RuleConfigError(f"Policy '{name}': unsupported conditions {sorted(unknown)}.")
        if not rule.get("when"):
            raise RuleConfigError(f"Policy '{name}': 'when' must not be empty.")


def load_ruleset_from_text(text: str) -> RuleSet:
    raw = yaml.safe_load(text)
    validate(raw)
    fingerprint = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
    return RuleSet(raw=raw, version=str(raw.get("version", "unversioned")), fingerprint=fingerprint)


@lru_cache(maxsize=4)
def _load_cached(path: str, mtime: float) -> RuleSet:
    return load_ruleset_from_text(Path(path).read_text(encoding="utf-8"))


def load_ruleset(path: str | None = None) -> RuleSet:
    """Load the rule file. Editing the file is picked up without a restart
    because the cache key includes the modification time."""
    if path is None:
        from django.conf import settings
        path = settings.VOICE_EVAL_RULES_PATH
    p = Path(path)
    if not p.exists():
        raise RuleConfigError(f"Rule file not found: {p}")
    return _load_cached(str(p), p.stat().st_mtime)
