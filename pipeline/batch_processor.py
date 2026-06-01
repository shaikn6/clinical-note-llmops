"""
Batch Processor — Process 1 000 synthetic clinical notes end-to-end.

Pipeline per note:
  1. PII scrub (Presidio / regex fallback)
  2. NER + ICD-10 extraction (spaCy + mock/OpenAI LLM)
  3. FHIR R4 Bundle generation (full 6-resource-type bundle)
  4. (Optional) audit log write

Features:
  - Parallel processing via concurrent.futures.ThreadPoolExecutor
  - Live progress bar via tqdm
  - Throughput benchmark (notes / second)
  - Deterministic synthetic note generation so the batch is reproducible
  - Graceful error capture per note — one failure never aborts the batch

Usage::

    from pipeline.batch_processor import run_batch

    result = run_batch(n_notes=1000, max_workers=8)
    print(result.summary())
"""

from __future__ import annotations

import logging
import random
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

try:
    from tqdm import tqdm
    TQDM_AVAILABLE = True
except ImportError:
    TQDM_AVAILABLE = False
    logger.warning("tqdm not installed; progress bar will be text-only.")

from pipeline.pii_scrubber import scrub_note  # noqa: E402
from pipeline.entity_extractor import extract_entities  # noqa: E402
from fhir.fhir_r4_builder import build_full_bundle  # noqa: E402

# ---------------------------------------------------------------------------
# Synthetic note generation
# ---------------------------------------------------------------------------

_FIRST_NAMES = [
    "James", "Mary", "Robert", "Patricia", "John", "Jennifer", "Michael",
    "Linda", "William", "Barbara", "David", "Susan", "Richard", "Jessica",
    "Joseph", "Sarah", "Thomas", "Karen", "Charles", "Lisa",
]
_LAST_NAMES = [
    "Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller",
    "Davis", "Wilson", "Taylor", "Anderson", "Thomas", "Jackson", "White",
    "Harris", "Martin", "Thompson", "Young", "Allen", "Hall",
]
_NOTE_TEMPLATES = [
    (
        "Patient {name}, DOB {dob}, MRN {mrn}. "
        "Chief complaint: chest pain radiating to left arm. "
        "History: hypertension, hyperlipidemia. "
        "Medications: metoprolol 25mg BID PO, aspirin 325mg QD PO, "
        "atorvastatin 40mg QD PO. "
        "Assessment: Acute MI (ICD-10: I21.9). Hypertension (I10). "
        "Plan: admit, cardiology consult. Contact {email}."
    ),
    (
        "Patient {name}, MRN {mrn}, DOB {dob}. "
        "Presenting with fatigue, polyuria. "
        "Medications: metformin 1000mg BID PO, empagliflozin 10mg QD PO. "
        "Labs: HbA1c elevated. "
        "Assessment: Type 2 diabetes mellitus with hyperglycemia (E11.65). "
        "Phone: {phone}."
    ),
    (
        "Name: {name}, DOB: {dob}, MRN: {mrn}. "
        "Complaint: productive cough, fever 38.5C. "
        "Medications: azithromycin 500mg QD PO, guaifenesin 400mg TID PO. "
        "Assessment: Pneumonia (J18.9). Upper respiratory infection (J06.9). "
        "Email: {email}. Follow-up in 7 days."
    ),
    (
        "Patient {name} (MRN {mrn}), DOB {dob}. "
        "Chronic low back pain, lumbar radiculopathy. "
        "Medications: naproxen 500mg BID PO, cyclobenzaprine 10mg TID PO, "
        "gabapentin 300mg TID PO. "
        "Assessment: Lumbar disc degeneration (M51.16). Low back pain (M54.5). "
        "Phone: {phone}."
    ),
    (
        "Psychiatry Note. Patient: {name}, MRN {mrn}, DOB {dob}. "
        "Symptoms: low mood, anhedonia, insomnia for 6 weeks. "
        "Medications: sertraline 50mg QD PO. "
        "Assessment: Major depressive disorder (F32.2). "
        "GAD (F41.1). Contact via email: {email}."
    ),
    (
        "{name}, MRN {mrn}. DOB {dob}. "
        "Presenting with severe RLQ pain, rebound tenderness. "
        "Assessment: Appendicitis (K37). "
        "Plan: urgent surgical consult. "
        "Medications: cefazolin 1g IV Q8H, ketorolac 30mg IV PRN. "
        "Phone: {phone}."
    ),
    (
        "Patient: {name}, DOB {dob}, MRN: {mrn}. "
        "Rheumatoid arthritis with flare. Joints: wrists, MCPs bilateral. "
        "Medications: methotrexate 15mg QWK PO, folic acid 1mg QD PO, "
        "adalimumab 40mg Q2W SC. "
        "Assessment: Rheumatoid arthritis (M05.79). Contact: {email}."
    ),
    (
        "Asthma follow-up. {name} (MRN {mrn}), DOB {dob}. "
        "Medications: albuterol 2.5mg Q4H inhaled PRN, "
        "fluticasone/salmeterol 250/50mcg BID MDI, ipratropium 500mcg Q6H inhaled. "
        "Assessment: Moderate persistent asthma with exacerbation (J45.41). "
        "Phone: {phone}."
    ),
]

_DEPARTMENTS = [
    "cardiology", "endocrinology", "pulmonology", "orthopedics",
    "psychiatry", "surgery", "rheumatology", "pulmonology",
]

_NOTE_TYPES = [
    "discharge_summary", "outpatient_visit", "emergency_note",
    "progress_note", "psychiatry_note", "preventive_visit",
    "rheumatology_note", "clinical_note",
]


def _rand_dob(rng: random.Random) -> str:
    year = rng.randint(1940, 1995)
    month = rng.randint(1, 12)
    day = rng.randint(1, 28)
    return f"{month:02d}/{day:02d}/{year}"


def _rand_mrn(rng: random.Random) -> str:
    return str(rng.randint(100000, 9999999))


def _rand_phone(rng: random.Random) -> str:
    return f"{rng.randint(200,999)}-{rng.randint(200,999)}-{rng.randint(1000,9999)}"


def _rand_email(first: str, last: str, rng: random.Random) -> str:
    domains = ["hospital.org", "clinic.com", "health.net", "medcenter.edu"]
    return f"{first[0].lower()}{last.lower()}{rng.randint(1,99)}@{rng.choice(domains)}"


def generate_synthetic_note(note_index: int, seed: int | None = None) -> dict:
    """
    Generate a deterministic synthetic clinical note with embedded PHI.

    Parameters
    ----------
    note_index : int
        Index used for reproducible RNG seeding.
    seed : int | None
        Optional global seed offset.

    Returns
    -------
    dict
        Keys: note_id, note_text, note_type, department, template_index.
    """
    rng = random.Random((seed or 42) + note_index)
    template_idx = note_index % len(_NOTE_TEMPLATES)
    template = _NOTE_TEMPLATES[template_idx]

    first = rng.choice(_FIRST_NAMES)
    last = rng.choice(_LAST_NAMES)
    name = f"{first} {last}"
    dob = _rand_dob(rng)
    mrn = _rand_mrn(rng)
    phone = _rand_phone(rng)
    email = _rand_email(first, last, rng)

    note_text = template.format(
        name=name, dob=dob, mrn=mrn, phone=phone, email=email
    )

    return {
        "note_id": f"BATCH-{note_index:05d}",
        "note_text": note_text,
        "note_type": _NOTE_TYPES[note_index % len(_NOTE_TYPES)],
        "department": _DEPARTMENTS[template_idx],
        "template_index": template_idx,
    }


# ---------------------------------------------------------------------------
# Single-note processing
# ---------------------------------------------------------------------------

def _process_one_note(note: dict) -> dict:
    """
    Run the full pipeline on a single synthetic note.

    Returns a result dict with status, timings, and counts.
    Never raises — errors are captured in result["error"].
    """
    note_id = note["note_id"]
    t_start = time.perf_counter()
    result: dict[str, Any] = {
        "note_id": note_id,
        "note_type": note.get("note_type", "unknown"),
        "status": "success",
        "error": None,
        "phi_count": 0,
        "icd_count": 0,
        "med_count": 0,
        "fhir_resource_count": 0,
        "scrubbing_mode": "unknown",
        "extraction_mode": "unknown",
        "elapsed_ms": 0.0,
    }

    try:
        # Step 1: PII scrub
        scrub_result = scrub_note(note["note_text"])
        result["phi_count"] = scrub_result.phi_count
        result["scrubbing_mode"] = scrub_result.scrubbing_mode

        # Step 2: NER + ICD-10 extraction
        extraction = extract_entities(scrub_result.scrubbed_text)
        result["icd_count"] = len(extraction.icd_codes)
        result["med_count"] = len(extraction.medications)
        result["extraction_mode"] = extraction.extraction_mode

        # Step 3: FHIR R4 bundle
        icd_dicts = [
            {"code": c.code, "description": c.description, "confidence": c.confidence}
            for c in extraction.icd_codes
        ]
        med_dicts = [
            {
                "name": m.name, "dose": m.dose,
                "frequency": m.frequency, "route": m.route,
                "confidence": m.confidence,
            }
            for m in extraction.medications
        ]
        patient_id = f"patient-{note_id.lower()}"
        bundle = build_full_bundle(
            note_id=note_id,
            patient_id=patient_id,
            icd_codes=icd_dicts,
            medications=med_dicts,
        )
        result["fhir_resource_count"] = bundle.get("total", 0)

    except Exception as exc:
        result["status"] = "error"
        result["error"] = str(exc)
        logger.warning("Batch note %s failed: %s", note_id, exc)

    result["elapsed_ms"] = (time.perf_counter() - t_start) * 1000
    return result


# ---------------------------------------------------------------------------
# Batch result container
# ---------------------------------------------------------------------------

@dataclass
class BatchResult:
    """Aggregated result from a full batch run."""
    n_requested: int
    n_success: int
    n_error: int
    total_elapsed_sec: float
    notes_per_second: float
    results: list[dict] = field(default_factory=list)
    max_workers: int = 1

    def summary(self) -> str:
        lines = [
            "=" * 60,
            "  Batch Processing Summary",
            "=" * 60,
            f"  Notes requested  : {self.n_requested:,}",
            f"  Succeeded        : {self.n_success:,}",
            f"  Errors           : {self.n_error:,}",
            f"  Workers          : {self.max_workers}",
            f"  Total time       : {self.total_elapsed_sec:.2f}s",
            f"  Throughput       : {self.notes_per_second:.1f} notes/sec",
            "=" * 60,
        ]
        if self.results:
            avg_phi = sum(r.get("phi_count", 0) for r in self.results) / len(self.results)
            avg_icd = sum(r.get("icd_count", 0) for r in self.results) / len(self.results)
            avg_med = sum(r.get("med_count", 0) for r in self.results) / len(self.results)
            lines += [
                f"  Avg PHI/note     : {avg_phi:.1f}",
                f"  Avg ICD codes    : {avg_icd:.1f}",
                f"  Avg medications  : {avg_med:.1f}",
                "=" * 60,
            ]
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "n_requested": self.n_requested,
            "n_success": self.n_success,
            "n_error": self.n_error,
            "total_elapsed_sec": round(self.total_elapsed_sec, 3),
            "notes_per_second": round(self.notes_per_second, 2),
            "max_workers": self.max_workers,
        }


# ---------------------------------------------------------------------------
# Main batch runner
# ---------------------------------------------------------------------------

def run_batch(
    n_notes: int = 1000,
    max_workers: int = 4,
    seed: int = 42,
    show_progress: bool = True,
) -> BatchResult:
    """
    Process ``n_notes`` synthetic clinical notes in parallel.

    Parameters
    ----------
    n_notes : int
        Number of notes to generate and process (default 1 000).
    max_workers : int
        Thread-pool concurrency (default 4).
    seed : int
        RNG seed for reproducible synthetic notes.
    show_progress : bool
        Show tqdm progress bar if available.

    Returns
    -------
    BatchResult
        Aggregated statistics and per-note results.
    """
    logger.info("Generating %d synthetic notes (seed=%d)…", n_notes, seed)
    notes = [generate_synthetic_note(i, seed=seed) for i in range(n_notes)]

    results: list[dict] = [None] * n_notes  # type: ignore[list-item]

    t_batch_start = time.perf_counter()

    if TQDM_AVAILABLE and show_progress:
        pbar = tqdm(total=n_notes, desc="Processing notes", unit="note", dynamic_ncols=True)
    else:
        pbar = None

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_idx = {
            executor.submit(_process_one_note, notes[i]): i
            for i in range(n_notes)
        }
        for future in as_completed(future_to_idx):
            idx = future_to_idx[future]
            try:
                results[idx] = future.result()
            except Exception as exc:
                results[idx] = {
                    "note_id": notes[idx]["note_id"],
                    "status": "error",
                    "error": str(exc),
                    "phi_count": 0,
                    "icd_count": 0,
                    "med_count": 0,
                    "fhir_resource_count": 0,
                    "elapsed_ms": 0.0,
                }
            if pbar is not None:
                pbar.update(1)

    if pbar is not None:
        pbar.close()

    total_elapsed = time.perf_counter() - t_batch_start
    n_success = sum(1 for r in results if r and r.get("status") == "success")
    n_error = n_notes - n_success
    throughput = n_notes / total_elapsed if total_elapsed > 0 else 0.0

    batch_result = BatchResult(
        n_requested=n_notes,
        n_success=n_success,
        n_error=n_error,
        total_elapsed_sec=total_elapsed,
        notes_per_second=throughput,
        results=results,
        max_workers=max_workers,
    )

    logger.info(batch_result.summary())
    return batch_result


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 1000
    workers = int(sys.argv[2]) if len(sys.argv) > 2 else 4
    result = run_batch(n_notes=n, max_workers=workers)
    print(result.summary())
