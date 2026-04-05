import re
from dataclasses import dataclass, field

_RISK_ORDER = {"none": 0, "low": 1, "medium": 2, "high": 3}


@dataclass(frozen=True)
class PIIScanResult:
    detected: bool
    risk_level: str
    pii_types: list[str] = field(default_factory=list)
    matches: dict[str, list[str]] = field(default_factory=dict)


class PIIDetector:
    REGEX_PATTERNS: dict[str, re.Pattern[str]] = {
        "email": re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"),
        "phone": re.compile(
            r"\b(?:\+\d{1,2}\s?)?(?:\(?\d{2,3}\)?[\s.\-]?)?\d{3,4}[\s.\-]?\d{4}\b"
        ),
        "ssn": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
        "credit_card": re.compile(r"\b\d{4}[\s\-]?\d{4}[\s\-]?\d{4}[\s\-]?\d{4}\b"),
        "api_key": re.compile(
            r"\b(?:sk-[A-Za-z0-9]{20,}|ghp_[A-Za-z0-9]{36}|AIza[A-Za-z0-9\-_]{35}|xox[baprs]-[A-Za-z0-9-]{20,}|[A-Fa-f0-9]{32,64})\b"
        ),
        "ip_address": re.compile(
            r"\b(?:(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\b"
        ),
        "credential_context": re.compile(
            r"(?i)(?:password|passwd|secret|token|api[_\-]?key)\s*[:=]\s*[^\s\[]+"
        ),
    }

    TYPE_RISK: dict[str, str] = {
        "ssn": "high",
        "credit_card": "high",
        "api_key": "high",
        "email": "medium",
        "phone": "medium",
        "ip_address": "low",
        "credential_context": "low",
    }

    def scan(self, text: str) -> PIIScanResult:
        matches: dict[str, list[str]] = {}
        for pii_type, pattern in self.REGEX_PATTERNS.items():
            found = pattern.findall(text)
            if not found:
                continue
            flattened: list[str] = []
            for item in found:
                flattened.append("".join(item) if isinstance(item, tuple) else item)
            matches[pii_type] = flattened
        pii_types = list(matches.keys())
        risk_level = self._compute_risk(pii_types)
        return PIIScanResult(
            detected=bool(matches),
            risk_level=risk_level,
            pii_types=pii_types,
            matches=matches,
        )

    def redact(self, text: str) -> str:
        redacted = text
        for pii_type, pattern in self.REGEX_PATTERNS.items():
            placeholder = f"[REDACTED-{pii_type.upper().replace('_', '-')}]"
            redacted = pattern.sub(placeholder, redacted)
        return redacted

    def _compute_risk(self, pii_types: list[str]) -> str:
        if not pii_types:
            return "none"
        return max(
            (self.TYPE_RISK.get(pii_type, "low") for pii_type in pii_types),
            key=lambda risk: _RISK_ORDER[risk],
        )


def risk_meets_threshold(risk_level: str, threshold: str) -> bool:
    return _RISK_ORDER.get(risk_level, 0) >= _RISK_ORDER.get(threshold, 0)
