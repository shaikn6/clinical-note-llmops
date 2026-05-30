"""
Tests for fhir/fhir_r4_builder.py

Covers:
  - Individual resource builders: Patient, Condition, Observation,
    MedicationRequest, Procedure, DiagnosticReport
  - BundleContents assembly
  - validate_r4_bundle
  - build_full_bundle (high-level convenience)
  - export_bundle_json
  - FHIRValidationError raised on invalid input
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import json
import pytest

from fhir.fhir_r4_builder import (
    build_patient,
    build_condition,
    build_observation,
    build_medication_request,
    build_procedure,
    build_diagnostic_report,
    assemble_bundle,
    validate_r4_bundle,
    build_full_bundle,
    export_bundle_json,
    BundleContents,
    FHIRValidationError,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

PATIENT_ID = "test-patient-001"
ICD_CODE = "I10"
ICD_DISPLAY = "Essential (primary) hypertension"


# ---------------------------------------------------------------------------
# Patient
# ---------------------------------------------------------------------------

class TestBuildPatient:
    def test_returns_dict_with_correct_resource_type(self):
        p = build_patient(PATIENT_ID)
        assert p["resourceType"] == "Patient"

    def test_patient_id_set(self):
        p = build_patient(PATIENT_ID)
        assert p["id"] == PATIENT_ID

    def test_default_gender_unknown(self):
        p = build_patient(PATIENT_ID)
        assert p["gender"] == "unknown"

    def test_explicit_gender(self):
        p = build_patient(PATIENT_ID, gender="female")
        assert p["gender"] == "female"

    def test_invalid_gender_raises(self):
        with pytest.raises(FHIRValidationError, match="gender"):
            build_patient(PATIENT_ID, gender="robot")

    def test_birth_date_included_when_provided(self):
        p = build_patient(PATIENT_ID, birth_date="1980-03-15")
        assert p["birthDate"] == "1980-03-15"

    def test_invalid_birth_date_raises(self):
        with pytest.raises(FHIRValidationError, match="birthDate"):
            build_patient(PATIENT_ID, birth_date="03/15/1980")

    def test_meta_profile_present(self):
        p = build_patient(PATIENT_ID)
        assert "profile" in p["meta"]

    def test_name_family_defaults_redacted(self):
        p = build_patient(PATIENT_ID)
        assert p["name"][0]["family"] == "[REDACTED]"


# ---------------------------------------------------------------------------
# Condition
# ---------------------------------------------------------------------------

class TestBuildCondition:
    def test_resource_type(self):
        c = build_condition(PATIENT_ID, icd_code=ICD_CODE, icd_display=ICD_DISPLAY)
        assert c["resourceType"] == "Condition"

    def test_subject_reference(self):
        c = build_condition(PATIENT_ID, icd_code=ICD_CODE, icd_display=ICD_DISPLAY)
        assert c["subject"]["reference"] == f"Patient/{PATIENT_ID}"

    def test_icd_code_in_coding(self):
        c = build_condition(PATIENT_ID, icd_code=ICD_CODE, icd_display=ICD_DISPLAY)
        coding = c["code"]["coding"][0]
        assert coding["code"] == ICD_CODE
        assert coding["system"] == "http://hl7.org/fhir/sid/icd-10-cm"

    def test_confidence_extension(self):
        c = build_condition(PATIENT_ID, icd_code=ICD_CODE, icd_display=ICD_DISPLAY, confidence=0.92)
        exts = {e["url"]: e for e in c["extension"]}
        conf_ext = exts["http://example.com/fhir/extension/extraction-confidence"]
        assert conf_ext["valueDecimal"] == pytest.approx(0.92, abs=0.001)

    def test_empty_icd_code_raises(self):
        with pytest.raises(FHIRValidationError, match="icd_code"):
            build_condition(PATIENT_ID, icd_code="", icd_display="test")

    def test_invalid_clinical_status_raises(self):
        with pytest.raises(FHIRValidationError, match="clinicalStatus"):
            build_condition(PATIENT_ID, icd_code=ICD_CODE, icd_display=ICD_DISPLAY,
                            clinical_status="cured")

    def test_invalid_verification_status_raises(self):
        with pytest.raises(FHIRValidationError, match="verificationStatus"):
            build_condition(PATIENT_ID, icd_code=ICD_CODE, icd_display=ICD_DISPLAY,
                            verification_status="maybe")

    def test_note_id_in_extension(self):
        c = build_condition(PATIENT_ID, icd_code=ICD_CODE, icd_display=ICD_DISPLAY, note_id="NOTE-1")
        note_exts = [e for e in c["extension"] if "source-note-id" in e["url"]]
        assert note_exts[0]["valueString"] == "NOTE-1"


# ---------------------------------------------------------------------------
# Observation
# ---------------------------------------------------------------------------

class TestBuildObservation:
    def test_resource_type(self):
        obs = build_observation(PATIENT_ID, loinc_code="59261-8", loinc_display="Hemoglobin A1c")
        assert obs["resourceType"] == "Observation"

    def test_status_set(self):
        obs = build_observation(PATIENT_ID, loinc_code="59261-8", loinc_display="HbA1c")
        assert obs["status"] == "final"

    def test_value_quantity_set(self):
        obs = build_observation(
            PATIENT_ID, loinc_code="59261-8", loinc_display="HbA1c",
            value_quantity=8.2, value_unit="%"
        )
        assert obs["valueQuantity"]["value"] == pytest.approx(8.2)
        assert obs["valueQuantity"]["unit"] == "%"

    def test_invalid_status_raises(self):
        with pytest.raises(FHIRValidationError, match="status"):
            build_observation(PATIENT_ID, loinc_code="59261-8", loinc_display="HbA1c", status="bad")

    def test_missing_loinc_code_raises(self):
        with pytest.raises(FHIRValidationError, match="loinc_code"):
            build_observation(PATIENT_ID, loinc_code="", loinc_display="HbA1c")

    def test_non_numeric_value_quantity_raises(self):
        with pytest.raises(FHIRValidationError):
            build_observation(
                PATIENT_ID, loinc_code="59261-8", loinc_display="HbA1c",
                value_quantity="high"   # type: ignore[arg-type]
            )


# ---------------------------------------------------------------------------
# MedicationRequest
# ---------------------------------------------------------------------------

class TestBuildMedicationRequest:
    def test_resource_type(self):
        mr = build_medication_request(PATIENT_ID, medication_name="Aspirin")
        assert mr["resourceType"] == "MedicationRequest"

    def test_status_and_intent(self):
        mr = build_medication_request(PATIENT_ID, medication_name="Aspirin")
        assert mr["status"] == "active"
        assert mr["intent"] == "order"

    def test_medication_name_in_coding(self):
        mr = build_medication_request(PATIENT_ID, medication_name="Metformin")
        assert mr["medicationCodeableConcept"]["text"] == "Metformin"

    def test_dose_quantity_set(self):
        mr = build_medication_request(
            PATIENT_ID, medication_name="Aspirin",
            dose_value=325.0, dose_unit="mg"
        )
        dose = mr["dosageInstruction"][0]["doseAndRate"][0]["doseQuantity"]
        assert dose["value"] == pytest.approx(325.0)
        assert dose["unit"] == "mg"

    def test_invalid_status_raises(self):
        with pytest.raises(FHIRValidationError, match="status"):
            build_medication_request(PATIENT_ID, medication_name="A", status="given")

    def test_empty_medication_name_raises(self):
        with pytest.raises(FHIRValidationError, match="medication_name"):
            build_medication_request(PATIENT_ID, medication_name="")


# ---------------------------------------------------------------------------
# Procedure
# ---------------------------------------------------------------------------

class TestBuildProcedure:
    def test_resource_type(self):
        proc = build_procedure(PATIENT_ID, snomed_code="80146002", snomed_display="Appendectomy")
        assert proc["resourceType"] == "Procedure"

    def test_status_completed(self):
        proc = build_procedure(PATIENT_ID, snomed_code="80146002", snomed_display="Appendectomy")
        assert proc["status"] == "completed"

    def test_snomed_system_in_coding(self):
        proc = build_procedure(PATIENT_ID, snomed_code="80146002", snomed_display="Appendectomy")
        assert proc["code"]["coding"][0]["system"] == "http://snomed.info/sct"

    def test_invalid_status_raises(self):
        with pytest.raises(FHIRValidationError, match="status"):
            build_procedure(PATIENT_ID, snomed_code="80146002", snomed_display="Op", status="done")

    def test_empty_snomed_code_raises(self):
        with pytest.raises(FHIRValidationError, match="snomed_code"):
            build_procedure(PATIENT_ID, snomed_code="", snomed_display="Op")


# ---------------------------------------------------------------------------
# DiagnosticReport
# ---------------------------------------------------------------------------

class TestBuildDiagnosticReport:
    def test_resource_type(self):
        dr = build_diagnostic_report(
            PATIENT_ID, loinc_code="24323-8", loinc_display="Comprehensive metabolic panel"
        )
        assert dr["resourceType"] == "DiagnosticReport"

    def test_loinc_system(self):
        dr = build_diagnostic_report(
            PATIENT_ID, loinc_code="24323-8", loinc_display="CMP"
        )
        assert dr["code"]["coding"][0]["system"] == "http://loinc.org"

    def test_conclusion_set(self):
        dr = build_diagnostic_report(
            PATIENT_ID, loinc_code="24323-8", loinc_display="CMP",
            conclusion="All values within normal range."
        )
        assert dr["conclusion"] == "All values within normal range."

    def test_invalid_status_raises(self):
        with pytest.raises(FHIRValidationError, match="status"):
            build_diagnostic_report(
                PATIENT_ID, loinc_code="24323-8", loinc_display="CMP", status="done"
            )

    def test_empty_loinc_code_raises(self):
        with pytest.raises(FHIRValidationError, match="loinc_code"):
            build_diagnostic_report(PATIENT_ID, loinc_code="", loinc_display="CMP")


# ---------------------------------------------------------------------------
# Bundle assembly & validation
# ---------------------------------------------------------------------------

class TestAssembleAndValidateBundle:
    def _make_contents(self) -> BundleContents:
        return BundleContents(
            patient=build_patient(PATIENT_ID),
            conditions=[
                build_condition(PATIENT_ID, icd_code=ICD_CODE, icd_display=ICD_DISPLAY)
            ],
            medication_requests=[
                build_medication_request(PATIENT_ID, medication_name="Aspirin")
            ],
        )

    def test_bundle_resource_type(self):
        bundle = assemble_bundle(self._make_contents(), note_id="N001")
        assert bundle["resourceType"] == "Bundle"

    def test_bundle_type_transaction(self):
        bundle = assemble_bundle(self._make_contents(), note_id="N001")
        assert bundle["type"] == "transaction"

    def test_bundle_total_matches_entry_count(self):
        bundle = assemble_bundle(self._make_contents(), note_id="N001")
        assert bundle["total"] == len(bundle["entry"])

    def test_validate_r4_bundle_passes(self):
        bundle = assemble_bundle(self._make_contents(), note_id="N001")
        is_valid, issues = validate_r4_bundle(bundle)
        assert is_valid, f"Unexpected validation issues: {issues}"

    def test_validate_bad_resource_type_fails(self):
        is_valid, issues = validate_r4_bundle({"resourceType": "Patient"})
        assert not is_valid

    def test_entry_has_full_url(self):
        bundle = assemble_bundle(self._make_contents())
        for entry in bundle["entry"]:
            assert entry.get("fullUrl", "").startswith("urn:uuid:")


# ---------------------------------------------------------------------------
# build_full_bundle (high-level)
# ---------------------------------------------------------------------------

class TestBuildFullBundle:
    def test_returns_valid_bundle(self):
        bundle = build_full_bundle(
            note_id="N001",
            patient_id=PATIENT_ID,
            icd_codes=[{"code": ICD_CODE, "description": ICD_DISPLAY, "confidence": 0.95}],
            medications=[{
                "name": "Aspirin", "dose": "325mg",
                "frequency": "once daily", "route": "oral", "confidence": 0.90,
            }],
        )
        is_valid, issues = validate_r4_bundle(bundle)
        assert is_valid, issues

    def test_patient_resource_included(self):
        bundle = build_full_bundle(note_id="N002", patient_id=PATIENT_ID)
        resource_types = {e["resource"]["resourceType"] for e in bundle["entry"]}
        assert "Patient" in resource_types

    def test_condition_for_each_icd(self):
        bundle = build_full_bundle(
            note_id="N003",
            patient_id=PATIENT_ID,
            icd_codes=[
                {"code": "I10",   "description": "Hypertension", "confidence": 0.95},
                {"code": "E11.9", "description": "Diabetes T2",  "confidence": 0.88},
            ],
        )
        conditions = [e for e in bundle["entry"] if e["resource"]["resourceType"] == "Condition"]
        assert len(conditions) == 2

    def test_medication_request_for_each_med(self):
        bundle = build_full_bundle(
            note_id="N004",
            patient_id=PATIENT_ID,
            medications=[
                {"name": "Metformin", "dose": "1000mg", "frequency": "BID", "route": "oral", "confidence": 0.9},
                {"name": "Aspirin",   "dose": "325mg",  "frequency": "QD",  "route": "oral", "confidence": 0.9},
            ],
        )
        med_reqs = [e for e in bundle["entry"] if e["resource"]["resourceType"] == "MedicationRequest"]
        assert len(med_reqs) == 2

    def test_gender_propagated(self):
        bundle = build_full_bundle(note_id="N005", patient_id=PATIENT_ID, patient_gender="female")
        patient_entries = [e for e in bundle["entry"] if e["resource"]["resourceType"] == "Patient"]
        assert patient_entries[0]["resource"]["gender"] == "female"

    def test_export_bundle_json_is_valid_json(self):
        bundle = build_full_bundle(note_id="N006", patient_id=PATIENT_ID)
        json_str = export_bundle_json(bundle)
        parsed = json.loads(json_str)
        assert parsed["resourceType"] == "Bundle"
