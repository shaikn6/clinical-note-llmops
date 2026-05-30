"""
Tests for pipeline/pii_scrubber.py

Verifies that:
- Known PHI patterns are removed from clinical notes.
- Scrubbed text is returned with correct structure.
- No PHI values leak into the output (positive & negative cases).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from pipeline.pii_scrubber import scrub_note, ScrubResult, CUSTOM_PATTERNS, _apply_regex_patterns


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

NOTE_WITH_PHI = (
    "Patient John Smith, DOB 03/15/1965, MRN 789234. "
    "Email: jsmith@hospital.com. Phone: 555-234-8901. "
    "SSN: 123-45-6789. Address: 123 Maple St, Dayton OH. "
    "Assessment: Acute MI (ICD-10: I21.9)."
)

NOTE_WITHOUT_PHI = (
    "[PATIENT_NAME], [DATE_OF_BIRTH], [MEDICAL_RECORD_NUMBER]. "
    "Assessment: Acute MI (ICD-10: I21.9)."
)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestScrubResult:
    def test_returns_scrub_result_type(self):
        result = scrub_note("Patient Name, DOB 01/01/1980, MRN 12345.")
        assert isinstance(result, ScrubResult)

    def test_scrubbed_text_is_string(self):
        result = scrub_note("John Doe, phone 555-999-1234.")
        assert isinstance(result.scrubbed_text, str)

    def test_original_text_preserved(self):
        text = "Patient Jane Doe, MRN 999999."
        result = scrub_note(text)
        assert result.original_text == text

    def test_phi_count_is_non_negative(self):
        result = scrub_note("No PHI here.")
        assert result.phi_count >= 0

    def test_phi_types_is_dict(self):
        result = scrub_note("John Smith, DOB 12/31/1955.")
        assert isinstance(result.phi_types, dict)

    def test_scrubbing_mode_set(self):
        result = scrub_note("Test note.")
        assert result.scrubbing_mode in ("presidio", "regex")


class TestMRNScrubbing:
    def test_mrn_label_removed(self):
        text = "Patient seen today. MRN 789234 confirmed."
        result = scrub_note(text)
        assert "789234" not in result.scrubbed_text

    def test_mrn_replacement_label_present(self):
        text = "MRN 123456 on file."
        result = scrub_note(text)
        assert "[MEDICAL_RECORD_NUMBER]" in result.scrubbed_text

    def test_mrn_with_colon(self):
        text = "Medical Record: MRN: 445567."
        result = scrub_note(text)
        assert "445567" not in result.scrubbed_text


class TestDOBScrubbing:
    def test_dob_label_removed(self):
        text = "DOB 03/15/1965 is on the chart."
        result = scrub_note(text)
        assert "03/15/1965" not in result.scrubbed_text

    def test_dob_replacement_present(self):
        text = "Patient DOB 07/22/1978."
        result = scrub_note(text)
        assert "[DATE_OF_BIRTH]" in result.scrubbed_text


class TestPhoneEmail:
    def test_phone_number_scrubbed(self):
        text = "Call us at 555-234-8901 for follow-up."
        result = scrub_note(text)
        assert "555-234-8901" not in result.scrubbed_text

    def test_email_scrubbed(self):
        text = "Contact jsmith@hospital.com for records."
        result = scrub_note(text)
        assert "jsmith@hospital.com" not in result.scrubbed_text

    def test_contact_info_replacement(self):
        text = "Phone: 937-555-0192"
        result = scrub_note(text)
        assert "[CONTACT_INFO]" in result.scrubbed_text


class TestSSN:
    def test_ssn_scrubbed(self):
        text = "SSN: 123-45-6789 on file."
        result = scrub_note(text)
        assert "123-45-6789" not in result.scrubbed_text

    def test_ssn_replacement_present(self):
        text = "SSN: 987-65-4321"
        result = scrub_note(text)
        assert "[SOCIAL_SECURITY_NUMBER]" in result.scrubbed_text


class TestIcdCodePreservation:
    def test_icd_code_not_scrubbed(self):
        """ICD-10 codes (e.g. I21.9) must survive scrubbing — they are not PHI."""
        text = "Assessment: Acute MI (ICD-10: I21.9). MRN 789234."
        result = scrub_note(text)
        assert "I21.9" in result.scrubbed_text

    def test_clinical_text_preserved(self):
        text = "Aspirin 325mg PO stat for acute MI. DOB 01/01/1960."
        result = scrub_note(text)
        assert "Aspirin" in result.scrubbed_text
        assert "acute MI" in result.scrubbed_text


class TestFullNote:
    def test_full_note_phi_removed(self):
        result = scrub_note(NOTE_WITH_PHI)
        # Regex-detectable PHI must always be removed
        for phi_value in ["789234", "jsmith@hospital.com",
                          "555-234-8901", "123-45-6789"]:
            assert phi_value not in result.scrubbed_text, (
                f"PHI value '{phi_value}' still present in scrubbed text"
            )
        # PERSON names only removed by Presidio (not available in regex-only mode)
        if result.scrubbing_mode == "presidio":
            assert "John Smith" not in result.scrubbed_text, (
                "PERSON name 'John Smith' not removed by Presidio"
            )

    def test_full_note_icd_survives(self):
        result = scrub_note(NOTE_WITH_PHI)
        assert "I21.9" in result.scrubbed_text


class TestRegexPatterns:
    def test_regex_replace_mrn(self):
        text, entities = _apply_regex_patterns("MRN 654321 on record.")
        assert "654321" not in text
        assert any(e["entity_type"] == "MRN" for e in entities)

    def test_regex_replace_phone(self):
        text, entities = _apply_regex_patterns("Phone: 614-555-0388")
        assert "614-555-0388" not in text

    def test_regex_replace_email(self):
        text, entities = _apply_regex_patterns("Email: nancy@example.com")
        assert "nancy@example.com" not in text

    def test_regex_entities_list(self):
        _, entities = _apply_regex_patterns("MRN 111222. Phone 555-111-2222. DOB 01/15/1970.")
        assert len(entities) >= 3
