"""
Tests for pipeline/fhir_mapper.py

Verifies:
- FHIR Bundle structure is valid R4.
- Condition resources are created for ICD-10 codes.
- MedicationStatement resources are created for medications.
- Validate_bundle correctly identifies valid / invalid bundles.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.fhir_mapper import (
    map_to_fhir,
    bundle_to_json,
    validate_bundle,
    _condition_dict,
    _medication_statement_dict,
    _bundle_dict,
    _parse_dose_value,
    _parse_dose_unit,
)
from pipeline.entity_extractor import ICDCode, Medication


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

ICD_CODE_MI = ICDCode(code="I21.9", description="Acute myocardial infarction, unspecified", confidence=0.92)
ICD_CODE_HTN = ICDCode(code="I10", description="Essential (primary) hypertension", confidence=0.95)
ICD_CODE_LOW = ICDCode(code="Z00.00", description="General examination", confidence=0.55)

MED_ASPIRIN = Medication(name="Aspirin", dose="325mg", frequency="once daily", route="oral", confidence=0.88)
MED_METOPROLOL = Medication(name="Metoprolol", dose="25mg", frequency="twice daily", route="oral", confidence=0.85)
MED_UNKNOWN_DOSE = Medication(name="Gabapentin", dose="unknown", frequency="unknown", route="oral", confidence=0.62)


# ---------------------------------------------------------------------------
# Dose parsing helpers
# ---------------------------------------------------------------------------

class TestDoseParsing:
    def test_parse_dose_value_standard(self):
        assert _parse_dose_value("325mg") == 325.0

    def test_parse_dose_value_decimal(self):
        assert _parse_dose_value("2.5mg") == 2.5

    def test_parse_dose_value_unknown(self):
        assert _parse_dose_value("unknown") == 0.0

    def test_parse_dose_unit_mg(self):
        assert _parse_dose_unit("325mg") == "mg"

    def test_parse_dose_unit_mcg(self):
        assert _parse_dose_unit("500mcg") == "mcg"

    def test_parse_dose_unit_unknown(self):
        result = _parse_dose_unit("unknown")
        assert result == "mg"  # default fallback


# ---------------------------------------------------------------------------
# Condition resource tests
# ---------------------------------------------------------------------------

class TestConditionDict:
    def test_resource_type(self):
        cond = _condition_dict(ICD_CODE_MI, "N001")
        assert cond["resourceType"] == "Condition"

    def test_icd_code_present(self):
        cond = _condition_dict(ICD_CODE_MI, "N001")
        coding = cond["code"]["coding"][0]
        assert coding["code"] == "I21.9"

    def test_icd_system_is_icd10cm(self):
        cond = _condition_dict(ICD_CODE_MI, "N001")
        coding = cond["code"]["coding"][0]
        assert "icd-10" in coding["system"].lower()

    def test_clinical_status_active(self):
        cond = _condition_dict(ICD_CODE_MI, "N001")
        status_code = cond["clinicalStatus"]["coding"][0]["code"]
        assert status_code == "active"

    def test_verification_status_confirmed(self):
        cond = _condition_dict(ICD_CODE_MI, "N001")
        ver_code = cond["verificationStatus"]["coding"][0]["code"]
        assert ver_code == "confirmed"

    def test_subject_reference_present(self):
        cond = _condition_dict(ICD_CODE_MI, "N001")
        assert "Patient" in cond["subject"]["reference"]

    def test_confidence_extension(self):
        cond = _condition_dict(ICD_CODE_MI, "N001")
        extensions = cond.get("extension", [])
        conf_ext = next(
            (e for e in extensions if "confidence" in e.get("url", "")),
            None,
        )
        assert conf_ext is not None
        assert conf_ext["valueDecimal"] == 0.92

    def test_note_id_extension(self):
        cond = _condition_dict(ICD_CODE_MI, "N001")
        extensions = cond.get("extension", [])
        note_ext = next(
            (e for e in extensions if "source-note-id" in e.get("url", "")),
            None,
        )
        assert note_ext is not None
        assert note_ext["valueString"] == "N001"

    def test_has_recorded_date(self):
        cond = _condition_dict(ICD_CODE_HTN, "N003")
        assert "recordedDate" in cond
        assert cond["recordedDate"]

    def test_has_resource_id(self):
        cond = _condition_dict(ICD_CODE_MI, "N001")
        assert "id" in cond
        assert len(cond["id"]) > 0


# ---------------------------------------------------------------------------
# MedicationStatement resource tests
# ---------------------------------------------------------------------------

class TestMedicationStatementDict:
    def test_resource_type(self):
        ms = _medication_statement_dict(MED_ASPIRIN, "N001")
        assert ms["resourceType"] == "MedicationStatement"

    def test_medication_name_in_text(self):
        ms = _medication_statement_dict(MED_ASPIRIN, "N001")
        assert ms["medicationCodeableConcept"]["text"] == "Aspirin"

    def test_status_active(self):
        ms = _medication_statement_dict(MED_ASPIRIN, "N001")
        assert ms["status"] == "active"

    def test_subject_reference_present(self):
        ms = _medication_statement_dict(MED_ASPIRIN, "N001")
        assert "Patient" in ms["subject"]["reference"]

    def test_dosage_present(self):
        ms = _medication_statement_dict(MED_ASPIRIN, "N001")
        assert len(ms["dosage"]) > 0

    def test_dosage_text(self):
        ms = _medication_statement_dict(MED_ASPIRIN, "N001")
        dosage_text = ms["dosage"][0]["text"]
        assert "Aspirin" in dosage_text

    def test_confidence_extension(self):
        ms = _medication_statement_dict(MED_ASPIRIN, "N001")
        extensions = ms.get("extension", [])
        conf_ext = next(
            (e for e in extensions if "confidence" in e.get("url", "")),
            None,
        )
        assert conf_ext is not None

    def test_note_id_extension(self):
        ms = _medication_statement_dict(MED_METOPROLOL, "N001")
        extensions = ms.get("extension", [])
        note_ext = next(
            (e for e in extensions if "source-note-id" in e.get("url", "")),
            None,
        )
        assert note_ext is not None

    def test_has_resource_id(self):
        ms = _medication_statement_dict(MED_ASPIRIN, "N001")
        assert "id" in ms


# ---------------------------------------------------------------------------
# Bundle creation tests
# ---------------------------------------------------------------------------

class TestBundleDict:
    def test_resource_type_bundle(self):
        bundle = _bundle_dict([], "N001")
        assert bundle["resourceType"] == "Bundle"

    def test_bundle_type_collection(self):
        bundle = _bundle_dict([], "N001")
        assert bundle["type"] == "collection"

    def test_empty_bundle_has_zero_entries(self):
        bundle = _bundle_dict([], "N001")
        assert bundle["total"] == 0

    def test_non_empty_bundle_entry_count(self):
        resources = [_condition_dict(ICD_CODE_MI, "N001"), _condition_dict(ICD_CODE_HTN, "N001")]
        bundle = _bundle_dict(resources, "N001")
        assert bundle["total"] == 2

    def test_entries_have_full_url(self):
        resources = [_condition_dict(ICD_CODE_MI, "N001")]
        bundle = _bundle_dict(resources, "N001")
        for entry in bundle["entry"]:
            assert "fullUrl" in entry
            assert entry["fullUrl"].startswith("urn:uuid:")


# ---------------------------------------------------------------------------
# map_to_fhir integration tests
# ---------------------------------------------------------------------------

class TestMapToFHIR:
    def test_returns_dict(self):
        bundle = map_to_fhir("N001", [ICD_CODE_MI], [MED_ASPIRIN])
        assert isinstance(bundle, dict)

    def test_resource_type_bundle(self):
        bundle = map_to_fhir("N001", [ICD_CODE_MI], [MED_ASPIRIN])
        assert bundle["resourceType"] == "Bundle"

    def test_correct_entry_count(self):
        bundle = map_to_fhir("N001", [ICD_CODE_MI, ICD_CODE_HTN], [MED_ASPIRIN, MED_METOPROLOL])
        assert bundle["total"] == 4

    def test_empty_inputs(self):
        bundle = map_to_fhir("N999", [], [])
        assert bundle["total"] == 0

    def test_conditions_in_bundle(self):
        bundle = map_to_fhir("N001", [ICD_CODE_MI, ICD_CODE_HTN], [])
        resource_types = [e["resource"]["resourceType"] for e in bundle["entry"]]
        assert all(rt == "Condition" for rt in resource_types)

    def test_medication_statements_in_bundle(self):
        bundle = map_to_fhir("N001", [], [MED_ASPIRIN, MED_METOPROLOL])
        resource_types = [e["resource"]["resourceType"] for e in bundle["entry"]]
        assert all(rt == "MedicationStatement" for rt in resource_types)

    def test_mixed_resources(self):
        bundle = map_to_fhir("N001", [ICD_CODE_MI], [MED_ASPIRIN])
        resource_types = [e["resource"]["resourceType"] for e in bundle["entry"]]
        assert "Condition" in resource_types
        assert "MedicationStatement" in resource_types

    def test_bundle_serializable_to_json(self):
        import json
        bundle = map_to_fhir("N001", [ICD_CODE_MI], [MED_ASPIRIN])
        json_str = bundle_to_json(bundle)
        parsed = json.loads(json_str)
        assert parsed["resourceType"] == "Bundle"


# ---------------------------------------------------------------------------
# Bundle validation tests
# ---------------------------------------------------------------------------

class TestValidateBundle:
    def test_valid_bundle_passes(self):
        bundle = map_to_fhir("N001", [ICD_CODE_MI], [MED_ASPIRIN])
        is_valid, issues = validate_bundle(bundle)
        assert is_valid is True
        assert issues == []

    def test_missing_resource_type_fails(self):
        bundle = {"type": "collection", "entry": []}
        is_valid, issues = validate_bundle(bundle)
        assert is_valid is False
        assert any("resourceType" in issue for issue in issues)

    def test_wrong_resource_type_fails(self):
        bundle = {"resourceType": "Patient", "type": "collection", "entry": []}
        is_valid, issues = validate_bundle(bundle)
        assert is_valid is False

    def test_entry_missing_resource_type(self):
        bundle = {
            "resourceType": "Bundle",
            "type": "collection",
            "entry": [{"fullUrl": "urn:uuid:abc", "resource": {"id": "x"}}],
        }
        is_valid, issues = validate_bundle(bundle)
        assert is_valid is False
        assert any("resourceType" in issue for issue in issues)

    def test_empty_bundle_is_valid(self):
        bundle = {"resourceType": "Bundle", "type": "collection", "entry": []}
        is_valid, issues = validate_bundle(bundle)
        assert is_valid is True

    def test_multiple_codes_all_valid(self):
        bundle = map_to_fhir(
            "N007",
            [ICD_CODE_MI, ICD_CODE_HTN, ICD_CODE_LOW],
            [MED_ASPIRIN, MED_METOPROLOL, MED_UNKNOWN_DOSE],
        )
        is_valid, issues = validate_bundle(bundle)
        assert is_valid is True
