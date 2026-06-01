"""
Tests for pipeline/entity_extractor.py

Tests cover:
- Medication extraction from scrubbed clinical text (regex path).
- ICD-10 extraction in mock mode.
- Confidence label assignment.
- ExtractionResult structure.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import os

os.environ.setdefault("MOCK_MODE", "true")

from pipeline.entity_extractor import (
    extract_entities,
    ExtractionResult,
    ICDCode,
    Medication,
    _extract_medications_regex,
    _mock_llm_extract,
    _confidence_label,
)


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

SCRUBBED_CHEST_PAIN = (
    "[PATIENT_NAME], [DATE_OF_BIRTH], [MEDICAL_RECORD_NUMBER]. "
    "Chief complaint: chest pain radiating to left arm for 2 hours. "
    "Assessment: Acute myocardial infarction (ICD-10: I21.9). "
    "Medications: Aspirin 325mg PO stat, Metoprolol 25mg PO BID, Atorvastatin 40mg PO QHS."
)

SCRUBBED_DIABETES = (
    "[PATIENT_NAME], [DATE_OF_BIRTH], [MEDICAL_RECORD_NUMBER]. "
    "Assessment: Type 2 diabetes mellitus (ICD-10: E11.65). "
    "Medications: Metformin 1000mg PO BID, Empagliflozin 10mg PO daily."
)


# ---------------------------------------------------------------------------
# Confidence label tests
# ---------------------------------------------------------------------------

class TestConfidenceLabel:
    def test_high_label(self):
        assert _confidence_label(0.95) == "HIGH"
        assert _confidence_label(0.85) == "HIGH"

    def test_medium_label(self):
        assert _confidence_label(0.75) == "MEDIUM"
        assert _confidence_label(0.60) == "MEDIUM"

    def test_low_label(self):
        assert _confidence_label(0.59) == "LOW"
        assert _confidence_label(0.0) == "LOW"


# ---------------------------------------------------------------------------
# ICDCode dataclass tests
# ---------------------------------------------------------------------------

class TestICDCode:
    def test_confidence_label_auto_assigned(self):
        icd = ICDCode(code="I21.9", description="Acute MI", confidence=0.92)
        assert icd.confidence_label == "HIGH"

    def test_medium_confidence_label(self):
        icd = ICDCode(code="J18.9", description="Pneumonia", confidence=0.72)
        assert icd.confidence_label == "MEDIUM"

    def test_low_confidence_label(self):
        icd = ICDCode(code="Z00.00", description="Wellness exam", confidence=0.50)
        assert icd.confidence_label == "LOW"


# ---------------------------------------------------------------------------
# Medication dataclass tests
# ---------------------------------------------------------------------------

class TestMedication:
    def test_med_confidence_label(self):
        med = Medication(name="Aspirin", dose="325mg", frequency="once daily", route="oral", confidence=0.88)
        assert med.confidence_label == "HIGH"

    def test_med_attributes(self):
        med = Medication(name="Metformin", dose="1000mg", frequency="twice daily", route="oral", confidence=0.75)
        assert med.name == "Metformin"
        assert med.dose == "1000mg"
        assert med.route == "oral"


# ---------------------------------------------------------------------------
# Regex medication extractor tests
# ---------------------------------------------------------------------------

class TestRegexMedicationExtractor:
    def test_extracts_known_drug(self):
        meds = _extract_medications_regex("Patient taking Aspirin 325mg PO daily.")
        assert any(m.name.lower() == "aspirin" for m in meds)

    def test_extracts_dose(self):
        meds = _extract_medications_regex("Metoprolol 25mg PO BID.")
        assert any(m.dose == "25mg" for m in meds)

    def test_extracts_multiple_drugs(self):
        text = "Medications: Aspirin 325mg PO stat, Metoprolol 25mg PO BID, Atorvastatin 40mg PO QHS."
        meds = _extract_medications_regex(text)
        names = [m.name.lower() for m in meds]
        assert "aspirin" in names
        assert "metoprolol" in names
        assert "atorvastatin" in names

    def test_no_duplicates(self):
        text = "Aspirin 325mg PO. Aspirin 325mg."
        meds = _extract_medications_regex(text)
        aspirin_count = sum(1 for m in meds if m.name.lower() == "aspirin")
        assert aspirin_count == 1

    def test_confidence_higher_with_dose(self):
        meds_with_dose    = _extract_medications_regex("Metformin 1000mg PO BID.")
        meds_without_dose = _extract_medications_regex("Metformin taken daily.")
        if meds_with_dose and meds_without_dose:
            assert meds_with_dose[0].confidence >= meds_without_dose[0].confidence

    def test_empty_text(self):
        meds = _extract_medications_regex("")
        assert meds == []

    def test_no_drugs_in_clean_text(self):
        meds = _extract_medications_regex("The patient rested well overnight.")
        assert meds == []


# ---------------------------------------------------------------------------
# Mock LLM ICD extractor tests
# ---------------------------------------------------------------------------

class TestMockLLMExtract:
    def test_extracts_inline_icd(self):
        text = "Assessment: Acute MI (ICD-10: I21.9)."
        codes, raw = _mock_llm_extract(text)
        code_values = [c.code for c in codes]
        assert "I21.9" in code_values

    def test_extracts_from_keyword_diabetes(self):
        text = "Patient has diabetes with elevated glucose."
        codes, raw = _mock_llm_extract(text)
        assert len(codes) > 0

    def test_extracts_from_keyword_hypertension(self):
        text = "Essential hypertension noted."
        codes, raw = _mock_llm_extract(text)
        assert any(c.code == "I10" for c in codes)

    def test_returns_default_when_no_match(self):
        text = "Patient feeling well. No acute complaints."
        codes, raw = _mock_llm_extract(text)
        assert len(codes) >= 1  # default fallback

    def test_raw_response_is_valid_json(self):
        import json
        text = "chest pain present."
        _, raw = _mock_llm_extract(text)
        parsed = json.loads(raw)
        assert isinstance(parsed, list)

    def test_no_duplicate_codes(self):
        text = "Acute MI (ICD-10: I21.9). Also has myocardial infarction and chest pain."
        codes, _ = _mock_llm_extract(text)
        code_values = [c.code for c in codes]
        assert len(code_values) == len(set(code_values))

    def test_inline_icd_has_high_confidence(self):
        text = "Assessment: Hypertension (ICD-10: I10)."
        codes, _ = _mock_llm_extract(text)
        inline = [c for c in codes if c.code == "I10" and c.source == "inline"]
        if inline:
            assert inline[0].confidence >= 0.85


# ---------------------------------------------------------------------------
# Full extract_entities integration tests
# ---------------------------------------------------------------------------

class TestExtractEntities:
    def test_returns_extraction_result_type(self):
        result = extract_entities(SCRUBBED_CHEST_PAIN)
        assert isinstance(result, ExtractionResult)

    def test_icd_codes_list(self):
        result = extract_entities(SCRUBBED_CHEST_PAIN)
        assert isinstance(result.icd_codes, list)

    def test_medications_list(self):
        result = extract_entities(SCRUBBED_CHEST_PAIN)
        assert isinstance(result.medications, list)

    def test_extraction_mode_set(self):
        result = extract_entities(SCRUBBED_CHEST_PAIN)
        assert result.extraction_mode in ("mock", "openai")

    def test_chest_pain_icd_extracted(self):
        result = extract_entities(SCRUBBED_CHEST_PAIN)
        assert len(result.icd_codes) > 0

    def test_chest_pain_meds_extracted(self):
        result = extract_entities(SCRUBBED_CHEST_PAIN)
        assert len(result.medications) > 0

    def test_low_confidence_count_non_negative(self):
        result = extract_entities(SCRUBBED_CHEST_PAIN)
        assert result.low_confidence_count >= 0

    def test_to_dict_method(self):
        result = extract_entities(SCRUBBED_DIABETES)
        d = result.to_dict()
        assert "icd_codes" in d
        assert "medications" in d
        assert "extraction_mode" in d

    def test_diabetes_extraction(self):
        result = extract_entities(SCRUBBED_DIABETES)
        med_names = [m.name.lower() for m in result.medications]
        assert "metformin" in med_names

    def test_all_icd_codes_have_confidence(self):
        result = extract_entities(SCRUBBED_CHEST_PAIN)
        for icd in result.icd_codes:
            assert 0.0 <= icd.confidence <= 1.0

    def test_all_meds_have_confidence(self):
        result = extract_entities(SCRUBBED_CHEST_PAIN)
        for med in result.medications:
            assert 0.0 <= med.confidence <= 1.0
