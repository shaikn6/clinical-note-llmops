"""
Audit Logger — HIPAA-compliant operation logging.

Every pipeline operation is logged to:
  1. SQLite local database (table: audit_log)
  2. AWS S3 (simulated via boto3 in mock mode)

The audit trail records who processed what, when, and what PHI categories
were encountered — WITHOUT storing the actual PHI values.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Column, Integer, String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# DB setup (shares engine with review_queue.py — same SQLite file)
# ---------------------------------------------------------------------------

DB_URL: str = os.getenv("DATABASE_URL", "sqlite:///./clinical_llmops.db")
MOCK_S3: bool = os.getenv("MOCK_S3", "true").lower() in ("true", "1", "yes")
S3_BUCKET: str = os.getenv("S3_AUDIT_BUCKET", "hipaa-audit-logs")

audit_engine = create_engine(
    DB_URL,
    connect_args={"check_same_thread": False} if DB_URL.startswith("sqlite") else {},
    echo=False,
)
AuditSession = sessionmaker(bind=audit_engine, autoflush=False, autocommit=False)


class AuditBase(DeclarativeBase):
    pass


class AuditLogEntry(AuditBase):
    __tablename__ = "audit_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    event_id = Column(String(64), nullable=False, unique=True)
    note_id = Column(String(64), nullable=False, index=True)
    operation = Column(String(64), nullable=False)
    user_id = Column(String(64), nullable=False, default="system")
    phi_types_detected = Column(Text, nullable=True)   # JSON list, no PHI values
    phi_count = Column(Integer, nullable=True)
    extraction_mode = Column(String(16), nullable=True)
    entity_count = Column(Integer, nullable=True)
    status = Column(String(16), nullable=False, default="success")
    error_message = Column(Text, nullable=True)
    timestamp = Column(String(32), nullable=False)
    details_json = Column(Text, nullable=True)


def init_audit_db() -> None:
    AuditBase.metadata.create_all(bind=audit_engine)
    logger.info("Audit log table initialised.")


# ---------------------------------------------------------------------------
# S3 interaction (boto3, mock-friendly)
# ---------------------------------------------------------------------------

def _s3_put(key: str, payload: dict) -> bool:
    """
    Upload audit payload to S3.
    In MOCK_S3=true mode, logs instead of making real AWS calls.
    """
    body = json.dumps(payload, indent=2)

    if MOCK_S3:
        logger.info(
            "[MOCK S3] s3.put_object(Bucket='%s', Key='%s', size=%d bytes)",
            S3_BUCKET, key, len(body),
        )
        return True

    try:
        import boto3  # type: ignore[import]
        client = boto3.client("s3")
        client.put_object(
            Bucket=S3_BUCKET,
            Key=key,
            Body=body.encode("utf-8"),
            ContentType="application/json",
            ServerSideEncryption="aws:kms",   # HIPAA: encrypt at rest
            Metadata={
                "note-id": payload.get("note_id", ""),
                "operation": payload.get("operation", ""),
            },
        )
        logger.info("Audit log uploaded to s3://%s/%s", S3_BUCKET, key)
        return True
    except Exception as exc:
        logger.error("S3 upload failed for key %s: %s", key, exc)
        return False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def log_operation(
    note_id: str,
    operation: str,
    user_id: str = "system",
    phi_types_detected: list[str] | None = None,
    phi_count: int = 0,
    extraction_mode: str | None = None,
    entity_count: int | None = None,
    status: str = "success",
    error_message: str | None = None,
    extra: dict[str, Any] | None = None,
) -> str:
    """
    Log a pipeline operation locally (SQLite) and remotely (S3).

    Returns the event_id (UUID).

    IMPORTANT: Do NOT pass raw PHI values into this function.
               Only aggregate metadata (phi_count, phi_types) is logged.
    """
    event_id = str(uuid.uuid4())
    timestamp = datetime.now(timezone.utc).isoformat()

    # --- SQLite ---
    with AuditSession() as session:
        entry = AuditLogEntry(
            event_id=event_id,
            note_id=note_id,
            operation=operation,
            user_id=user_id,
            phi_types_detected=json.dumps(phi_types_detected or []),
            phi_count=phi_count,
            extraction_mode=extraction_mode,
            entity_count=entity_count,
            status=status,
            error_message=error_message,
            timestamp=timestamp,
            details_json=json.dumps(extra or {}),
        )
        session.add(entry)
        session.commit()

    # --- S3 ---
    s3_payload = {
        "event_id": event_id,
        "note_id": note_id,
        "operation": operation,
        "user_id": user_id,
        "phi_types_detected": phi_types_detected or [],
        "phi_count": phi_count,
        "extraction_mode": extraction_mode,
        "entity_count": entity_count,
        "status": status,
        "error_message": error_message,
        "timestamp": timestamp,
    }
    s3_key = f"audit/{note_id}/{event_id}.json"
    _s3_put(s3_key, s3_payload)

    return event_id


def get_audit_log(note_id: str | None = None, limit: int = 200) -> list[dict]:
    """Retrieve audit log entries, optionally filtered by note_id."""
    with AuditSession() as session:
        query = session.query(AuditLogEntry).order_by(
            AuditLogEntry.timestamp.desc()
        )
        if note_id:
            query = query.filter(AuditLogEntry.note_id == note_id)
        rows = query.limit(limit).all()
        return [_row_to_dict(r) for r in rows]


def _row_to_dict(row: AuditLogEntry) -> dict:
    return {
        "id": row.id,
        "event_id": row.event_id,
        "note_id": row.note_id,
        "operation": row.operation,
        "user_id": row.user_id,
        "phi_types_detected": json.loads(row.phi_types_detected or "[]"),
        "phi_count": row.phi_count,
        "extraction_mode": row.extraction_mode,
        "entity_count": row.entity_count,
        "status": row.status,
        "error_message": row.error_message,
        "timestamp": row.timestamp,
        "details": json.loads(row.details_json or "{}"),
    }
