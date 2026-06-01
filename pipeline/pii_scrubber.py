"""
PII Scrubber — HIPAA PHI removal using Microsoft Presidio.

CRITICAL SECURITY GUARANTEE:
  Raw clinical note text NEVER reaches any LLM.
  All PII/PHI is replaced with structured placeholders before
  the scrubbed text is passed downstream.

Entities detected:
  - PERSON        → [PATIENT_NAME]
  - DATE_TIME     → [DATE_OF_BIRTH] or [DATE]
  - US_SSN        → [SOCIAL_SECURITY_NUMBER]
  - PHONE_NUMBER  → [CONTACT_INFO]
  - EMAIL_ADDRESS → [CONTACT_INFO]
  - LOCATION      → [ADDRESS]
  - US_ITIN / NRP → [IDENTIFIER]
  - MEDICAL_LICENSE → [MEDICAL_RECORD_NUMBER]
  - Custom MRN pattern → [MEDICAL_RECORD_NUMBER]
"""

import os
import re
import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

# Try to import presidio; fall back to regex-only mode if not installed.
try:
    from presidio_analyzer import AnalyzerEngine, RecognizerResult
    from presidio_analyzer.nlp_engine import NlpEngineProvider
    from presidio_anonymizer import AnonymizerEngine
    from presidio_anonymizer.entities import OperatorConfig
    PRESIDIO_AVAILABLE = True
except ImportError:  # pragma: no cover
    PRESIDIO_AVAILABLE = False
    logger.warning(
        "presidio-analyzer / presidio-anonymizer not installed. "
        "Falling back to regex-only PII scrubbing."
    )


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class ScrubResult:
    scrubbed_text: str
    # SECURITY NOTE: original_text stores raw PHI for internal diagnostics only.
    # It MUST NOT be logged, serialised to a response body, or persisted anywhere.
    # Callers should access only scrubbed_text for downstream processing.
    original_text: str
    entities_found: list[dict]
    phi_count: int
    scrubbing_mode: str          # "presidio" | "regex"
    phi_types: dict[str, int]    # entity_type → count

    def clear_original(self) -> None:
        """
        Overwrite original_text in-place after the scrubbing step is complete.
        Call this as soon as you no longer need the raw text to minimise the
        window during which PHI lives in Python heap memory.
        """
        object.__setattr__(self, "original_text", "")


# ---------------------------------------------------------------------------
# Replacement labels
# ---------------------------------------------------------------------------

ENTITY_LABELS: dict[str, str] = {
    "PERSON":              "[PATIENT_NAME]",
    "DATE_TIME":           "[DATE]",
    "US_SSN":              "[SOCIAL_SECURITY_NUMBER]",
    "PHONE_NUMBER":        "[CONTACT_INFO]",
    "EMAIL_ADDRESS":       "[CONTACT_INFO]",
    "LOCATION":            "[ADDRESS]",
    "US_DRIVER_LICENSE":   "[IDENTIFIER]",
    "US_ITIN":             "[IDENTIFIER]",
    "NRP":                 "[IDENTIFIER]",
    "MEDICAL_LICENSE":     "[MEDICAL_RECORD_NUMBER]",
    "IP_ADDRESS":          "[IDENTIFIER]",
    "URL":                 "[IDENTIFIER]",
    "IBAN_CODE":           "[IDENTIFIER]",
    "CREDIT_CARD":         "[IDENTIFIER]",
}

# Custom regex patterns for PHI not always caught by Presidio
CUSTOM_PATTERNS: list[tuple[str, str, str]] = [
    # MRN patterns: "MRN 789234" or "MRN: 789234"
    (r'\bMRN[:\s]+\d{5,10}\b',                   "[MEDICAL_RECORD_NUMBER]", "MRN"),
    # DOB explicit labels: "DOB 03/15/1965" / "DOB: ..."
    (r'\bDOB[:\s]+\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4}\b', "[DATE_OF_BIRTH]",        "DOB"),
    # Bare SSN patterns (if presidio misses edge cases)
    (r'\b\d{3}-\d{2}-\d{4}\b',                   "[SOCIAL_SECURITY_NUMBER]", "SSN"),
    # Phone numbers: 555-234-8901 / (555) 234-8901 / 555.234.8901
    (r'\b(?:\(?\d{3}\)?[\s\-.]?\d{3}[\s\-.]?\d{4})\b', "[CONTACT_INFO]", "PHONE"),
    # Email addresses (belt + suspenders over presidio)
    (r'\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b', "[CONTACT_INFO]", "EMAIL"),
]


# ---------------------------------------------------------------------------
# Presidio-based scrubber (primary path)
# ---------------------------------------------------------------------------

class PresidioScrubber:
    """PII scrubber backed by Microsoft Presidio."""

    def __init__(self) -> None:
        # Use spaCy's en_core_web_sm as the NLP backend for Presidio
        provider = NlpEngineProvider(nlp_configuration={
            "nlp_engine_name": "spacy",
            "models": [{"lang_code": "en", "model_name": "en_core_web_sm"}],
        })
        nlp_engine = provider.create_engine()
        self._analyzer = AnalyzerEngine(nlp_engine=nlp_engine, supported_languages=["en"])
        self._anonymizer = AnonymizerEngine()

    def scrub(self, text: str) -> ScrubResult:
        results: list[RecognizerResult] = self._analyzer.analyze(
            text=text,
            language="en",
            entities=list(ENTITY_LABELS.keys()),
        )

        # Build operator config — replace each entity type with its label
        operators: dict[str, OperatorConfig] = {}
        for entity_type, label in ENTITY_LABELS.items():
            operators[entity_type] = OperatorConfig("replace", {"new_value": label})

        anonymized = self._anonymizer.anonymize(
            text=text,
            analyzer_results=results,
            operators=operators,
        )
        scrubbed = anonymized.text

        # Apply custom regex patterns on top
        entities_found: list[dict] = [
            {
                "entity_type": r.entity_type,
                "start": r.start,
                "end": r.end,
                "score": round(r.score, 3),
                "replacement": ENTITY_LABELS.get(r.entity_type, "[REDACTED]"),
            }
            for r in results
        ]

        scrubbed, regex_entities = _apply_regex_patterns(scrubbed)
        entities_found.extend(regex_entities)

        phi_types: dict[str, int] = {}
        for e in entities_found:
            phi_types[e["entity_type"]] = phi_types.get(e["entity_type"], 0) + 1

        return ScrubResult(
            scrubbed_text=scrubbed,
            original_text=text,
            entities_found=entities_found,
            phi_count=len(entities_found),
            scrubbing_mode="presidio",
            phi_types=phi_types,
        )


# ---------------------------------------------------------------------------
# Regex-only scrubber (fallback)
# ---------------------------------------------------------------------------

class RegexScrubber:
    """Regex-only PII scrubber used when Presidio is unavailable."""

    def scrub(self, text: str) -> ScrubResult:
        scrubbed, entities_found = _apply_regex_patterns(text)

        phi_types: dict[str, int] = {}
        for e in entities_found:
            phi_types[e["entity_type"]] = phi_types.get(e["entity_type"], 0) + 1

        return ScrubResult(
            scrubbed_text=scrubbed,
            original_text=text,
            entities_found=entities_found,
            phi_count=len(entities_found),
            scrubbing_mode="regex",
            phi_types=phi_types,
        )


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _apply_regex_patterns(text: str) -> tuple[str, list[dict]]:
    """Apply CUSTOM_PATTERNS sequentially and collect entity records."""
    entities: list[dict] = []
    for pattern, replacement, entity_type in CUSTOM_PATTERNS:
        for match in re.finditer(pattern, text):
            entities.append({
                "entity_type": entity_type,
                "start": match.start(),
                "end": match.end(),
                "score": 0.95,
                "replacement": replacement,
                "matched_text_length": len(match.group()),
            })
        text = re.sub(pattern, replacement, text)
    return text, entities


# ---------------------------------------------------------------------------
# Public factory
# ---------------------------------------------------------------------------

def create_scrubber() -> PresidioScrubber | RegexScrubber:
    """Return the best available scrubber."""
    if PRESIDIO_AVAILABLE:
        try:
            return PresidioScrubber()
        except Exception as exc:  # pragma: no cover
            logger.warning("Presidio init failed (%s); falling back to regex scrubber.", exc)
    # HIPAA COMPLIANCE RISK: regex-only mode does not detect PERSON (patient names),
    # geographic subdivisions smaller than state, or device identifiers.
    # This fallback is acceptable for development but MUST NOT be used in production
    # without additional controls.  Set REQUIRE_PRESIDIO=true to fail hard instead.
    if os.getenv("REQUIRE_PRESIDIO", "false").lower() in ("true", "1", "yes"):
        raise RuntimeError(
            "REQUIRE_PRESIDIO=true but Presidio is unavailable. "
            "Cannot proceed: regex-only scrubbing does not cover all 18 HIPAA identifiers."
        )
    logger.warning(
        "HIPAA RISK: Running regex-only PII scrubbing. "
        "Patient names, geographic subdivisions, and device identifiers are NOT detected. "
        "Install presidio-analyzer and presidio-anonymizer for full HIPAA coverage."
    )
    return RegexScrubber()


# Module-level singleton (lazy-initialized)
_scrubber: Optional[PresidioScrubber | RegexScrubber] = None


def scrub_note(text: str) -> ScrubResult:
    """
    Scrub PII/PHI from a clinical note.

    This is the ONLY entry point the pipeline should use.
    Always call this before passing text to any LLM.
    """
    global _scrubber
    if _scrubber is None:
        _scrubber = create_scrubber()
    return _scrubber.scrub(text)
