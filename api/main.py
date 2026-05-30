"""
FastAPI REST API — Clinical Note LLMOps

Endpoints:
  POST /api/process-note         Process a raw clinical note through the full pipeline
  GET  /api/review-queue         List pending review queue items
  POST /api/review-queue/{id}/approve  Approve a review item
  POST /api/review-queue/{id}/reject   Reject a review item
  GET  /api/audit-log            Retrieve audit log entries
  GET  /api/fhir/{note_id}       Retrieve the FHIR Bundle for a processed note
  GET  /health                   Health check
  GET  /                         Serve HTML frontend

SECURITY: All PHI-processing endpoints require a valid API key supplied via the
X-API-Key header.  Set API_KEY env var (min 32 chars) before starting.
CORS is restricted to explicitly trusted origins via ALLOWED_ORIGINS env var.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Security
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.security.api_key import APIKeyHeader
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from pipeline.pii_scrubber import scrub_note
from pipeline.entity_extractor import extract_entities
from pipeline.confidence_scorer import score_extractions
from pipeline.fhir_mapper import map_to_fhir, validate_bundle
from pipeline.review_queue import (
    init_db,
    populate_review_queue,
    get_pending_items,
    get_all_items,
    approve_item,
    reject_item,
    queue_stats,
)
from pipeline.audit_logger import init_audit_db, log_operation, get_audit_log

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Security configuration
# ---------------------------------------------------------------------------

# API key: must be set in the environment.  Min 32 chars enforced at startup.
_API_KEY: str = os.getenv("API_KEY", "")

def _validate_api_key_at_startup() -> None:
    if not _API_KEY or len(_API_KEY) < 32:
        logger.warning(
            "API_KEY env var is not set or is shorter than 32 characters. "
            "All /api/* endpoints will reject requests until a valid key is configured."
        )

_validate_api_key_at_startup()

API_KEY_HEADER = APIKeyHeader(name="X-API-Key", auto_error=False)


def require_api_key(api_key: str | None = Security(API_KEY_HEADER)) -> str:
    """
    FastAPI dependency: reject requests that do not present the correct API key.
    Uses constant-time comparison to prevent timing attacks.
    """
    if not _API_KEY or len(_API_KEY) < 32:
        raise HTTPException(
            status_code=503,
            detail="Service unavailable: API key not configured on server.",
        )
    if not api_key or not secrets.compare_digest(api_key, _API_KEY):
        raise HTTPException(
            status_code=401,
            detail="Unauthorized: valid X-API-Key header required.",
        )
    return api_key


# ---------------------------------------------------------------------------
# CORS: restrict to explicitly configured trusted origins only
# ---------------------------------------------------------------------------

# Comma-separated list of allowed origins.  Defaults to localhost for dev.
# Override in production: ALLOWED_ORIGINS=https://your-app.example.com
_raw_origins = os.getenv("ALLOWED_ORIGINS", "http://localhost:3000,http://localhost:8501")
ALLOWED_ORIGINS: list[str] = [o.strip() for o in _raw_origins.split(",") if o.strip()]

# In-memory FHIR cache (note_id → bundle dict)
FHIR_CACHE: dict[str, dict] = {}

# ---------------------------------------------------------------------------
# Security headers middleware
# ---------------------------------------------------------------------------

class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add HIPAA/OWASP-recommended security headers to every response."""

    async def dispatch(self, request: Request, call_next) -> Response:
        response: Response = await call_next(request)
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains; preload"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Cache-Control"] = "no-store"
        return response


# ---------------------------------------------------------------------------
# App lifecycle
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    init_audit_db()
    logger.info("Database tables ready.")
    yield


app = FastAPI(
    title="Clinical Note LLMOps API",
    description=(
        "HIPAA-compliant LLMOps pipeline: PII scrubbing → ICD-10/medication extraction "
        "→ FHIR R4 output → S3 audit trail. Human-in-the-loop review queue."
    ),
    version="1.1.0",
    lifespan=lifespan,
)

# Security headers on all responses
app.add_middleware(SecurityHeadersMiddleware)

# CORS: locked to trusted origins only — never wildcard in a PHI-handling service
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "X-API-Key"],
)

# Serve frontend static files if the directory exists
FRONTEND_DIR = Path(__file__).parent.parent / "frontend"
if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")

# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class ProcessNoteRequest(BaseModel):
    note_id: str
    note_text: str
    note_type: str = "clinical_note"
    department: str = "General"
    user_id: str = "system"


class ProcessNoteResponse(BaseModel):
    note_id: str
    scrubbed_text: str
    phi_count: int
    phi_types: dict
    icd_codes: list[dict]
    medications: list[dict]
    scoring_summary: dict
    fhir_bundle: dict
    fhir_valid: bool
    fhir_issues: list[str]
    review_items_enqueued: int
    extraction_mode: str
    audit_event_id: str


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/health")
async def health_check():
    # Health endpoint is intentionally unauthenticated (load-balancer probes).
    # It does NOT reveal configuration details that could aid attackers.
    return {"status": "healthy", "version": "1.1.0"}


@app.get("/", response_class=HTMLResponse)
async def serve_frontend():
    index_path = FRONTEND_DIR / "index.html"
    if index_path.exists():
        return FileResponse(str(index_path))
    return HTMLResponse("<h1>Clinical Note LLMOps API</h1><p>See <a href='/docs'>API docs</a>.</p>")


@app.post("/api/process-note", response_model=ProcessNoteResponse, dependencies=[Depends(require_api_key)])
async def process_note(request: ProcessNoteRequest):
    """
    Full pipeline:
    1. Scrub PII/PHI (presidio/regex)
    2. Extract ICD-10 codes + medications (spaCy + LLM/mock)
    3. Score confidence, flag low-confidence entities
    4. Map to FHIR R4 Bundle
    5. Enqueue low-confidence entities for human review
    6. Log operation to audit trail (SQLite + S3)

    Requires: X-API-Key header with valid API key.
    """
    note_id = request.note_id
    # SECURITY: log note_id only — NEVER log raw note_text which contains PHI
    logger.info("Processing note id=%s user=%s", note_id, request.user_id)

    # Step 1: PII scrubbing — MUST happen before any LLM call
    try:
        scrub_result = scrub_note(request.note_text)
        # SECURITY: clear raw PHI from the result object immediately after
        # audit logging is complete.  original_text is never passed downstream.
        log_operation(
            note_id=note_id,
            operation="pii_scrubbing",
            user_id=request.user_id,
            phi_types_detected=list(scrub_result.phi_types.keys()),
            phi_count=scrub_result.phi_count,
            status="success",
        )
        scrub_result.clear_original()
    except Exception as exc:
        # SECURITY: log the error internally but return a generic message to
        # the caller — exception strings from the scrubber may echo fragments
        # of the input text, which could contain PHI.
        logger.error("PII scrubbing error for note_id=%s: %s", note_id, exc)
        log_operation(note_id=note_id, operation="pii_scrubbing", status="error",
                      error_message=type(exc).__name__)
        raise HTTPException(status_code=500, detail="PII scrubbing failed. Check server logs.")

    # Step 2: Entity extraction (scrubbed text only!)
    try:
        extraction = extract_entities(scrub_result.scrubbed_text)
        log_operation(
            note_id=note_id,
            operation="entity_extraction",
            user_id=request.user_id,
            extraction_mode=extraction.extraction_mode,
            entity_count=len(extraction.icd_codes) + len(extraction.medications),
            status="success",
        )
    except Exception as exc:
        logger.error("Entity extraction error for note_id=%s: %s", note_id, exc)
        log_operation(note_id=note_id, operation="entity_extraction", status="error",
                      error_message=type(exc).__name__)
        raise HTTPException(status_code=500, detail="Entity extraction failed. Check server logs.")

    # Step 3: Confidence scoring
    scoring = score_extractions(extraction)

    # Step 4: FHIR mapping (only medium/high confidence entities)
    high_med_icds = [
        icd for icd in extraction.icd_codes if icd.confidence >= 0.60
    ]
    high_med_meds = [
        med for med in extraction.medications if med.confidence >= 0.60
    ]
    bundle = map_to_fhir(note_id, high_med_icds, high_med_meds)
    FHIR_CACHE[note_id] = bundle
    fhir_valid, fhir_issues = validate_bundle(bundle)

    # Step 5: Enqueue low-confidence for human review
    review_count = populate_review_queue(note_id, scoring)

    log_operation(
        note_id=note_id,
        operation="fhir_mapping",
        user_id=request.user_id,
        entity_count=len(bundle.get("entry", [])),
        status="success",
        extra={"fhir_valid": fhir_valid, "review_items": review_count},
    )

    audit_event_id = log_operation(
        note_id=note_id,
        operation="pipeline_complete",
        user_id=request.user_id,
        phi_count=scrub_result.phi_count,
        entity_count=len(extraction.icd_codes) + len(extraction.medications),
        status="success",
    )

    return ProcessNoteResponse(
        note_id=note_id,
        scrubbed_text=scrub_result.scrubbed_text,
        phi_count=scrub_result.phi_count,
        phi_types=scrub_result.phi_types,
        icd_codes=[vars(c) for c in extraction.icd_codes],
        medications=[vars(m) for m in extraction.medications],
        scoring_summary=scoring.to_dict(),
        fhir_bundle=bundle,
        fhir_valid=fhir_valid,
        fhir_issues=fhir_issues,
        review_items_enqueued=review_count,
        extraction_mode=extraction.extraction_mode,
        audit_event_id=audit_event_id,
    )


@app.get("/api/review-queue", dependencies=[Depends(require_api_key)])
async def list_review_queue(
    status_filter: Optional[str] = Query(None, alias="status"),
    limit: int = Query(100, le=500),
):
    if status_filter == "pending":
        return {"items": get_pending_items(limit=limit), "stats": queue_stats()}
    return {"items": get_all_items(limit=limit), "stats": queue_stats()}


@app.post("/api/review-queue/{item_id}/approve", dependencies=[Depends(require_api_key)])
async def approve_review_item(item_id: int, reviewer_id: str = "clinician"):
    success = approve_item(item_id, reviewer_id=reviewer_id)
    if not success:
        raise HTTPException(status_code=404, detail="Review item not found.")
    log_operation(
        note_id="review_action",
        operation="approve_review_item",
        user_id=reviewer_id,
        extra={"item_id": item_id},
    )
    return {"success": True, "item_id": item_id, "new_status": "approved"}


@app.post("/api/review-queue/{item_id}/reject", dependencies=[Depends(require_api_key)])
async def reject_review_item(item_id: int, reviewer_id: str = "clinician"):
    success = reject_item(item_id, reviewer_id=reviewer_id)
    if not success:
        raise HTTPException(status_code=404, detail="Review item not found.")
    log_operation(
        note_id="review_action",
        operation="reject_review_item",
        user_id=reviewer_id,
        extra={"item_id": item_id},
    )
    return {"success": True, "item_id": item_id, "new_status": "rejected"}


@app.get("/api/audit-log", dependencies=[Depends(require_api_key)])
async def list_audit_log(
    note_id: Optional[str] = None,
    limit: int = Query(100, le=500),
):
    entries = get_audit_log(note_id=note_id, limit=limit)
    return {"entries": entries, "total": len(entries)}


@app.get("/api/fhir/{note_id}", dependencies=[Depends(require_api_key)])
async def get_fhir_bundle(note_id: str):
    bundle = FHIR_CACHE.get(note_id)
    if bundle is None:
        raise HTTPException(
            status_code=404,
            detail=f"No FHIR Bundle found for note_id={note_id}. Process the note first.",
        )
    return JSONResponse(content=bundle)


@app.get("/api/sample-notes", dependencies=[Depends(require_api_key)])
async def list_sample_notes():
    """Return the sample notes for testing."""
    notes_path = Path(__file__).parent.parent / "data" / "sample_notes.json"
    if not notes_path.exists():
        raise HTTPException(status_code=404, detail="Sample notes not found.")
    with open(notes_path) as f:
        notes = json.load(f)
    return {"notes": notes, "count": len(notes)}
