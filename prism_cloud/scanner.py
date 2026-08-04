"""Security scanner — PII detection + prompt injection classification.

Scans requests before forwarding to providers. Blocks or redacts
sensitive content based on policy.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from enum import Enum


class ScanAction(Enum):
    ALLOW = "allow"
    BLOCK = "block"
    REDACT = "redact"
    WARN = "warn"


@dataclass
class ScanResult:
    """Result of scanning a message."""
    action: ScanAction
    findings: list[dict] = field(default_factory=list)
    redacted_content: str | None = None
    scan_time_ms: float = 0.0


@dataclass
class ScannerConfig:
    """Security scanner configuration."""
    pii_enabled: bool = True
    injection_enabled: bool = True
    pii_action: ScanAction = ScanAction.REDACT
    injection_action: ScanAction = ScanAction.BLOCK
    custom_patterns: list[dict] = field(default_factory=list)


# --- PII Patterns ---

PII_PATTERNS = {
    "email": r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b',
    "phone_us": r'\b(\+1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b',
    "ssn": r'\b\d{3}-\d{2}-\d{4}\b',
    "credit_card": r'\b(?:\d{4}[-\s]?){3}\d{4}\b',
    "ip_address": r'\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b',
    "aws_key": r'\b(AKIA|ABIA|ACCA|ASIA)[0-9A-Z]{16}\b',
    "api_key_generic": r'\b(sk-|pk_|rk_)[a-zA-Z0-9]{20,}\b',
}

# --- Injection Patterns ---

INJECTION_PATTERNS = [
    r'ignore\s+(all\s+)?previous\s+instructions',
    r'ignore\s+(all\s+)?above\s+instructions',
    r'disregard\s+(all\s+)?previous',
    r'you\s+are\s+now\s+(a|an)\s+',
    r'new\s+instructions?\s*:',
    r'system\s*:\s*you\s+are',
    r'<\s*system\s*>',
    r'IMPORTANT:\s*ignore',
    r'override\s+instructions',
    r'forget\s+(all\s+)?previous',
    r'do\s+not\s+follow\s+previous',
]


class SecurityScanner:
    """Scans prompts for PII and injection attacks."""

    def __init__(self, config: ScannerConfig | None = None):
        self.config = config or ScannerConfig()
        self._compiled_pii = {k: re.compile(v, re.IGNORECASE) for k, v in PII_PATTERNS.items()}
        self._compiled_injection = [re.compile(p, re.IGNORECASE) for p in INJECTION_PATTERNS]
        self._total_scanned = 0
        self._total_blocked = 0
        self._total_redacted = 0

    def scan(self, content: str) -> ScanResult:
        """Scan content for PII and injection patterns."""
        start = time.perf_counter()
        findings = []
        self._total_scanned += 1

        # PII detection
        if self.config.pii_enabled:
            for pii_type, pattern in self._compiled_pii.items():
                matches = pattern.findall(content)
                if matches:
                    findings.append({
                        "type": "pii",
                        "category": pii_type,
                        "count": len(matches),
                        "action": self.config.pii_action.value,
                    })

        # Injection detection
        if self.config.injection_enabled:
            for pattern in self._compiled_injection:
                if pattern.search(content):
                    findings.append({
                        "type": "injection",
                        "pattern": pattern.pattern[:50],
                        "action": self.config.injection_action.value,
                    })
                    break  # One injection finding is enough

        # Determine action
        action = ScanAction.ALLOW
        redacted = None

        if any(f["type"] == "injection" for f in findings):
            action = self.config.injection_action
            self._total_blocked += 1
        elif any(f["type"] == "pii" for f in findings):
            action = self.config.pii_action
            if action == ScanAction.REDACT:
                redacted = self._redact(content)
                self._total_redacted += 1

        elapsed = (time.perf_counter() - start) * 1000
        return ScanResult(
            action=action,
            findings=findings,
            redacted_content=redacted,
            scan_time_ms=round(elapsed, 2),
        )

    def scan_messages(self, messages: list[dict]) -> ScanResult:
        """Scan all messages in a conversation."""
        combined_findings = []
        worst_action = ScanAction.ALLOW
        total_time = 0.0

        for msg in messages:
            content = msg.get("content", "")
            if not content:
                continue
            result = self.scan(content)
            combined_findings.extend(result.findings)
            total_time += result.scan_time_ms

            if result.action == ScanAction.BLOCK:
                worst_action = ScanAction.BLOCK
            elif result.action == ScanAction.REDACT and worst_action != ScanAction.BLOCK:
                worst_action = ScanAction.REDACT
            elif result.action == ScanAction.WARN and worst_action == ScanAction.ALLOW:
                worst_action = ScanAction.WARN

        return ScanResult(
            action=worst_action,
            findings=combined_findings,
            scan_time_ms=round(total_time, 2),
        )

    def _redact(self, content: str) -> str:
        """Redact PII from content."""
        redacted = content
        for pii_type, pattern in self._compiled_pii.items():
            redacted = pattern.sub(f"[REDACTED:{pii_type.upper()}]", redacted)
        return redacted

    @property
    def stats(self) -> dict:
        return {
            "total_scanned": self._total_scanned,
            "total_blocked": self._total_blocked,
            "total_redacted": self._total_redacted,
            "block_rate": round(self._total_blocked / max(self._total_scanned, 1), 4),
        }
