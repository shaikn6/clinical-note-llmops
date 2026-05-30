# Changelog

## v2.0.0 — 2026-05-30

### What's New

- Full FHIR R4 resource generation: 6 resource types, schema-validated
  (`Patient`, `Condition`, `Observation`, `MedicationRequest`, `Procedure`, `DiagnosticReport`)
  assembled into a `transaction` Bundle; validation enforces required fields,
  data types, and coding system/code presence.
- Batch processing: 1 000 synthetic notes end-to-end with `ThreadPoolExecutor`
  parallelism; live `tqdm` progress bar; throughput benchmark (notes/sec).
- De-identification benchmarker: precision / recall / F1 per PII type
  (NAME, DATE, PHONE, EMAIL, MRN) on 100 annotated synthetic notes;
  benchmark report PDF via matplotlib.
- HIPAA audit logger: structured compliance log (SQLite-backed) recording
  every PHI access and redaction with `operator_id`, `action`, `record_id`,
  `phi_types`, `timestamp`; PDF report (who accessed what, when).
- V2 Streamlit dashboard (`dashboard/app_v2.py`) with four tabs:
  Single Note Pipeline (V1+), Batch Processor, De-id Benchmark, Audit Log Explorer.

### Improvements

- Pipeline throughput improved via parallel batch mode (`concurrent.futures`).
- FHIR output is now a full R4 `transaction` Bundle covering all 6 resource
  types (was partial `collection` with only Condition + MedicationStatement).
- HIPAA audit logger extracted into dedicated `audit/` package with
  `HIPAAAuditLogger` class; module-level singleton for backward compatibility.

### Under the Hood

- +35 tests covering FHIR R4 schema validation, individual resource builders,
  batch consistency, de-id benchmark metrics, and HIPAA audit logger CRUD.
- New packages: `fhir/`, `deidentification/`, `audit/`.
- New requirements: `tqdm` (progress bar).

---

## v1.0.0 — 2026-05-30

- Presidio PII scrubbing (regex fallback) → spaCy NER → ICD-10 extraction
  (mock LLM / OpenAI) → partial FHIR R4 Bundle output
  (Condition + MedicationStatement resources).
- Human-in-the-loop review queue (SQLite + Streamlit).
- Pipeline metrics dashboard (Plotly charts).
- Audit logging (SQLite + simulated S3).
- FastAPI REST endpoint (`/process`).
- Docker + docker-compose configuration.
