"""
FHIR Mapper — Convert extracted entities to FHIR R4 Bundle.

Produces:
  - FHIR Condition resources for ICD-10 codes.
  - FHIR MedicationStatement resources for medications.
  - Wrapped in a FHIR Bundle (type = "collection").

The output conforms to HL7 FHIR R4 (https://hl7.org/fhir/R4/).
We use the `fhir.resources` library for validated model construction where
available and fall back to plain dict-based construction otherwise.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from pipeline.entity_extractor import ICDCode, Medication

logger = logging.getLogger(__name__)

try:
    from fhir.resources.bundle import Bundle, BundleEntry
    from fhir.resources.condition import Condition
    from fhir.resources.codeableconcept import CodeableConcept
    from fhir.resources.coding import Coding
    from fhir.resources.medicationstatement import MedicationStatement
    from fhir.resources.dosage import Dosage
    from fhir.resources.meta import Meta
    from fhir.resources.reference import Reference
    FHIR_AVAILABLE = True
except ImportError:
    FHIR_AVAILABLE = False
    logger.warning(
        "fhir.resources not installed; using plain-dict FHIR construction."
    )

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SNOMED_CLINICAL_STATUS = {
    "active": "55561003",
    "resolved": "73425007",
}

SNOMED_VERIFICATION = {
    "confirmed": "59156000",
    "provisional": "2470005",
}

FHIR_DATE = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
PATIENT_REF = "Patient/patient-placeholder"   # replaced by real EHR patient ID at integration time


def _new_id() -> str:
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# Dict-based FHIR builders (always available)
# ---------------------------------------------------------------------------

def _condition_dict(icd: ICDCode, note_id: str) -> dict:
    return {
        "resourceType": "Condition",
        "id": _new_id(),
        "meta": {
            "profile": ["http://hl7.org/fhir/StructureDefinition/Condition"],
            "lastUpdated": FHIR_DATE,
        },
        "clinicalStatus": {
            "coding": [{
                "system": "http://terminology.hl7.org/CodeSystem/condition-clinical",
                "code": "active",
                "display": "Active",
            }],
        },
        "verificationStatus": {
            "coding": [{
                "system": "http://terminology.hl7.org/CodeSystem/condition-ver-status",
                "code": "confirmed",
                "display": "Confirmed",
            }],
        },
        "code": {
            "coding": [{
                "system": "http://hl7.org/fhir/sid/icd-10-cm",
                "code": icd.code,
                "display": icd.description,
            }],
            "text": icd.description,
        },
        "subject": {"reference": PATIENT_REF},
        "recordedDate": FHIR_DATE,
        "extension": [
            {
                "url": "http://example.com/fhir/extension/extraction-confidence",
                "valueDecimal": icd.confidence,
            },
            {
                "url": "http://example.com/fhir/extension/source-note-id",
                "valueString": note_id,
            },
        ],
    }


def _dosage_timing(frequency: str) -> dict:
    freq_map = {
        "once daily": {"frequency": 1, "period": 1, "periodUnit": "d"},
        "twice daily": {"frequency": 2, "period": 1, "periodUnit": "d"},
        "three times daily": {"frequency": 3, "period": 1, "periodUnit": "d"},
        "four times daily": {"frequency": 4, "period": 1, "periodUnit": "d"},
        "every 4 hours": {"frequency": 1, "period": 4, "periodUnit": "h"},
        "every 6 hours": {"frequency": 1, "period": 6, "periodUnit": "h"},
        "every 8 hours": {"frequency": 1, "period": 8, "periodUnit": "h"},
        "every 12 hours": {"frequency": 1, "period": 12, "periodUnit": "h"},
        "at bedtime": {"frequency": 1, "period": 1, "periodUnit": "d"},
        "as needed": {"frequency": 1, "period": 1, "periodUnit": "d"},
        "every 2 weeks": {"frequency": 1, "period": 14, "periodUnit": "d"},
        "weekly": {"frequency": 1, "period": 7, "periodUnit": "d"},
        "immediately": {"frequency": 1, "period": 1, "periodUnit": "d"},
    }
    timing = freq_map.get(frequency, {"frequency": 1, "period": 1, "periodUnit": "d"})
    return {"repeat": timing}


def _medication_statement_dict(med: Medication, note_id: str) -> dict:
    dosage_entry: dict[str, Any] = {
        "text": f"{med.name} {med.dose} {med.route} {med.frequency}",
        "timing": _dosage_timing(med.frequency),
        "route": {
            "coding": [{
                "system": "http://snomed.info/sct",
                "code": "26643006",
                "display": med.route,
            }],
            "text": med.route,
        },
        "doseAndRate": [{
            "type": {
                "coding": [{
                    "system": "http://terminology.hl7.org/CodeSystem/dose-rate-type",
                    "code": "ordered",
                }],
            },
            "doseQuantity": {
                "value": _parse_dose_value(med.dose),
                "unit": _parse_dose_unit(med.dose),
                "system": "http://unitsofmeasure.org",
            },
        }],
    }

    return {
        "resourceType": "MedicationStatement",
        "id": _new_id(),
        "meta": {
            "profile": ["http://hl7.org/fhir/StructureDefinition/MedicationStatement"],
            "lastUpdated": FHIR_DATE,
        },
        "status": "active",
        "medicationCodeableConcept": {
            "coding": [{
                "system": "http://www.nlm.nih.gov/research/umls/rxnorm",
                "display": med.name,
            }],
            "text": med.name,
        },
        "subject": {"reference": PATIENT_REF},
        "effectiveDateTime": FHIR_DATE,
        "dosage": [dosage_entry],
        "extension": [
            {
                "url": "http://example.com/fhir/extension/extraction-confidence",
                "valueDecimal": med.confidence,
            },
            {
                "url": "http://example.com/fhir/extension/source-note-id",
                "valueString": note_id,
            },
        ],
    }


def _parse_dose_value(dose: str) -> float:
    import re
    match = re.search(r'(\d+(?:\.\d+)?)', dose)
    return float(match.group(1)) if match else 0.0


def _parse_dose_unit(dose: str) -> str:
    import re
    match = re.search(r'\d+(?:\.\d+)?\s*([a-zA-Z/]+)', dose)
    return match.group(1) if match else "mg"


def _bundle_dict(entries: list[dict], note_id: str) -> dict:
    return {
        "resourceType": "Bundle",
        "id": _new_id(),
        "meta": {
            "lastUpdated": FHIR_DATE,
            "profile": ["http://hl7.org/fhir/StructureDefinition/Bundle"],
        },
        "type": "collection",
        "timestamp": FHIR_DATE,
        "total": len(entries),
        "extension": [{
            "url": "http://example.com/fhir/extension/source-note-id",
            "valueString": note_id,
        }],
        "entry": [
            {"fullUrl": f"urn:uuid:{_new_id()}", "resource": r}
            for r in entries
        ],
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def map_to_fhir(
    note_id: str,
    icd_codes: list[ICDCode],
    medications: list[Medication],
) -> dict:
    """
    Produce a FHIR R4 Bundle dict from extracted entities.

    Parameters
    ----------
    note_id : str
        Source note identifier (used in extension fields).
    icd_codes : list[ICDCode]
        ICD-10 codes from entity_extractor.
    medications : list[Medication]
        Medications from entity_extractor.

    Returns
    -------
    dict
        FHIR R4 Bundle as a plain Python dict (JSON-serializable).
    """
    resources: list[dict] = []

    for icd in icd_codes:
        resources.append(_condition_dict(icd, note_id))

    for med in medications:
        resources.append(_medication_statement_dict(med, note_id))

    bundle = _bundle_dict(resources, note_id)
    logger.info(
        "FHIR Bundle created for note %s: %d Condition(s), %d MedicationStatement(s)",
        note_id, len(icd_codes), len(medications),
    )
    return bundle


def bundle_to_json(bundle: dict, indent: int = 2) -> str:
    """Serialize a FHIR Bundle dict to a pretty-printed JSON string."""
    return json.dumps(bundle, indent=indent)


def validate_bundle(bundle: dict) -> tuple[bool, list[str]]:
    """
    Lightweight validation of bundle structure.

    Returns (is_valid, list_of_issues).
    """
    issues: list[str] = []

    if bundle.get("resourceType") != "Bundle":
        issues.append("Missing or incorrect resourceType (expected 'Bundle').")

    if bundle.get("type") not in ("collection", "searchset", "transaction", "batch"):
        issues.append(f"Unexpected bundle type: {bundle.get('type')}")

    entries = bundle.get("entry", [])
    for i, entry in enumerate(entries):
        resource = entry.get("resource", {})
        if not resource.get("resourceType"):
            issues.append(f"Entry {i} missing resourceType.")
        if not resource.get("id"):
            issues.append(f"Entry {i} missing id.")

    return len(issues) == 0, issues
