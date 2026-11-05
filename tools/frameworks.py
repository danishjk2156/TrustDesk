"""Map TrustDesk control tags to SOC 2 and ISO/IEC 27001:2022 controls.

An indicative mapping to help auditors find their way, not a certification claim: confirm the
mapping with your auditor before relying on it.

  SOC 2: AICPA Trust Services Criteria (2017, revised points of focus 2022), e.g. CC6.1, A1.2
  ISO:   ISO/IEC 27001:2022 Annex A, e.g. A.8.24 Use of cryptography
"""
from __future__ import annotations

ISO_TITLES = {
    "A.5.15": "Access control", "A.5.16": "Identity management", "A.5.17": "Authentication information",
    "A.5.30": "ICT readiness for business continuity", "A.5.35": "Independent review of information security",
    "A.6.1": "Screening", "A.6.8": "Information security event reporting", "A.8.2": "Privileged access rights",
    "A.8.3": "Information access restriction", "A.8.5": "Secure authentication", "A.8.7": "Protection against malware",
    "A.8.8": "Management of technical vulnerabilities", "A.8.10": "Information deletion", "A.8.13": "Information backup",
    "A.8.14": "Redundancy of information processing facilities", "A.8.15": "Logging", "A.8.16": "Monitoring activities",
    "A.8.20": "Networks security", "A.8.22": "Segregation of networks", "A.8.24": "Use of cryptography",
    "A.8.28": "Secure coding", "A.8.29": "Security testing in development and acceptance", "A.8.32": "Change management",
}

# control tag -> (SOC 2 criteria, ISO 27001:2022 Annex A controls)
MAPPING: dict[str, tuple[list[str], list[str]]] = {
    "db_encryption_at_rest": (["CC6.1"], ["A.8.24"]),
    "kms_key_rotation": (["CC6.1"], ["A.8.24"]),
    "storage_encryption_all_buckets": (["CC6.1"], ["A.8.24"]),
    "tls_in_transit": (["CC6.7"], ["A.8.24"]),
    "https_redirect": (["CC6.7"], ["A.8.24"]),
    "hsts": (["CC6.7"], ["A.8.24"]),
    "db_tls_required": (["CC6.7"], ["A.8.24"]),
    "mfa_admin": (["CC6.1"], ["A.8.5"]),
    "mfa_all_users": (["CC6.1"], ["A.8.5"]),
    "sso": (["CC6.1"], ["A.5.16", "A.8.5"]),
    "password_length": (["CC6.1"], ["A.5.17"]),
    "password_reuse": (["CC6.1"], ["A.5.17"]),
    "session_timeout": (["CC6.1"], ["A.8.5"]),
    "least_privilege": (["CC6.3"], ["A.5.15", "A.8.2"]),
    "code_review_required": (["CC8.1"], ["A.8.32"]),
    "audit_logging": (["CC7.2"], ["A.8.15"]),
    "log_integrity": (["CC7.2"], ["A.8.15"]),
    "log_retention": (["CC7.2"], ["A.8.15"]),
    "network_flow_logs": (["CC7.2"], ["A.8.16"]),
    "db_backups": (["A1.2"], ["A.8.13"]),
    "multi_az": (["A1.2"], ["A.8.14"]),
    "deletion_protection": (["A1.2"], ["A.8.13"]),
    "storage_versioning": (["A1.2"], ["A.8.13"]),
    "storage_public_access_blocked": (["CC6.6"], ["A.8.3"]),
    "waf": (["CC6.6"], ["A.8.20"]),
    "db_not_public": (["CC6.6"], ["A.8.22"]),
    "db_network_restricted": (["CC6.6"], ["A.8.22"]),
    "sast": (["CC8.1"], ["A.8.29"]),
    "dependency_scanning": (["CC7.1"], ["A.8.8"]),
    "secrets_scanning": (["CC6.1"], ["A.8.28"]),
    "container_scanning": (["CC7.1"], ["A.8.8"]),
    "vulnerability_disclosure": (["CC7.1"], ["A.6.8"]),
    "backup_restore_testing": (["A1.3"], ["A.8.13"]),
    "penetration_testing": (["CC4.1"], ["A.8.8"]),
    "dr_testing": (["A1.3"], ["A.5.30"]),
    "endpoint_protection": (["CC6.8"], ["A.8.7"]),
    "data_deletion": (["C1.2"], ["A.8.10"]),
    "background_checks": (["CC1.4"], ["A.6.1"]),
    "soc2_report": (["CC4.1"], ["A.5.35"]),
}


def frameworks(control: str | None) -> dict:
    """{"soc2": [...], "iso27001": [{"id", "title"}]} for a control tag; empty lists if unmapped."""
    soc2, iso = MAPPING.get(control or "", ([], []))
    return {"soc2": list(soc2), "iso27001": [{"id": i, "title": ISO_TITLES[i]} for i in iso]}


def label(control: str | None) -> str:
    """Short text for a spreadsheet cell, e.g. 'SOC 2 CC6.1 · ISO 27001 A.8.24'."""
    f = frameworks(control)
    parts = []
    if f["soc2"]:
        parts.append("SOC 2 " + ", ".join(f["soc2"]))
    if f["iso27001"]:
        parts.append("ISO 27001 " + ", ".join(i["id"] for i in f["iso27001"]))
    return " · ".join(parts)
