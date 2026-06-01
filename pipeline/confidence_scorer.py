"""
Confidence Scorer — aggregate and classify extraction confidence.

Scoring rules:
  HIGH   >= 0.85  → Automatically accepted, included in FHIR output.
  MEDIUM  0.60–0.84 → Included but flagged for optional review.
  LOW    < 0.60  → Sent to human-in-the-loop review queue before FHIR inclusion.

The scorer also produces a note-level quality summary.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from pipeline.entity_extractor import ExtractionResult

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------

HIGH_THRESHOLD: float = 0.85
MEDIUM_THRESHOLD: float = 0.60

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class ScoredEntity:
    entity_type: str               # "icd_code" | "medication"
    entity_value: str              # human-readable identifier
    confidence: float
    confidence_label: str
    requires_review: bool
    details: dict                  # original entity fields


@dataclass
class ScoringResult:
    scored_entities: list[ScoredEntity]
    high_confidence_count: int
    medium_confidence_count: int
    low_confidence_count: int
    overall_quality: str           # "HIGH" | "MEDIUM" | "LOW"
    review_required: bool          # True if any entity < MEDIUM_THRESHOLD
    note_quality_score: float      # 0.0 – 1.0

    @classmethod
    def empty(cls) -> "ScoringResult":
        return cls(
            scored_entities=[],
            high_confidence_count=0,
            medium_confidence_count=0,
            low_confidence_count=0,
            overall_quality="LOW",
            review_required=False,
            note_quality_score=0.0,
        )

    def to_dict(self) -> dict:
        return {
            "scored_entities": [vars(e) for e in self.scored_entities],
            "high_confidence_count": self.high_confidence_count,
            "medium_confidence_count": self.medium_confidence_count,
            "low_confidence_count": self.low_confidence_count,
            "overall_quality": self.overall_quality,
            "review_required": self.review_required,
            "note_quality_score": round(self.note_quality_score, 3),
        }


# ---------------------------------------------------------------------------
# Scoring logic
# ---------------------------------------------------------------------------


def _label(score: float) -> str:
    if score >= HIGH_THRESHOLD:
        return "HIGH"
    if score >= MEDIUM_THRESHOLD:
        return "MEDIUM"
    return "LOW"


def score_extractions(extraction: ExtractionResult) -> ScoringResult:
    """
    Score all extracted entities and build a ScoringResult.

    Parameters
    ----------
    extraction : ExtractionResult
        Output from entity_extractor.extract_entities().

    Returns
    -------
    ScoringResult
        Aggregated confidence summary with per-entity scores.
    """
    scored: list[ScoredEntity] = []

    # --- ICD codes ---
    for icd in extraction.icd_codes:
        scored.append(ScoredEntity(
            entity_type="icd_code",
            entity_value=f"{icd.code} — {icd.description}",
            confidence=icd.confidence,
            confidence_label=_label(icd.confidence),
            requires_review=icd.confidence < MEDIUM_THRESHOLD,
            details=vars(icd),
        ))

    # --- Medications ---
    for med in extraction.medications:
        # Boost confidence slightly if dose and frequency were captured
        adjusted = med.confidence
        if med.dose != "unknown" and med.frequency != "unknown":
            adjusted = min(1.0, med.confidence + 0.05)
        if med.dose == "unknown" and med.frequency == "unknown":
            adjusted = max(0.0, med.confidence - 0.10)

        scored.append(ScoredEntity(
            entity_type="medication",
            entity_value=f"{med.name} {med.dose} {med.route}",
            confidence=round(adjusted, 3),
            confidence_label=_label(adjusted),
            requires_review=adjusted < MEDIUM_THRESHOLD,
            details={**vars(med), "adjusted_confidence": adjusted},
        ))

    if not scored:
        return ScoringResult.empty()

    # --- Aggregate counts ---
    high_count = sum(1 for e in scored if e.confidence_label == "HIGH")
    med_count  = sum(1 for e in scored if e.confidence_label == "MEDIUM")
    low_count  = sum(1 for e in scored if e.confidence_label == "LOW")

    # Note-level quality score = weighted mean (HIGH=1, MEDIUM=0.7, LOW=0.4)
    if scored:
        weights = {
            "HIGH": 1.0, "MEDIUM": 0.7, "LOW": 0.4
        }
        quality_score = sum(
            weights[e.confidence_label] for e in scored
        ) / len(scored)
    else:
        quality_score = 0.0

    # Overall quality label driven by majority + worst-case blend
    if low_count > 0:
        overall = "LOW"
    elif med_count > high_count:
        overall = "MEDIUM"
    else:
        overall = "HIGH"

    return ScoringResult(
        scored_entities=scored,
        high_confidence_count=high_count,
        medium_confidence_count=med_count,
        low_confidence_count=low_count,
        overall_quality=overall,
        review_required=low_count > 0,
        note_quality_score=round(quality_score, 3),
    )


def filter_for_fhir(result: ScoringResult) -> list[ScoredEntity]:
    """
    Return entities safe for automatic FHIR mapping.
    Entities with confidence < MEDIUM_THRESHOLD are withheld pending human review.
    """
    return [e for e in result.scored_entities if e.confidence >= MEDIUM_THRESHOLD]
