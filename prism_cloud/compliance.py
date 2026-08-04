"""Compliance — audit logging, data residency, export.

Maintains a tamper-evident audit log of all AI interactions
for SOC2/HIPAA/GDPR compliance.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from collections import deque
from typing import Callable


@dataclass
class AuditEntry:
    """A single audit log entry."""
    id: str
    timestamp: float
    request_id: str
    team: str
    model: str
    provider: str
    action: str  # "completion", "embedding", "moderation"
    input_hash: str  # SHA-256 of input (not stored raw for privacy)
    output_hash: str
    tokens_in: int
    tokens_out: int
    latency_ms: float
    status: str  # "success", "error", "blocked"
    metadata: dict = field(default_factory=dict)
    # Chain integrity
    prev_hash: str = ""
    entry_hash: str = ""


class ComplianceLogger:
    """Tamper-evident audit logger for compliance.

    Each entry is hash-chained to the previous entry,
    making retroactive tampering detectable.
    """

    def __init__(self, max_buffer: int = 100_000, export_callback: Callable | None = None):
        self._buffer: deque[AuditEntry] = deque(maxlen=max_buffer)
        self._last_hash: str = "genesis"
        self._total_entries: int = 0
        self._export_callback = export_callback

    def log(
        self,
        request_id: str,
        team: str,
        model: str,
        provider: str,
        action: str,
        input_content: str,
        output_content: str,
        tokens_in: int,
        tokens_out: int,
        latency_ms: float,
        status: str = "success",
        metadata: dict | None = None,
    ) -> AuditEntry:
        """Create and store an audit entry."""
        entry_id = hashlib.sha256(f"{request_id}:{time.time()}".encode()).hexdigest()[:16]

        entry = AuditEntry(
            id=entry_id,
            timestamp=time.time(),
            request_id=request_id,
            team=team,
            model=model,
            provider=provider,
            action=action,
            input_hash=hashlib.sha256(input_content.encode()).hexdigest()[:32],
            output_hash=hashlib.sha256(output_content.encode()).hexdigest()[:32],
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            latency_ms=latency_ms,
            status=status,
            metadata=metadata or {},
            prev_hash=self._last_hash,
        )

        # Compute chain hash
        chain_data = f"{entry.id}:{entry.prev_hash}:{entry.input_hash}:{entry.output_hash}"
        entry.entry_hash = hashlib.sha256(chain_data.encode()).hexdigest()[:32]
        self._last_hash = entry.entry_hash

        self._buffer.append(entry)
        self._total_entries += 1

        if self._export_callback:
            self._export_callback(entry)

        return entry

    def verify_chain(self) -> tuple[bool, int]:
        """Verify the integrity of the audit chain.
        Returns (is_valid, entries_checked).
        """
        entries = list(self._buffer)
        if not entries:
            return True, 0

        for i, entry in enumerate(entries):
            # Verify hash
            chain_data = f"{entry.id}:{entry.prev_hash}:{entry.input_hash}:{entry.output_hash}"
            expected_hash = hashlib.sha256(chain_data.encode()).hexdigest()[:32]
            if entry.entry_hash != expected_hash:
                return False, i

            # Verify chain linkage
            if i > 0 and entry.prev_hash != entries[i - 1].entry_hash:
                return False, i

        return True, len(entries)

    def export(self, start_time: float = 0, end_time: float | None = None, team: str | None = None) -> list[dict]:
        """Export audit entries for compliance reporting."""
        end = end_time or time.time()
        entries = []
        for entry in self._buffer:
            if entry.timestamp < start_time:
                continue
            if entry.timestamp > end:
                continue
            if team and entry.team != team:
                continue
            entries.append({
                "id": entry.id,
                "timestamp": entry.timestamp,
                "request_id": entry.request_id,
                "team": entry.team,
                "model": entry.model,
                "provider": entry.provider,
                "action": entry.action,
                "input_hash": entry.input_hash,
                "output_hash": entry.output_hash,
                "tokens_in": entry.tokens_in,
                "tokens_out": entry.tokens_out,
                "latency_ms": entry.latency_ms,
                "status": entry.status,
                "entry_hash": entry.entry_hash,
                "prev_hash": entry.prev_hash,
            })
        return entries

    @property
    def stats(self) -> dict:
        return {
            "total_entries": self._total_entries,
            "buffer_size": len(self._buffer),
            "chain_head": self._last_hash,
        }
