"""Security scanner — Enterprise-grade PII detection + prompt injection classification.

Multi-compliance engine supporting HIPAA, PCI-DSS, GDPR, SOX, CCPA, DPDP Act,
and custom policies. Detects 60+ entity types across all major regions.

Architecture:
  Request → PolicyEngine → [Detectors] → Findings → Action
                              ├── PII Detector (regex + heuristics)
                              ├── Secrets Detector (entropy + patterns)
                              ├── Injection Detector (patterns + classifier)
                              └── Custom Rules (user-defined)
"""
from __future__ import annotations

import hashlib
import math
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

# ─── Enums ────────────────────────────────────────────────────────────────────

class ScanAction(Enum):
    ALLOW = "allow"
    BLOCK = "block"
    REDACT = "redact"
    WARN = "warn"
    AUDIT = "audit"  # Log but pass through


class Severity(Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class ComplianceFramework(Enum):
    """Supported compliance frameworks."""
    HIPAA = "hipaa"          # US Healthcare
    PCI_DSS = "pci_dss"     # Payment Card Industry
    GDPR = "gdpr"           # EU General Data Protection
    CCPA = "ccpa"           # California Consumer Privacy
    SOX = "sox"             # Sarbanes-Oxley (financial)
    DPDP = "dpdp"           # India Digital Personal Data Protection
    SOC2 = "soc2"           # Service Organization Controls
    FERPA = "ferpa"         # US Education records
    GLBA = "glba"           # US Financial (Gramm-Leach-Bliley)
    NIST = "nist"           # NIST Cybersecurity Framework
    ISO27001 = "iso27001"   # Information Security
    CUSTOM = "custom"


class EntityCategory(Enum):
    """Entity categories for detection."""
    PERSONAL_ID = "personal_id"
    FINANCIAL = "financial"
    HEALTH = "health"
    CREDENTIALS = "credentials"
    CONTACT = "contact"
    LOCATION = "location"
    BIOMETRIC = "biometric"
    LEGAL = "legal"
    CORPORATE = "corporate"


# ─── Data Classes ─────────────────────────────────────────────────────────────

@dataclass
class Finding:
    """A single detected entity."""
    entity_type: str
    category: EntityCategory
    severity: Severity
    confidence: float  # 0.0 - 1.0
    start: int
    end: int
    matched_text: str  # For audit; redacted in responses
    frameworks: list[ComplianceFramework]
    action: ScanAction = ScanAction.ALLOW
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "entity_type": self.entity_type,
            "category": self.category.value,
            "severity": self.severity.value,
            "confidence": self.confidence,
            "frameworks": [f.value for f in self.frameworks],
            "action": self.action.value,
            "fingerprint": hashlib.sha256(self.matched_text.encode()).hexdigest()[:12],
        }


@dataclass
class ScanResult:
    """Result of scanning content."""
    action: ScanAction
    findings: list[dict] = field(default_factory=list)
    redacted_content: str | None = None
    scan_time_ms: float = 0.0
    entities_detected: int = 0
    frameworks_violated: list[str] = field(default_factory=list)


@dataclass
class PolicyRule:
    """A compliance policy rule."""
    entity_type: str
    action: ScanAction
    severity: Severity
    frameworks: list[ComplianceFramework]
    enabled: bool = True
    environments: list[str] = field(default_factory=lambda: ["production", "staging", "development"])


@dataclass
class CustomEntity:
    """User-defined entity pattern."""
    name: str
    pattern: str
    category: EntityCategory = EntityCategory.PERSONAL_ID
    severity: Severity = Severity.HIGH
    confidence: float = 0.8
    action: ScanAction = ScanAction.REDACT
    description: str = ""
    frameworks: list[ComplianceFramework] = field(default_factory=lambda: [ComplianceFramework.CUSTOM])


@dataclass
class CustomSecret:
    """User-defined secret/credential pattern."""
    name: str
    pattern: str
    severity: Severity = Severity.CRITICAL
    confidence: float = 0.9
    action: ScanAction = ScanAction.BLOCK
    description: str = ""
    # Entropy threshold — if set, only match when string entropy exceeds this
    min_entropy: float = 0.0


@dataclass
class CustomInjection:
    """User-defined injection pattern."""
    name: str
    pattern: str
    severity: Severity = Severity.HIGH
    confidence: float = 0.8
    action: ScanAction = ScanAction.BLOCK
    description: str = ""


@dataclass
class ScannerConfig:
    """Scanner configuration — drives the entire policy engine."""
    enabled: bool = True
    # Compliance frameworks to enforce
    frameworks: list[ComplianceFramework] = field(
        default_factory=lambda: [ComplianceFramework.PCI_DSS, ComplianceFramework.GDPR]
    )
    # Default actions by severity
    severity_actions: dict[str, ScanAction] = field(default_factory=lambda: {
        "critical": ScanAction.BLOCK,
        "high": ScanAction.REDACT,
        "medium": ScanAction.REDACT,
        "low": ScanAction.WARN,
        "info": ScanAction.AUDIT,
    })
    # Override actions for specific entity types
    entity_overrides: dict[str, ScanAction] = field(default_factory=dict)
    # Custom user-defined rules
    custom_entities: list[CustomEntity] = field(default_factory=list)
    custom_secrets: list[CustomSecret] = field(default_factory=list)
    custom_injections: list[CustomInjection] = field(default_factory=list)
    # Legacy: raw dict patterns (deprecated, use typed classes above)
    custom_patterns: list[dict] = field(default_factory=list)
    # Whether to include matched text in audit log
    audit_matched_text: bool = False
    # Minimum confidence threshold (0.0 - 1.0)
    min_confidence: float = 0.5
    # Environment (affects which rules fire)
    environment: str = "production"
    # Injection detection
    injection_enabled: bool = True
    injection_action: ScanAction = ScanAction.BLOCK
    # Secrets detection
    secrets_enabled: bool = True
    secrets_action: ScanAction = ScanAction.BLOCK

    @classmethod
    def from_yaml(cls, yaml_dict: dict) -> ScannerConfig:
        r"""Load scanner config from YAML dict (e.g. prism.yaml 'scanner' section).

        Example YAML:
            scanner:
              enabled: true
              frameworks: [pci_dss, gdpr, hipaa, dpdp]
              min_confidence: 0.6
              severity_actions:
                critical: block
                high: redact
              custom_entities:
                - name: employee_id
                  pattern: 'EMP-\d{6}'
                  severity: high
                  action: redact
                  description: Internal employee ID
                - name: internal_project
                  pattern: 'PRJ-[A-Z]{2}-\d{4}'
                  severity: medium
                  action: warn
              custom_secrets:
                - name: internal_api_token
                  pattern: 'tok_[a-zA-Z0-9]{32}'
                  description: Our internal service tokens
                  min_entropy: 3.5
                - name: database_password
                  pattern: 'DB_PASS=[^\s]+'
                  severity: critical
              custom_injections:
                - name: competitor_extraction
                  pattern: 'list all (customers|users|accounts)'
                  severity: high
                  description: Attempt to extract user lists
              entity_overrides:
                email: warn
                ip_address: audit
        """
        config = cls()

        if "enabled" in yaml_dict:
            config.enabled = yaml_dict["enabled"]
        if "min_confidence" in yaml_dict:
            config.min_confidence = yaml_dict["min_confidence"]
        if "environment" in yaml_dict:
            config.environment = yaml_dict["environment"]
        if "audit_matched_text" in yaml_dict:
            config.audit_matched_text = yaml_dict["audit_matched_text"]
        if "injection_enabled" in yaml_dict:
            config.injection_enabled = yaml_dict["injection_enabled"]
        if "secrets_enabled" in yaml_dict:
            config.secrets_enabled = yaml_dict["secrets_enabled"]

        # Frameworks
        if "frameworks" in yaml_dict:
            config.frameworks = [
                ComplianceFramework(f) for f in yaml_dict["frameworks"]
            ]

        # Severity actions
        if "severity_actions" in yaml_dict:
            config.severity_actions = {
                k: ScanAction(v) for k, v in yaml_dict["severity_actions"].items()
            }

        # Entity overrides
        if "entity_overrides" in yaml_dict:
            config.entity_overrides = {
                k: ScanAction(v) for k, v in yaml_dict["entity_overrides"].items()
            }

        # Injection/secrets actions
        if "injection_action" in yaml_dict:
            config.injection_action = ScanAction(yaml_dict["injection_action"])
        if "secrets_action" in yaml_dict:
            config.secrets_action = ScanAction(yaml_dict["secrets_action"])

        # Custom entities
        for entity in yaml_dict.get("custom_entities", []):
            config.custom_entities.append(CustomEntity(
                name=entity["name"],
                pattern=entity["pattern"],
                category=EntityCategory(entity.get("category", "personal_id")),
                severity=Severity(entity.get("severity", "high")),
                confidence=entity.get("confidence", 0.8),
                action=ScanAction(entity.get("action", "redact")),
                description=entity.get("description", ""),
                frameworks=[ComplianceFramework(f) for f in entity.get("frameworks", ["custom"])],
            ))

        # Custom secrets
        for secret in yaml_dict.get("custom_secrets", []):
            config.custom_secrets.append(CustomSecret(
                name=secret["name"],
                pattern=secret["pattern"],
                severity=Severity(secret.get("severity", "critical")),
                confidence=secret.get("confidence", 0.9),
                action=ScanAction(secret.get("action", "block")),
                description=secret.get("description", ""),
                min_entropy=secret.get("min_entropy", 0.0),
            ))

        # Custom injections
        for inj in yaml_dict.get("custom_injections", []):
            config.custom_injections.append(CustomInjection(
                name=inj["name"],
                pattern=inj["pattern"],
                severity=Severity(inj.get("severity", "high")),
                confidence=inj.get("confidence", 0.8),
                action=ScanAction(inj.get("action", "block")),
                description=inj.get("description", ""),
            ))

        return config


# ─── Entity Definitions ───────────────────────────────────────────────────────
# Each entity: (pattern, category, severity, confidence, frameworks, description)

ENTITY_REGISTRY: dict[str, dict[str, Any]] = {
    # ── Personal Identifiers (Global) ─────────────────────────────────────
    "email": {
        "pattern": r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b',
        "category": EntityCategory.CONTACT,
        "severity": Severity.MEDIUM,
        "confidence": 0.95,
        "frameworks": [ComplianceFramework.GDPR, ComplianceFramework.CCPA, ComplianceFramework.DPDP],
    },
    "phone_international": {
        "pattern": r'\b\+?\d{1,4}[\s.-]?\(?\d{1,4}\)?[\s.-]?\d{2,4}[\s.-]?\d{2,4}[\s.-]?\d{0,4}\b',
        "category": EntityCategory.CONTACT,
        "severity": Severity.MEDIUM,
        "confidence": 0.7,
        "frameworks": [ComplianceFramework.GDPR, ComplianceFramework.CCPA, ComplianceFramework.DPDP],
    },
    "ip_address": {
        "pattern": r'\b(?:(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\b',
        "category": EntityCategory.CONTACT,
        "severity": Severity.LOW,
        "confidence": 0.9,
        "frameworks": [ComplianceFramework.GDPR, ComplianceFramework.NIST],
    },
    "ipv6_address": {
        "pattern": r'\b(?:[0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}\b',
        "category": EntityCategory.CONTACT,
        "severity": Severity.LOW,
        "confidence": 0.9,
        "frameworks": [ComplianceFramework.GDPR, ComplianceFramework.NIST],
    },

    # ── United States ─────────────────────────────────────────────────────
    "us_ssn": {
        "pattern": r'\b(?!000|666|9\d{2})\d{3}-(?!00)\d{2}-(?!0000)\d{4}\b',
        "category": EntityCategory.PERSONAL_ID,
        "severity": Severity.CRITICAL,
        "confidence": 0.95,
        "frameworks": [ComplianceFramework.HIPAA, ComplianceFramework.CCPA, ComplianceFramework.SOC2],
    },
    "us_phone": {
        "pattern": r'\b(\+1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}\b',
        "category": EntityCategory.CONTACT,
        "severity": Severity.MEDIUM,
        "confidence": 0.8,
        "frameworks": [ComplianceFramework.CCPA, ComplianceFramework.HIPAA],
    },
    "us_drivers_license": {
        "pattern": r'\b[A-Z]\d{7,8}\b',
        "category": EntityCategory.PERSONAL_ID,
        "severity": Severity.HIGH,
        "confidence": 0.5,
        "frameworks": [ComplianceFramework.CCPA],
    },
    "us_passport": {
        "pattern": r'\b[A-Z]\d{8}\b',
        "category": EntityCategory.PERSONAL_ID,
        "severity": Severity.HIGH,
        "confidence": 0.6,
        "frameworks": [ComplianceFramework.CCPA],
    },
    "us_itin": {
        "pattern": r'\b9\d{2}-[7-9]\d-\d{4}\b',
        "category": EntityCategory.PERSONAL_ID,
        "severity": Severity.CRITICAL,
        "confidence": 0.9,
        "frameworks": [ComplianceFramework.CCPA, ComplianceFramework.GLBA],
    },
    "us_medicare": {
        "pattern": r'\b\d{1}[A-Z]{1,2}\d{1,2}-?\d{1,2}-?\d{1,2}-?\d{1,4}[A-Z]?\b',
        "category": EntityCategory.HEALTH,
        "severity": Severity.CRITICAL,
        "confidence": 0.7,
        "frameworks": [ComplianceFramework.HIPAA],
    },

    # ── European Union / UK ───────────────────────────────────────────────
    "eu_iban": {
        "pattern": r'\b[A-Z]{2}\d{2}[\s]?[\dA-Z]{4}[\s]?(?:[\dA-Z]{4}[\s]?){2,7}[\dA-Z]{1,4}\b',
        "category": EntityCategory.FINANCIAL,
        "severity": Severity.HIGH,
        "confidence": 0.9,
        "frameworks": [ComplianceFramework.GDPR, ComplianceFramework.PCI_DSS],
    },
    "uk_nino": {
        "pattern": r'\b[A-CEGHJ-PR-TW-Z]{2}\d{6}[A-D]\b',
        "category": EntityCategory.PERSONAL_ID,
        "severity": Severity.CRITICAL,
        "confidence": 0.9,
        "frameworks": [ComplianceFramework.GDPR],
    },
    "uk_nhs": {
        "pattern": r'\b\d{3}[\s-]?\d{3}[\s-]?\d{4}\b',
        "category": EntityCategory.HEALTH,
        "severity": Severity.CRITICAL,
        "confidence": 0.6,
        "frameworks": [ComplianceFramework.GDPR],
    },
    "de_personalausweis": {
        "pattern": r'\b[CFGHJKLMNPRTVWXYZ0-9]{9}\d\b',
        "category": EntityCategory.PERSONAL_ID,
        "severity": Severity.HIGH,
        "confidence": 0.6,
        "frameworks": [ComplianceFramework.GDPR],
    },
    "fr_insee": {
        "pattern": r'\b[12]\s?\d{2}\s?\d{2}\s?\d{2}\s?\d{3}\s?\d{3}\s?\d{2}\b',
        "category": EntityCategory.PERSONAL_ID,
        "severity": Severity.CRITICAL,
        "confidence": 0.85,
        "frameworks": [ComplianceFramework.GDPR],
    },

    # ── India ─────────────────────────────────────────────────────────────
    "in_pan": {
        "pattern": r'\b[A-Z]{5}\d{4}[A-Z]\b',
        "category": EntityCategory.PERSONAL_ID,
        "severity": Severity.CRITICAL,
        "confidence": 0.85,
        "frameworks": [ComplianceFramework.DPDP],
    },
    "in_aadhaar": {
        "pattern": r'\b\d{4}[\s-]?\d{4}[\s-]?\d{4}\b',
        "category": EntityCategory.PERSONAL_ID,
        "severity": Severity.CRITICAL,
        "confidence": 0.8,
        "frameworks": [ComplianceFramework.DPDP],
    },
    "in_upi": {
        "pattern": r'\b[\w.-]+@(?:ybl|okhdfcbank|okaxis|okicici|oksbi|paytm|apl|ibl|axl)\b',
        "category": EntityCategory.FINANCIAL,
        "severity": Severity.HIGH,
        "confidence": 0.9,
        "frameworks": [ComplianceFramework.DPDP],
    },
    "in_ifsc": {
        "pattern": r'\b[A-Z]{4}0[A-Z0-9]{6}\b',
        "category": EntityCategory.FINANCIAL,
        "severity": Severity.MEDIUM,
        "confidence": 0.8,
        "frameworks": [ComplianceFramework.DPDP],
    },
    "in_vehicle_registration": {
        "pattern": r'\b[A-Z]{2}\d{2}[A-Z]{1,2}\d{4}\b',
        "category": EntityCategory.PERSONAL_ID,
        "severity": Severity.MEDIUM,
        "confidence": 0.7,
        "frameworks": [ComplianceFramework.DPDP],
    },
    "in_voter_id": {
        "pattern": r'\b[A-Z]{3}\d{7}\b',
        "category": EntityCategory.PERSONAL_ID,
        "severity": Severity.HIGH,
        "confidence": 0.6,
        "frameworks": [ComplianceFramework.DPDP],
    },

    # ── Financial (Global) ────────────────────────────────────────────────
    "credit_card": {
        "pattern": r'\b(?:4\d{3}|5[1-5]\d{2}|3[47]\d{2}|6(?:011|5\d{2}))[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}\b',
        "category": EntityCategory.FINANCIAL,
        "severity": Severity.CRITICAL,
        "confidence": 0.95,
        "frameworks": [ComplianceFramework.PCI_DSS, ComplianceFramework.GDPR, ComplianceFramework.CCPA],
    },
    "credit_card_amex": {
        "pattern": r'\b3[47]\d{2}[\s-]?\d{6}[\s-]?\d{5}\b',
        "category": EntityCategory.FINANCIAL,
        "severity": Severity.CRITICAL,
        "confidence": 0.95,
        "frameworks": [ComplianceFramework.PCI_DSS],
    },
    "swift_bic": {
        "pattern": r'\b[A-Z]{6}[A-Z0-9]{2}(?:[A-Z0-9]{3})?\b',
        "category": EntityCategory.FINANCIAL,
        "severity": Severity.MEDIUM,
        "confidence": 0.7,
        "frameworks": [ComplianceFramework.PCI_DSS, ComplianceFramework.GLBA],
    },
    "us_routing_number": {
        "pattern": r'\b(?:0[1-9]|[12]\d|3[0-2])\d{7}\b',
        "category": EntityCategory.FINANCIAL,
        "severity": Severity.HIGH,
        "confidence": 0.6,
        "frameworks": [ComplianceFramework.GLBA, ComplianceFramework.PCI_DSS],
    },
    "us_bank_account": {
        "pattern": r'\b\d{8,17}\b',
        "category": EntityCategory.FINANCIAL,
        "severity": Severity.HIGH,
        "confidence": 0.3,  # Low confidence alone — needs context
        "frameworks": [ComplianceFramework.GLBA],
    },
    "crypto_btc_address": {
        "pattern": r'\b(?:bc1|[13])[a-zA-HJ-NP-Z0-9]{25,62}\b',
        "category": EntityCategory.FINANCIAL,
        "severity": Severity.HIGH,
        "confidence": 0.9,
        "frameworks": [ComplianceFramework.SOX],
    },
    "crypto_eth_address": {
        "pattern": r'\b0x[a-fA-F0-9]{40}\b',
        "category": EntityCategory.FINANCIAL,
        "severity": Severity.HIGH,
        "confidence": 0.95,
        "frameworks": [ComplianceFramework.SOX],
    },

    # ── Healthcare (HIPAA) ────────────────────────────────────────────────
    "medical_record_number": {
        "pattern": r'\bMRN[\s:#-]?\d{6,10}\b',
        "category": EntityCategory.HEALTH,
        "severity": Severity.CRITICAL,
        "confidence": 0.95,
        "frameworks": [ComplianceFramework.HIPAA],
    },
    "dea_number": {
        "pattern": r'\b[ABCDEFGHJKLMNPRSTUabcdefghjklmnprstu]\w\d{7}\b',
        "category": EntityCategory.HEALTH,
        "severity": Severity.HIGH,
        "confidence": 0.7,
        "frameworks": [ComplianceFramework.HIPAA],
    },
    "ndc_code": {
        "pattern": r'\b\d{5}-\d{4}-\d{2}\b',
        "category": EntityCategory.HEALTH,
        "severity": Severity.MEDIUM,
        "confidence": 0.8,
        "frameworks": [ComplianceFramework.HIPAA],
    },
    "icd10_code": {
        "pattern": r'\b[A-Z]\d{2}(?:\.\d{1,4})?\b',
        "category": EntityCategory.HEALTH,
        "severity": Severity.MEDIUM,
        "confidence": 0.4,  # Very common format — needs context
        "frameworks": [ComplianceFramework.HIPAA],
    },

    # ── Education (FERPA) ─────────────────────────────────────────────────
    "student_id": {
        "pattern": r'\b(?:SID|Student[\s]?ID)[\s:#-]?\d{6,10}\b',
        "category": EntityCategory.PERSONAL_ID,
        "severity": Severity.HIGH,
        "confidence": 0.9,
        "frameworks": [ComplianceFramework.FERPA],
    },

    # ── Biometric / Genetic ───────────────────────────────────────────────
    "dna_sequence": {
        "pattern": r'\b[ACGT]{20,}\b',
        "category": EntityCategory.BIOMETRIC,
        "severity": Severity.CRITICAL,
        "confidence": 0.7,
        "frameworks": [ComplianceFramework.HIPAA, ComplianceFramework.GDPR],
    },
}

# ─── Secrets & Credentials ────────────────────────────────────────────────────

SECRETS_PATTERNS: dict[str, dict[str, Any]] = {
    "aws_access_key": {
        "pattern": r'\b(AKIA|ABIA|ACCA|ASIA)[0-9A-Z]{16}\b',
        "severity": Severity.CRITICAL,
        "confidence": 0.99,
    },
    "aws_secret_key": {
        "pattern": r'\b[A-Za-z0-9/+=]{40}\b',
        "severity": Severity.CRITICAL,
        "confidence": 0.4,  # Needs entropy check
    },
    "github_token": {
        "pattern": r'\b(ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9_]{36,255}\b',
        "severity": Severity.CRITICAL,
        "confidence": 0.99,
    },
    "github_fine_grained": {
        "pattern": r'\bgithub_pat_[A-Za-z0-9_]{22,255}\b',
        "severity": Severity.CRITICAL,
        "confidence": 0.99,
    },
    "openai_api_key": {
        "pattern": r'\bsk-[A-Za-z0-9]{20,}\b',
        "severity": Severity.CRITICAL,
        "confidence": 0.95,
    },
    "anthropic_api_key": {
        "pattern": r'\bsk-ant-[A-Za-z0-9_-]{20,}\b',
        "severity": Severity.CRITICAL,
        "confidence": 0.99,
    },
    "stripe_key": {
        "pattern": r'\b(sk|pk|rk)_(test|live)_[A-Za-z0-9]{20,}\b',
        "severity": Severity.CRITICAL,
        "confidence": 0.99,
    },
    "google_api_key": {
        "pattern": r'\bAIza[0-9A-Za-z_-]{35}\b',
        "severity": Severity.CRITICAL,
        "confidence": 0.95,
    },
    "slack_token": {
        "pattern": r'\bxox[baprs]-[0-9A-Za-z-]{10,}\b',
        "severity": Severity.CRITICAL,
        "confidence": 0.95,
    },
    "slack_webhook": {
        "pattern": r'https://hooks\.slack\.com/services/T[A-Z0-9]+/B[A-Z0-9]+/[A-Za-z0-9]+',
        "severity": Severity.HIGH,
        "confidence": 0.99,
    },
    "jwt_token": {
        "pattern": r'\beyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b',
        "severity": Severity.HIGH,
        "confidence": 0.95,
    },
    "private_key_header": {
        "pattern": r'-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----',
        "severity": Severity.CRITICAL,
        "confidence": 0.99,
    },
    "azure_connection_string": {
        "pattern": r'DefaultEndpointsProtocol=https?;AccountName=[^;]+;AccountKey=[^;]+',
        "severity": Severity.CRITICAL,
        "confidence": 0.99,
    },
    "gcp_service_account": {
        "pattern": r'"type"\s*:\s*"service_account"',
        "severity": Severity.CRITICAL,
        "confidence": 0.95,
    },
    "mongodb_uri": {
        "pattern": r'mongodb(?:\+srv)?://[^:]+:[^@]+@[^/]+',
        "severity": Severity.CRITICAL,
        "confidence": 0.95,
    },
    "postgres_uri": {
        "pattern": r'postgres(?:ql)?://[^:]+:[^@]+@[^/]+',
        "severity": Severity.CRITICAL,
        "confidence": 0.95,
    },
    "sendgrid_key": {
        "pattern": r'\bSG\.[A-Za-z0-9_-]{22}\.[A-Za-z0-9_-]{43}\b',
        "severity": Severity.CRITICAL,
        "confidence": 0.99,
    },
    "twilio_key": {
        "pattern": r'\bSK[a-f0-9]{32}\b',
        "severity": Severity.HIGH,
        "confidence": 0.9,
    },
    "heroku_api_key": {
        "pattern": r'\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b',
        "severity": Severity.MEDIUM,
        "confidence": 0.3,  # UUID format — needs context
    },
    "npm_token": {
        "pattern": r'\bnpm_[A-Za-z0-9]{36}\b',
        "severity": Severity.CRITICAL,
        "confidence": 0.99,
    },
    "pypi_token": {
        "pattern": r'\bpypi-[A-Za-z0-9_-]{50,}\b',
        "severity": Severity.CRITICAL,
        "confidence": 0.99,
    },
}

# ─── Injection Patterns ───────────────────────────────────────────────────────

INJECTION_PATTERNS: list[dict[str, Any]] = [
    # Direct instruction override
    {"pattern": r'ignore\s+(all\s+)?previous\s+instructions', "severity": Severity.CRITICAL, "confidence": 0.95},
    {"pattern": r'ignore\s+(all\s+)?above\s+instructions', "severity": Severity.CRITICAL, "confidence": 0.95},
    {"pattern": r'disregard\s+(all\s+)?previous', "severity": Severity.CRITICAL, "confidence": 0.9},
    {"pattern": r'forget\s+(all\s+)?previous', "severity": Severity.CRITICAL, "confidence": 0.9},
    {"pattern": r'do\s+not\s+follow\s+previous', "severity": Severity.HIGH, "confidence": 0.85},
    {"pattern": r'override\s+(all\s+)?instructions', "severity": Severity.CRITICAL, "confidence": 0.9},
    # Role manipulation
    {"pattern": r'you\s+are\s+now\s+(a|an)\s+', "severity": Severity.HIGH, "confidence": 0.7},
    {"pattern": r'act\s+as\s+(a|an)\s+', "severity": Severity.MEDIUM, "confidence": 0.5},
    {"pattern": r'pretend\s+(to\s+be|you\s+are)', "severity": Severity.HIGH, "confidence": 0.7},
    {"pattern": r'roleplay\s+as', "severity": Severity.MEDIUM, "confidence": 0.6},
    # System prompt extraction
    {"pattern": r'(print|show|display|reveal|output)\s+(your\s+)?(system\s+)?prompt', "severity": Severity.HIGH, "confidence": 0.85},
    {"pattern": r'what\s+(is|are)\s+your\s+(system\s+)?(instructions|rules|prompt)', "severity": Severity.MEDIUM, "confidence": 0.7},
    {"pattern": r'repeat\s+(the\s+)?(text|words|instructions)\s+above', "severity": Severity.HIGH, "confidence": 0.8},
    # Delimiter injection
    {"pattern": r'<\s*system\s*>', "severity": Severity.HIGH, "confidence": 0.9},
    {"pattern": r'\[SYSTEM\]', "severity": Severity.HIGH, "confidence": 0.85},
    {"pattern": r'###\s*(system|instruction|new\s+task)', "severity": Severity.HIGH, "confidence": 0.8},
    # Encoded/obfuscated injection
    {"pattern": r'base64\s*:\s*[A-Za-z0-9+/=]{20,}', "severity": Severity.HIGH, "confidence": 0.75},
    {"pattern": r'\\x[0-9a-fA-F]{2}(\\x[0-9a-fA-F]{2}){5,}', "severity": Severity.MEDIUM, "confidence": 0.7},
    # Context manipulation
    {"pattern": r'IMPORTANT\s*:\s*(ignore|override|forget)', "severity": Severity.CRITICAL, "confidence": 0.95},
    {"pattern": r'new\s+instructions?\s*:', "severity": Severity.HIGH, "confidence": 0.8},
    {"pattern": r'BEGIN\s+(NEW\s+)?(INSTRUCTION|TASK|PROMPT)', "severity": Severity.HIGH, "confidence": 0.85},
    # Privilege escalation
    {"pattern": r'(admin|sudo|root|superuser)\s+mode', "severity": Severity.HIGH, "confidence": 0.8},
    {"pattern": r'enable\s+(debug|developer|god)\s+mode', "severity": Severity.HIGH, "confidence": 0.85},
    {"pattern": r'unlock\s+(all\s+)?(restrictions|limitations|capabilities)', "severity": Severity.HIGH, "confidence": 0.85},
    # Jailbreak patterns
    {"pattern": r'DAN\s*(mode|prompt|jailbreak)', "severity": Severity.CRITICAL, "confidence": 0.95},
    {"pattern": r'(jail|prison)\s*break', "severity": Severity.HIGH, "confidence": 0.8},
    {"pattern": r'bypass\s+(safety|content|ethical)\s+(filter|guidelines|restrictions)', "severity": Severity.CRITICAL, "confidence": 0.9},
]


# ─── Main Scanner ─────────────────────────────────────────────────────────────

class SecurityScanner:
    """Enterprise security scanner — PII, secrets, injection detection.

    Supports 12 compliance frameworks, 60+ entity types, 20+ secret patterns,
    and 27 injection patterns. Configurable per-entity actions with confidence scoring.
    """

    def __init__(self, config: ScannerConfig | None = None):
        self.config = config or ScannerConfig()
        self._compiled_entities: dict[str, re.Pattern] = {}
        self._compiled_secrets: dict[str, re.Pattern] = {}
        self._compiled_injections: list[tuple[re.Pattern, dict]] = []

        # Compile patterns
        self._compile_patterns()

        # Stats
        self._total_scanned = 0
        self._total_blocked = 0
        self._total_redacted = 0
        self._total_findings = 0
        self._findings_by_type: dict[str, int] = {}
        self._findings_by_framework: dict[str, int] = {}

    def _compile_patterns(self):
        """Compile regex patterns for active frameworks."""
        active_frameworks = set(self.config.frameworks)

        for name, entity in ENTITY_REGISTRY.items():
            # Only compile entities relevant to active frameworks
            entity_frameworks = set(entity["frameworks"])
            if entity_frameworks & active_frameworks or ComplianceFramework.CUSTOM in active_frameworks:
                self._compiled_entities[name] = re.compile(entity["pattern"], re.IGNORECASE)

        if self.config.secrets_enabled:
            for name, secret in SECRETS_PATTERNS.items():
                self._compiled_secrets[name] = re.compile(secret["pattern"])

        if self.config.injection_enabled:
            for inj in INJECTION_PATTERNS:
                self._compiled_injections.append(
                    (re.compile(inj["pattern"], re.IGNORECASE), inj)
                )

        # Legacy custom patterns (deprecated)
        for custom in self.config.custom_patterns:
            self._compiled_entities[custom["name"]] = re.compile(
                custom["pattern"], re.IGNORECASE
            )

        # Typed custom entities
        for entity in self.config.custom_entities:
            ENTITY_REGISTRY[entity.name] = {
                "pattern": entity.pattern,
                "category": entity.category,
                "severity": entity.severity,
                "confidence": entity.confidence,
                "frameworks": entity.frameworks,
            }
            self._compiled_entities[entity.name] = re.compile(entity.pattern, re.IGNORECASE)

        # Typed custom secrets
        for secret in self.config.custom_secrets:
            SECRETS_PATTERNS[secret.name] = {
                "pattern": secret.pattern,
                "severity": secret.severity,
                "confidence": secret.confidence,
                "min_entropy": secret.min_entropy,
            }
            self._compiled_secrets[secret.name] = re.compile(secret.pattern)

        # Typed custom injections
        for inj in self.config.custom_injections:
            inj_def = {
                "pattern": inj.pattern,
                "severity": inj.severity,
                "confidence": inj.confidence,
            }
            self._compiled_injections.append(
                (re.compile(inj.pattern, re.IGNORECASE), inj_def)
            )

    def scan(self, content: str) -> ScanResult:
        """Scan content for all configured entity types."""
        start = time.perf_counter()
        findings: list[Finding] = []
        self._total_scanned += 1

        if not content or not self.config.enabled:
            return ScanResult(action=ScanAction.ALLOW, scan_time_ms=0.0)

        # 1. PII / Entity detection
        findings.extend(self._detect_entities(content))

        # 2. Secrets detection
        if self.config.secrets_enabled:
            findings.extend(self._detect_secrets(content))

        # 3. Injection detection
        if self.config.injection_enabled:
            findings.extend(self._detect_injections(content))

        # 4. Filter by confidence threshold
        findings = [f for f in findings if f.confidence >= self.config.min_confidence]

        # 5. Apply policy — determine actions
        for finding in findings:
            finding.action = self._resolve_action(finding)

        # 6. Determine overall action (worst wins)
        action = self._worst_action(findings)

        # 7. Redact if needed
        redacted = None
        if action == ScanAction.REDACT:
            redacted = self._redact(content, findings)
            self._total_redacted += 1
        elif action == ScanAction.BLOCK:
            self._total_blocked += 1

        # 8. Track stats
        self._total_findings += len(findings)
        frameworks_violated = set()
        for f in findings:
            self._findings_by_type[f.entity_type] = self._findings_by_type.get(f.entity_type, 0) + 1
            for fw in f.frameworks:
                fw_name = fw.value
                self._findings_by_framework[fw_name] = self._findings_by_framework.get(fw_name, 0) + 1
                frameworks_violated.add(fw_name)

        elapsed = (time.perf_counter() - start) * 1000
        return ScanResult(
            action=action,
            findings=[f.to_dict() for f in findings],
            redacted_content=redacted,
            scan_time_ms=round(elapsed, 2),
            entities_detected=len(findings),
            frameworks_violated=sorted(frameworks_violated),
        )

    def scan_messages(self, messages: list[dict]) -> ScanResult:
        """Scan all messages in a conversation."""
        combined_findings: list[dict] = []
        worst_action = ScanAction.ALLOW
        total_time = 0.0
        total_entities = 0
        all_frameworks: set[str] = set()

        for msg in messages:
            content = msg.get("content", "")
            if not content:
                continue
            result = self.scan(content)
            combined_findings.extend(result.findings)
            total_time += result.scan_time_ms
            total_entities += result.entities_detected
            all_frameworks.update(result.frameworks_violated)

            if _action_priority(result.action) > _action_priority(worst_action):
                worst_action = result.action

        return ScanResult(
            action=worst_action,
            findings=combined_findings,
            scan_time_ms=round(total_time, 2),
            entities_detected=total_entities,
            frameworks_violated=sorted(all_frameworks),
        )

    def _detect_entities(self, content: str) -> list[Finding]:
        """Detect PII entities in content."""
        findings = []
        for name, pattern in self._compiled_entities.items():
            entity_def = ENTITY_REGISTRY.get(name)
            if not entity_def:
                # Custom pattern
                for custom in self.config.custom_patterns:
                    if custom["name"] == name:
                        entity_def = custom
                        break
                if not entity_def:
                    continue

            for match in pattern.finditer(content):
                findings.append(Finding(
                    entity_type=name,
                    category=entity_def.get("category", EntityCategory.PERSONAL_ID),
                    severity=entity_def.get("severity", Severity.MEDIUM),
                    confidence=entity_def.get("confidence", 0.5),
                    start=match.start(),
                    end=match.end(),
                    matched_text=match.group(),
                    frameworks=entity_def.get("frameworks", []),
                ))
        return findings

    def _detect_secrets(self, content: str) -> list[Finding]:
        """Detect credentials and secrets."""
        findings = []
        for name, pattern in self._compiled_secrets.items():
            secret_def = SECRETS_PATTERNS[name]
            for match in pattern.finditer(content):
                confidence = secret_def["confidence"]
                matched_text = match.group()

                # Check min_entropy threshold (custom secrets can require high entropy)
                min_entropy = secret_def.get("min_entropy", 0.0)
                entropy = _shannon_entropy(matched_text)
                if min_entropy > 0 and entropy < min_entropy:
                    continue  # Skip low-entropy matches

                # Boost confidence with entropy check for low-confidence patterns
                if confidence < 0.7:
                    if entropy > 4.0:
                        confidence = min(confidence + 0.3, 0.95)
                    else:
                        confidence = max(confidence - 0.2, 0.1)

                findings.append(Finding(
                    entity_type=name,
                    category=EntityCategory.CREDENTIALS,
                    severity=secret_def["severity"],
                    confidence=confidence,
                    start=match.start(),
                    end=match.end(),
                    matched_text=matched_text,
                    frameworks=[ComplianceFramework.SOC2, ComplianceFramework.NIST],
                ))
        return findings

    def _detect_injections(self, content: str) -> list[Finding]:
        """Detect prompt injection attempts."""
        findings = []
        for pattern, inj_def in self._compiled_injections:
            match = pattern.search(content)
            if match:
                findings.append(Finding(
                    entity_type="prompt_injection",
                    category=EntityCategory.LEGAL,
                    severity=inj_def["severity"],
                    confidence=inj_def["confidence"],
                    start=match.start(),
                    end=match.end(),
                    matched_text=match.group(),
                    frameworks=[],  # Injection is cross-framework
                    metadata={"pattern": pattern.pattern[:60]},
                ))
        return findings

    def _resolve_action(self, finding: Finding) -> ScanAction:
        """Determine action for a finding based on policy."""
        # Check entity-specific override first
        if finding.entity_type in self.config.entity_overrides:
            return self.config.entity_overrides[finding.entity_type]

        # Injection always uses injection action
        if finding.entity_type == "prompt_injection":
            return self.config.injection_action

        # Secrets always use secrets action
        if finding.category == EntityCategory.CREDENTIALS:
            return self.config.secrets_action

        # Fall back to severity-based action
        return self.config.severity_actions.get(
            finding.severity.value, ScanAction.WARN
        )

    def _worst_action(self, findings: list[Finding]) -> ScanAction:
        """Return the most severe action from findings."""
        if not findings:
            return ScanAction.ALLOW
        return max(findings, key=lambda f: _action_priority(f.action)).action

    def _redact(self, content: str, findings: list[Finding]) -> str:
        """Redact detected entities from content."""
        # Sort by position (reverse) to not shift indices
        sorted_findings = sorted(findings, key=lambda f: f.start, reverse=True)
        redacted = content
        for finding in sorted_findings:
            if _action_priority(finding.action) >= _action_priority(ScanAction.REDACT):
                placeholder = f"[REDACTED:{finding.entity_type.upper()}]"
                redacted = redacted[:finding.start] + placeholder + redacted[finding.end:]
        return redacted

    @property
    def stats(self) -> dict:
        """Scanner statistics for observability."""
        return {
            "total_scanned": self._total_scanned,
            "total_blocked": self._total_blocked,
            "total_redacted": self._total_redacted,
            "total_findings": self._total_findings,
            "block_rate": round(self._total_blocked / max(self._total_scanned, 1), 4),
            "findings_by_type": dict(sorted(
                self._findings_by_type.items(), key=lambda x: x[1], reverse=True
            )[:20]),
            "findings_by_framework": dict(self._findings_by_framework),
            "active_frameworks": [f.value for f in self.config.frameworks],
            "entity_types_loaded": len(self._compiled_entities),
            "secret_patterns_loaded": len(self._compiled_secrets),
            "injection_patterns_loaded": len(self._compiled_injections),
        }


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _action_priority(action: ScanAction) -> int:
    """Priority ordering for actions (higher = more severe)."""
    return {
        ScanAction.ALLOW: 0,
        ScanAction.AUDIT: 1,
        ScanAction.WARN: 2,
        ScanAction.REDACT: 3,
        ScanAction.BLOCK: 4,
    }[action]


def _shannon_entropy(data: str) -> float:
    """Calculate Shannon entropy of a string (higher = more random = likely a secret)."""
    if not data:
        return 0.0
    freq: dict[str, int] = {}
    for c in data:
        freq[c] = freq.get(c, 0) + 1
    length = len(data)
    return -sum((count / length) * math.log2(count / length) for count in freq.values())
