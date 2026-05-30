"""
FHIR R4 Full Bundle Builder.

Produces validated FHIR R4 resources:
  - Patient
  - Condition
  - Observation
  - MedicationRequest
  - Procedure
  - DiagnosticReport

All resources are assembled into a FHIR R4 Bundle (type="transaction").
Schema validation enforces required fields and data-type constraints before export.

Usage::

    from fhir.fhir_r4_builder import build_full_bundle, export_bundle_json

    bundle = build_full_bundle(note_id="N001", patient_data={...}, ...)
    json_str = export_bundle_json(bundle)
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Timestamp helper
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _new_id() -> str:
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

class FHIRValidationError(ValueError):
    """Raised when a FHIR resource fails schema validation."""


def _require(obj: dict, *fields: str, resource_type: str = "") -> None:
    """Assert that required fields are present and non-empty."""
    for f in fields:
        if not obj.get(f):
            raise FHIRValidationError(
                f"[{resource_type}] Required field '{f}' is missing or empty."
            )


def _require_coding(coding_list: list[dict], resource_type: str, field_name: str) -> None:
    if not coding_list or not isinstance(coding_list, list):
        raise FHIRValidationError(
            f"[{resource_type}] '{field_name}.coding' must be a non-empty list."
        )
    for coding in coding_list:
        if not coding.get("system"):
            raise FHIRValidationError(
                f"[{resource_type}] Each coding in '{field_name}' must have 'system'."
            )
        if not coding.get("code"):
            raise FHIRValidationError(
                f"[{resource_type}] Each coding in '{field_name}' must have 'code'."
            )


# ---------------------------------------------------------------------------
# Resource builders
# ---------------------------------------------------------------------------

def build_patient(
    patient_id: str,
    *,
    gender: str = "unknown",
    birth_date: str = "",
    family_name: str = "[REDACTED]",
    given_names: list[str] | None = None,
) -> dict:
    """
    Build a FHIR R4 Patient resource.

    Required fields (R4 spec):
      - resourceType = "Patient"
      - id

    Parameters
    ----------
    patient_id : str
        Logical resource ID (de-identified).
    gender : str
        Administrative gender: "male" | "female" | "other" | "unknown".
    birth_date : str
        ISO-8601 date string (YYYY-MM-DD). May be empty for de-identified data.
    family_name : str
        Family/last name. Defaults to "[REDACTED]" for PHI compliance.
    given_names : list[str]
        List of given names. Defaults to ["[REDACTED]"].
    """
    if given_names is None:
        given_names = ["[REDACTED]"]

    valid_genders = {"male", "female", "other", "unknown"}
    if gender not in valid_genders:
        raise FHIRValidationError(
            f"[Patient] 'gender' must be one of {valid_genders}, got '{gender}'."
        )

    resource: dict[str, Any] = {
        "resourceType": "Patient",
        "id": patient_id,
        "meta": {
            "profile": ["http://hl7.org/fhir/StructureDefinition/Patient"],
            "lastUpdated": _now_iso(),
        },
        "text": {
            "status": "generated",
            "div": "<div xmlns='http://www.w3.org/1999/xhtml'>De-identified patient record</div>",
        },
        "name": [
            {
                "use": "official",
                "family": family_name,
                "given": given_names,
            }
        ],
        "gender": gender,
        "active": True,
    }

    if birth_date:
        # Validate ISO-8601 date format
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", birth_date):
            raise FHIRValidationError(
                f"[Patient] 'birthDate' must be YYYY-MM-DD, got '{birth_date}'."
            )
        resource["birthDate"] = birth_date

    _require(resource, "resourceType", "id", resource_type="Patient")
    return resource


def build_condition(
    patient_id: str,
    *,
    icd_code: str,
    icd_display: str,
    clinical_status: str = "active",
    verification_status: str = "confirmed",
    onset_date_time: str = "",
    note_id: str = "",
    confidence: float = 1.0,
) -> dict:
    """
    Build a FHIR R4 Condition resource.

    Required fields (R4 spec):
      - resourceType, id, code, subject, clinicalStatus, verificationStatus
    """
    valid_clinical = {"active", "recurrence", "relapse", "inactive", "remission", "resolved"}
    valid_verification = {"unconfirmed", "provisional", "differential", "confirmed", "refuted", "entered-in-error"}

    if clinical_status not in valid_clinical:
        raise FHIRValidationError(
            f"[Condition] 'clinicalStatus' must be one of {valid_clinical}."
        )
    if verification_status not in valid_verification:
        raise FHIRValidationError(
            f"[Condition] 'verificationStatus' must be one of {valid_verification}."
        )
    if not icd_code:
        raise FHIRValidationError("[Condition] 'icd_code' is required.")
    if not patient_id:
        raise FHIRValidationError("[Condition] 'patient_id' (subject reference) is required.")

    resource: dict[str, Any] = {
        "resourceType": "Condition",
        "id": _new_id(),
        "meta": {
            "profile": ["http://hl7.org/fhir/StructureDefinition/Condition"],
            "lastUpdated": _now_iso(),
        },
        "clinicalStatus": {
            "coding": [
                {
                    "system": "http://terminology.hl7.org/CodeSystem/condition-clinical",
                    "code": clinical_status,
                    "display": clinical_status.capitalize(),
                }
            ]
        },
        "verificationStatus": {
            "coding": [
                {
                    "system": "http://terminology.hl7.org/CodeSystem/condition-ver-status",
                    "code": verification_status,
                    "display": verification_status.capitalize(),
                }
            ]
        },
        "code": {
            "coding": [
                {
                    "system": "http://hl7.org/fhir/sid/icd-10-cm",
                    "code": icd_code,
                    "display": icd_display,
                }
            ],
            "text": icd_display,
        },
        "subject": {"reference": f"Patient/{patient_id}"},
        "recordedDate": _now_iso(),
        "extension": [
            {
                "url": "http://example.com/fhir/extension/extraction-confidence",
                "valueDecimal": round(float(confidence), 4),
            }
        ],
    }

    if onset_date_time:
        resource["onsetDateTime"] = onset_date_time
    if note_id:
        resource["extension"].append({
            "url": "http://example.com/fhir/extension/source-note-id",
            "valueString": note_id,
        })

    _require(resource, "resourceType", "id", "code", "subject", resource_type="Condition")
    _require_coding(resource["code"]["coding"], "Condition", "code")
    return resource


def build_observation(
    patient_id: str,
    *,
    loinc_code: str,
    loinc_display: str,
    value_quantity: float | None = None,
    value_unit: str = "",
    value_system: str = "http://unitsofmeasure.org",
    status: str = "final",
    effective_date_time: str = "",
    note_id: str = "",
    interpretation_code: str = "N",
    interpretation_display: str = "Normal",
) -> dict:
    """
    Build a FHIR R4 Observation resource (lab results, vitals).

    Required fields (R4 spec):
      - resourceType, id, status, code, subject
    """
    valid_statuses = {
        "registered", "preliminary", "final", "amended",
        "corrected", "cancelled", "entered-in-error", "unknown",
    }
    if status not in valid_statuses:
        raise FHIRValidationError(
            f"[Observation] 'status' must be one of {valid_statuses}."
        )
    if not loinc_code:
        raise FHIRValidationError("[Observation] 'loinc_code' is required.")
    if not patient_id:
        raise FHIRValidationError("[Observation] 'patient_id' is required.")

    resource: dict[str, Any] = {
        "resourceType": "Observation",
        "id": _new_id(),
        "meta": {
            "profile": ["http://hl7.org/fhir/StructureDefinition/Observation"],
            "lastUpdated": _now_iso(),
        },
        "status": status,
        "code": {
            "coding": [
                {
                    "system": "http://loinc.org",
                    "code": loinc_code,
                    "display": loinc_display,
                }
            ],
            "text": loinc_display,
        },
        "subject": {"reference": f"Patient/{patient_id}"},
        "effectiveDateTime": effective_date_time or _now_iso(),
        "interpretation": [
            {
                "coding": [
                    {
                        "system": "http://terminology.hl7.org/CodeSystem/v3-ObservationInterpretation",
                        "code": interpretation_code,
                        "display": interpretation_display,
                    }
                ]
            }
        ],
    }

    if value_quantity is not None:
        if not isinstance(value_quantity, (int, float)):
            raise FHIRValidationError(
                "[Observation] 'value_quantity' must be a numeric type."
            )
        resource["valueQuantity"] = {
            "value": float(value_quantity),
            "unit": value_unit,
            "system": value_system,
            "code": value_unit,
        }

    if note_id:
        resource["extension"] = [
            {
                "url": "http://example.com/fhir/extension/source-note-id",
                "valueString": note_id,
            }
        ]

    _require(resource, "resourceType", "id", "status", "code", "subject", resource_type="Observation")
    _require_coding(resource["code"]["coding"], "Observation", "code")
    return resource


def build_medication_request(
    patient_id: str,
    *,
    medication_name: str,
    rxnorm_code: str = "",
    dose_value: float | None = None,
    dose_unit: str = "mg",
    route_code: str = "26643006",
    route_display: str = "oral",
    frequency_text: str = "once daily",
    intent: str = "order",
    status: str = "active",
    note_id: str = "",
    confidence: float = 1.0,
) -> dict:
    """
    Build a FHIR R4 MedicationRequest resource.

    Required fields (R4 spec):
      - resourceType, id, status, intent, medication[x], subject
    """
    valid_statuses = {"active", "on-hold", "cancelled", "completed", "entered-in-error", "stopped", "draft", "unknown"}
    valid_intents = {"proposal", "plan", "order", "original-order", "reflex-order", "filler-order", "instance-order", "option"}

    if status not in valid_statuses:
        raise FHIRValidationError(f"[MedicationRequest] 'status' must be one of {valid_statuses}.")
    if intent not in valid_intents:
        raise FHIRValidationError(f"[MedicationRequest] 'intent' must be one of {valid_intents}.")
    if not medication_name:
        raise FHIRValidationError("[MedicationRequest] 'medication_name' is required.")
    if not patient_id:
        raise FHIRValidationError("[MedicationRequest] 'patient_id' is required.")

    med_coding: dict[str, Any] = {
        "system": "http://www.nlm.nih.gov/research/umls/rxnorm",
        "display": medication_name,
    }
    if rxnorm_code:
        med_coding["code"] = rxnorm_code

    dosage: dict[str, Any] = {
        "text": f"{medication_name} {dose_value or ''}{dose_unit} {route_display} {frequency_text}".strip(),
        "route": {
            "coding": [
                {
                    "system": "http://snomed.info/sct",
                    "code": route_code,
                    "display": route_display,
                }
            ],
            "text": route_display,
        },
        "timing": {
            "repeat": {
                "boundsPeriod": {
                    "start": _now_iso(),
                },
                "frequency": 1,
                "period": 1,
                "periodUnit": "d",
            }
        },
    }

    if dose_value is not None:
        if not isinstance(dose_value, (int, float)):
            raise FHIRValidationError("[MedicationRequest] 'dose_value' must be numeric.")
        dosage["doseAndRate"] = [
            {
                "type": {
                    "coding": [
                        {
                            "system": "http://terminology.hl7.org/CodeSystem/dose-rate-type",
                            "code": "ordered",
                            "display": "Ordered",
                        }
                    ]
                },
                "doseQuantity": {
                    "value": float(dose_value),
                    "unit": dose_unit,
                    "system": "http://unitsofmeasure.org",
                    "code": dose_unit,
                },
            }
        ]

    resource: dict[str, Any] = {
        "resourceType": "MedicationRequest",
        "id": _new_id(),
        "meta": {
            "profile": ["http://hl7.org/fhir/StructureDefinition/MedicationRequest"],
            "lastUpdated": _now_iso(),
        },
        "status": status,
        "intent": intent,
        "medicationCodeableConcept": {
            "coding": [med_coding],
            "text": medication_name,
        },
        "subject": {"reference": f"Patient/{patient_id}"},
        "authoredOn": _now_iso(),
        "dosageInstruction": [dosage],
        "extension": [
            {
                "url": "http://example.com/fhir/extension/extraction-confidence",
                "valueDecimal": round(float(confidence), 4),
            }
        ],
    }

    if note_id:
        resource["extension"].append({
            "url": "http://example.com/fhir/extension/source-note-id",
            "valueString": note_id,
        })

    _require(resource, "resourceType", "id", "status", "intent", resource_type="MedicationRequest")
    return resource


def build_procedure(
    patient_id: str,
    *,
    snomed_code: str,
    snomed_display: str,
    status: str = "completed",
    performed_date_time: str = "",
    note_id: str = "",
    confidence: float = 1.0,
) -> dict:
    """
    Build a FHIR R4 Procedure resource.

    Required fields (R4 spec):
      - resourceType, id, status, code, subject
    """
    valid_statuses = {
        "preparation", "in-progress", "not-done", "on-hold",
        "stopped", "completed", "entered-in-error", "unknown",
    }
    if status not in valid_statuses:
        raise FHIRValidationError(f"[Procedure] 'status' must be one of {valid_statuses}.")
    if not snomed_code:
        raise FHIRValidationError("[Procedure] 'snomed_code' is required.")
    if not patient_id:
        raise FHIRValidationError("[Procedure] 'patient_id' is required.")

    resource: dict[str, Any] = {
        "resourceType": "Procedure",
        "id": _new_id(),
        "meta": {
            "profile": ["http://hl7.org/fhir/StructureDefinition/Procedure"],
            "lastUpdated": _now_iso(),
        },
        "status": status,
        "code": {
            "coding": [
                {
                    "system": "http://snomed.info/sct",
                    "code": snomed_code,
                    "display": snomed_display,
                }
            ],
            "text": snomed_display,
        },
        "subject": {"reference": f"Patient/{patient_id}"},
        "performedDateTime": performed_date_time or _now_iso(),
        "extension": [
            {
                "url": "http://example.com/fhir/extension/extraction-confidence",
                "valueDecimal": round(float(confidence), 4),
            }
        ],
    }

    if note_id:
        resource["extension"].append({
            "url": "http://example.com/fhir/extension/source-note-id",
            "valueString": note_id,
        })

    _require(resource, "resourceType", "id", "status", "code", "subject", resource_type="Procedure")
    _require_coding(resource["code"]["coding"], "Procedure", "code")
    return resource


def build_diagnostic_report(
    patient_id: str,
    *,
    loinc_code: str,
    loinc_display: str,
    status: str = "final",
    category_code: str = "LAB",
    category_display: str = "Laboratory",
    conclusion: str = "",
    result_references: list[str] | None = None,
    note_id: str = "",
) -> dict:
    """
    Build a FHIR R4 DiagnosticReport resource.

    Required fields (R4 spec):
      - resourceType, id, status, code, subject
    """
    valid_statuses = {
        "registered", "partial", "preliminary", "final",
        "amended", "corrected", "appended", "cancelled", "entered-in-error", "unknown",
    }
    if status not in valid_statuses:
        raise FHIRValidationError(f"[DiagnosticReport] 'status' must be one of {valid_statuses}.")
    if not loinc_code:
        raise FHIRValidationError("[DiagnosticReport] 'loinc_code' is required.")
    if not patient_id:
        raise FHIRValidationError("[DiagnosticReport] 'patient_id' is required.")

    resource: dict[str, Any] = {
        "resourceType": "DiagnosticReport",
        "id": _new_id(),
        "meta": {
            "profile": ["http://hl7.org/fhir/StructureDefinition/DiagnosticReport"],
            "lastUpdated": _now_iso(),
        },
        "status": status,
        "category": [
            {
                "coding": [
                    {
                        "system": "http://terminology.hl7.org/CodeSystem/v2-0074",
                        "code": category_code,
                        "display": category_display,
                    }
                ]
            }
        ],
        "code": {
            "coding": [
                {
                    "system": "http://loinc.org",
                    "code": loinc_code,
                    "display": loinc_display,
                }
            ],
            "text": loinc_display,
        },
        "subject": {"reference": f"Patient/{patient_id}"},
        "effectiveDateTime": _now_iso(),
        "issued": _now_iso(),
    }

    if result_references:
        resource["result"] = [{"reference": ref} for ref in result_references]
    if conclusion:
        resource["conclusion"] = conclusion
    if note_id:
        resource["extension"] = [
            {
                "url": "http://example.com/fhir/extension/source-note-id",
                "valueString": note_id,
            }
        ]

    _require(resource, "resourceType", "id", "status", "code", "subject", resource_type="DiagnosticReport")
    _require_coding(resource["code"]["coding"], "DiagnosticReport", "code")
    return resource


# ---------------------------------------------------------------------------
# Bundle assembly
# ---------------------------------------------------------------------------

def _wrap_entry(resource: dict, method: str = "POST") -> dict:
    """Wrap a resource dict in a FHIR Bundle entry with transaction request."""
    resource_type = resource.get("resourceType", "Resource")
    return {
        "fullUrl": f"urn:uuid:{resource.get('id', _new_id())}",
        "resource": resource,
        "request": {
            "method": method,
            "url": resource_type,
        },
    }


@dataclass
class BundleContents:
    """Holds all resource lists for building a full FHIR Bundle."""
    patient: dict | None = None
    conditions: list[dict] = field(default_factory=list)
    observations: list[dict] = field(default_factory=list)
    medication_requests: list[dict] = field(default_factory=list)
    procedures: list[dict] = field(default_factory=list)
    diagnostic_reports: list[dict] = field(default_factory=list)


def assemble_bundle(contents: BundleContents, note_id: str = "") -> dict:
    """
    Assemble a FHIR R4 Bundle (type=transaction) from BundleContents.

    All resources are schema-validated before assembly.
    Raises FHIRValidationError if any resource is invalid.
    """
    entries: list[dict] = []

    if contents.patient:
        # Patient uses PUT (upsert by known ID)
        entries.append(_wrap_entry(contents.patient, method="PUT"))

    for resource in contents.conditions:
        entries.append(_wrap_entry(resource))
    for resource in contents.observations:
        entries.append(_wrap_entry(resource))
    for resource in contents.medication_requests:
        entries.append(_wrap_entry(resource))
    for resource in contents.procedures:
        entries.append(_wrap_entry(resource))
    for resource in contents.diagnostic_reports:
        entries.append(_wrap_entry(resource))

    bundle: dict[str, Any] = {
        "resourceType": "Bundle",
        "id": _new_id(),
        "meta": {
            "profile": ["http://hl7.org/fhir/StructureDefinition/Bundle"],
            "lastUpdated": _now_iso(),
        },
        "type": "transaction",
        "timestamp": _now_iso(),
        "total": len(entries),
        "entry": entries,
    }

    if note_id:
        bundle["extension"] = [
            {
                "url": "http://example.com/fhir/extension/source-note-id",
                "valueString": note_id,
            }
        ]

    logger.info(
        "FHIR R4 Bundle assembled for note '%s': "
        "%d Patient, %d Condition, %d Observation, "
        "%d MedicationRequest, %d Procedure, %d DiagnosticReport",
        note_id,
        1 if contents.patient else 0,
        len(contents.conditions),
        len(contents.observations),
        len(contents.medication_requests),
        len(contents.procedures),
        len(contents.diagnostic_reports),
    )
    return bundle


# ---------------------------------------------------------------------------
# Bundle validation
# ---------------------------------------------------------------------------

_REQUIRED_RESOURCE_FIELDS: dict[str, list[str]] = {
    "Patient":           ["id", "gender"],
    "Condition":         ["id", "code", "subject", "clinicalStatus", "verificationStatus"],
    "Observation":       ["id", "status", "code", "subject"],
    "MedicationRequest": ["id", "status", "intent", "medicationCodeableConcept", "subject"],
    "Procedure":         ["id", "status", "code", "subject"],
    "DiagnosticReport":  ["id", "status", "code", "subject"],
    "Bundle":            ["id", "type", "entry"],
}


def validate_r4_bundle(bundle: dict) -> tuple[bool, list[str]]:
    """
    Validate a FHIR R4 Bundle against required-field rules.

    Returns (is_valid, list_of_issues).
    No issues means the bundle conforms to minimum R4 requirements.
    """
    issues: list[str] = []

    if bundle.get("resourceType") != "Bundle":
        issues.append("Missing or incorrect 'resourceType' (expected 'Bundle').")
        return False, issues

    for req_field in _REQUIRED_RESOURCE_FIELDS["Bundle"]:
        if req_field not in bundle:
            issues.append(f"Bundle missing required field '{req_field}'.")

    valid_types = {"document", "message", "transaction", "transaction-response",
                   "batch", "batch-response", "history", "searchset", "collection"}
    if bundle.get("type") not in valid_types:
        issues.append(f"Bundle 'type' is invalid: {bundle.get('type')!r}.")

    entries = bundle.get("entry", [])
    if not isinstance(entries, list):
        issues.append("Bundle 'entry' must be a list.")
        return len(issues) == 0, issues

    for i, entry in enumerate(entries):
        resource = entry.get("resource", {})
        if not resource:
            issues.append(f"Entry[{i}] has no 'resource'.")
            continue

        rt = resource.get("resourceType", "")
        if not rt:
            issues.append(f"Entry[{i}] resource missing 'resourceType'.")
            continue

        required_fields = _REQUIRED_RESOURCE_FIELDS.get(rt, ["id"])
        for rf in required_fields:
            if not resource.get(rf):
                issues.append(f"Entry[{i}] {rt} missing required field '{rf}'.")

        # Coding system/code checks for coded fields
        for coded_field in ("code", "clinicalStatus", "verificationStatus"):
            cf = resource.get(coded_field)
            if isinstance(cf, dict):
                coding_list = cf.get("coding", [])
                for j, coding in enumerate(coding_list):
                    if not coding.get("system"):
                        issues.append(
                            f"Entry[{i}] {rt}.{coded_field}.coding[{j}] missing 'system'."
                        )
                    if not coding.get("code"):
                        issues.append(
                            f"Entry[{i}] {rt}.{coded_field}.coding[{j}] missing 'code'."
                        )

    return len(issues) == 0, issues


# ---------------------------------------------------------------------------
# Convenience: build full bundle from extracted pipeline data
# ---------------------------------------------------------------------------

def build_full_bundle(
    note_id: str,
    patient_id: str,
    icd_codes: list[dict] | None = None,
    medications: list[dict] | None = None,
    observations: list[dict] | None = None,
    procedures: list[dict] | None = None,
    diagnostic_reports: list[dict] | None = None,
    patient_gender: str = "unknown",
) -> dict:
    """
    High-level convenience function: build a full validated FHIR R4 Bundle.

    Parameters
    ----------
    note_id : str
        Source note ID for extension tracing.
    patient_id : str
        De-identified patient identifier.
    icd_codes : list[dict]
        Each dict: {code, description, confidence}.
    medications : list[dict]
        Each dict: {name, dose, frequency, route, confidence}.
    observations : list[dict]
        Each dict: {loinc_code, loinc_display, value_quantity, value_unit}.
    procedures : list[dict]
        Each dict: {snomed_code, snomed_display, status}.
    diagnostic_reports : list[dict]
        Each dict: {loinc_code, loinc_display, conclusion}.
    patient_gender : str
        "male" | "female" | "other" | "unknown".

    Returns
    -------
    dict
        Validated FHIR R4 Bundle (JSON-serializable).

    Raises
    ------
    FHIRValidationError
        If any resource fails R4 schema validation.
    """
    contents = BundleContents()

    contents.patient = build_patient(patient_id, gender=patient_gender)

    for icd in (icd_codes or []):
        contents.conditions.append(
            build_condition(
                patient_id,
                icd_code=icd["code"],
                icd_display=icd.get("description", icd["code"]),
                confidence=icd.get("confidence", 1.0),
                note_id=note_id,
            )
        )

    for med in (medications or []):
        dose_str: str = str(med.get("dose", ""))
        dose_val_match = re.search(r"(\d+(?:\.\d+)?)", dose_str)
        dose_val = float(dose_val_match.group(1)) if dose_val_match else None
        dose_unit_match = re.search(r"[a-zA-Z/]+", dose_str)
        dose_unit = dose_unit_match.group() if dose_unit_match else "mg"

        contents.medication_requests.append(
            build_medication_request(
                patient_id,
                medication_name=med["name"],
                dose_value=dose_val,
                dose_unit=dose_unit,
                route_display=med.get("route", "oral"),
                frequency_text=med.get("frequency", "unknown"),
                confidence=med.get("confidence", 1.0),
                note_id=note_id,
            )
        )

    for obs in (observations or []):
        contents.observations.append(
            build_observation(
                patient_id,
                loinc_code=obs["loinc_code"],
                loinc_display=obs.get("loinc_display", obs["loinc_code"]),
                value_quantity=obs.get("value_quantity"),
                value_unit=obs.get("value_unit", ""),
                note_id=note_id,
            )
        )

    for proc in (procedures or []):
        contents.procedures.append(
            build_procedure(
                patient_id,
                snomed_code=proc["snomed_code"],
                snomed_display=proc.get("snomed_display", proc["snomed_code"]),
                status=proc.get("status", "completed"),
                confidence=proc.get("confidence", 1.0),
                note_id=note_id,
            )
        )

    for dr in (diagnostic_reports or []):
        contents.diagnostic_reports.append(
            build_diagnostic_report(
                patient_id,
                loinc_code=dr["loinc_code"],
                loinc_display=dr.get("loinc_display", dr["loinc_code"]),
                conclusion=dr.get("conclusion", ""),
                note_id=note_id,
            )
        )

    bundle = assemble_bundle(contents, note_id=note_id)

    is_valid, issues = validate_r4_bundle(bundle)
    if not is_valid:
        raise FHIRValidationError(
            f"FHIR R4 Bundle validation failed with {len(issues)} issue(s):\n"
            + "\n".join(f"  - {i}" for i in issues)
        )

    return bundle


def export_bundle_json(bundle: dict, indent: int = 2) -> str:
    """Serialize a validated FHIR R4 Bundle to pretty-printed JSON."""
    return json.dumps(bundle, indent=indent, ensure_ascii=False)
