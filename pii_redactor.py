"""Active PII Redaction & Data Scrubbing Pipeline for Paxton Bank AgentOps.

Scrubs sensitive Personal Identifiable Information (PII) and financial secrets
from all structured logs, OpenTelemetry trace spans, and persistent memory/session
records before storage.

Supports two layers:
  1. Deterministic high-speed regex scrubbing (<0.2ms) for SSNs, Credit Cards,
     Bank Account/IBAN numbers, Email addresses, Phone numbers, and API/Bearer keys.
  2. Optional Google Cloud Data Loss Prevention (Cloud DLP API `google.cloud.dlp_v2`)
     inspection and de-identification when `PAXTON_ENABLE_CLOUD_DLP=1`.
"""
import os
import re
from typing import Any

# Deterministic PII regex patterns (ordered from most specific to general)
_PII_PATTERNS: list[tuple[re.Pattern, str]] = [
    # US Social Security Numbers (XXX-XX-XXXX)
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[REDACTED_SSN]"),
    # Credit / Debit Card Numbers (13-19 digits, spaced or dashed)
    (re.compile(r"\b(?:\d{4}[-\s]?){3}\d{1,7}\b"), "[REDACTED_CARD]"),
    # IBAN / International Bank Account Numbers
    (re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b"), "[REDACTED_IBAN]"),
    # US Bank Account / Routing numbers labeled in text
    (re.compile(r"(?i)\b(account|routing|acct|aba)[\s#:=-]*\d{8,17}\b"), r"\1=[REDACTED_ACCOUNT]"),
    # Email addresses
    (re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"), "[REDACTED_EMAIL]"),
    # North American & International Phone Numbers
    (re.compile(r"(?<!\d)(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}(?!\d)"), "[REDACTED_PHONE]"),
    # Bearer tokens, OAuth tokens, and API keys
    (re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9._~+/=-]{16,}"), r"\1[REDACTED_TOKEN]"),
    (re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"), "[REDACTED_API_KEY]"),
]

_SENSITIVE_FIELD_NAMES = frozenset({
    "password", "passwd", "secret", "token", "access_token", "refresh_token",
    "api_key", "apikey", "authorization", "cookie", "ssn", "tax_id",
    "card_number", "cvv", "account_number", "routing_number",
})


def redact_pii(text: str, *, use_cloud_dlp: bool = False) -> str:
    """Scrub PII from a text string before logging or persisting to memory.

    Args:
        text: Raw input string potentially containing PII or credentials.
        use_cloud_dlp: When True (or when PAXTON_ENABLE_CLOUD_DLP=1), invokes
            Google Cloud DLP API after deterministic regex scrubbing.

    Returns:
        Sanitized string with all detected PII replaced by redaction tokens.
    """
    if not text or not isinstance(text, str):
        return text
    scrubbed = text
    for pattern, replacement in _PII_PATTERNS:
        scrubbed = pattern.sub(replacement, scrubbed)

    if use_cloud_dlp or os.environ.get("PAXTON_ENABLE_CLOUD_DLP") == "1":
        scrubbed = _redact_with_cloud_dlp(scrubbed)
    return scrubbed


def _redact_with_cloud_dlp(text: str) -> str:
    """De-identify sensitive info types via Google Cloud DLP API (`google.cloud.dlp_v2`)."""
    try:
        from google.cloud import dlp_v2  # type: ignore
        project_id = os.environ.get("CP_PROJECT") or os.environ.get("GOOGLE_CLOUD_PROJECT") or "kairos-504105"
        client = dlp_v2.DlpServiceClient()
        parent = f"projects/{project_id}/locations/global"
        info_types = [
            {"name": "EMAIL_ADDRESS"},
            {"name": "PHONE_NUMBER"},
            {"name": "US_SOCIAL_SECURITY_NUMBER"},
            {"name": "CREDIT_CARD_NUMBER"},
            {"name": "IBAN_CODE"},
            {"name": "PERSON_NAME"},
        ]
        inspect_config = {"info_types": info_types, "min_likelihood": dlp_v2.Likelihood.POSSIBLE}
        deidentify_config = {
            "info_type_transformations": {
                "transformations": [
                    {
                        "primitive_transformation": {
                            "replace_with_info_type_config": {}
                        }
                    }
                ]
            }
        }
        response = client.deidentify_content(
            request={
                "parent": parent,
                "inspect_config": inspect_config,
                "deidentify_config": deidentify_config,
                "item": {"value": text},
            },
            timeout=2.5,
        )
        return response.item.value
    except Exception:
        # Fail-safe: return the regex-scrubbed string if Cloud DLP is unreachable
        return text


def redact_data(value: Any) -> Any:
    """Recursively scrub PII from dicts, lists, and primitive values."""
    if isinstance(value, str):
        return redact_pii(value)
    if isinstance(value, dict):
        sanitized = {}
        for k, v in value.items():
            key_lower = str(k).lower()
            if key_lower in _SENSITIVE_FIELD_NAMES:
                sanitized[k] = "[REDACTED_SECRET]"
            else:
                sanitized[k] = redact_data(v)
        return sanitized
    if isinstance(value, list):
        return [redact_data(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_data(item) for item in value)
    return value
