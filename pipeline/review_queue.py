"""
Review Queue — Human-in-the-loop queue for low-confidence extractions.

Low-confidence entities (confidence < 0.70) are inserted into a SQLite
`review_queue` table and withheld from automatic FHIR mapping until
a human reviewer approves or rejects them.

Schema:
  id            INTEGER PK AUTOINCREMENT
  note_id       TEXT
  entity_type   TEXT   ("icd_code" | "medication")
  entity_value  TEXT   (human-readable: code+description or drug name+dose)
  confidence    REAL
  status        TEXT   ("pending" | "approved" | "rejected")
  reviewer_id   TEXT   (NULL until actioned)
  created_at    TEXT   (ISO-8601)
  actioned_at   TEXT   (NULL until actioned)
  details_json  TEXT   (full entity JSON)
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone

from sqlalchemy import (
    Column, Float, Integer, String, Text, create_engine, text,
)
from sqlalchemy.orm import DeclarativeBase, sessionmaker

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Database setup
# ---------------------------------------------------------------------------

DB_URL: str = os.getenv("DATABASE_URL", "sqlite:///./clinical_llmops.db")

engine = create_engine(
    DB_URL,
    connect_args={"check_same_thread": False} if DB_URL.startswith("sqlite") else {},
    echo=False,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


class ReviewQueueItem(Base):
    __tablename__ = "review_queue"

    id = Column(Integer, primary_key=True, autoincrement=True)
    note_id = Column(String(64), nullable=False, index=True)
    entity_type = Column(String(32), nullable=False)
    entity_value = Column(Text, nullable=False)
    confidence = Column(Float, nullable=False)
    status = Column(String(16), nullable=False, default="pending", index=True)
    reviewer_id = Column(String(64), nullable=True)
    created_at = Column(String(32), nullable=False)
    actioned_at = Column(String(32), nullable=True)
    details_json = Column(Text, nullable=True)


def init_db() -> None:
    """Create tables if they do not exist."""
    Base.metadata.create_all(bind=engine)
    logger.info("Database tables initialised.")


# ---------------------------------------------------------------------------
# Queue operations
# ---------------------------------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def enqueue_low_confidence(
    note_id: str,
    entity_type: str,
    entity_value: str,
    confidence: float,
    details: dict | None = None,
) -> int:
    """
    Insert a low-confidence entity into the review queue.

    Returns the new row id.
    """
    with SessionLocal() as session:
        item = ReviewQueueItem(
            note_id=note_id,
            entity_type=entity_type,
            entity_value=entity_value,
            confidence=confidence,
            status="pending",
            created_at=_now(),
            details_json=json.dumps(details or {}),
        )
        session.add(item)
        session.commit()
        session.refresh(item)
        row_id: int = item.id
    logger.info(
        "Enqueued review item %d for note %s (conf=%.2f, type=%s)",
        row_id, note_id, confidence, entity_type,
    )
    return row_id


def get_pending_items(limit: int = 100) -> list[dict]:
    """Return all pending review items as dicts."""
    with SessionLocal() as session:
        items = (
            session.query(ReviewQueueItem)
            .filter(ReviewQueueItem.status == "pending")
            .order_by(ReviewQueueItem.confidence.asc())  # lowest confidence first
            .limit(limit)
            .all()
        )
        return [_item_to_dict(i) for i in items]


def get_all_items(limit: int = 500) -> list[dict]:
    """Return all review items (any status)."""
    with SessionLocal() as session:
        items = (
            session.query(ReviewQueueItem)
            .order_by(ReviewQueueItem.created_at.desc())
            .limit(limit)
            .all()
        )
        return [_item_to_dict(i) for i in items]


def get_item_by_id(item_id: int) -> dict | None:
    with SessionLocal() as session:
        item = session.get(ReviewQueueItem, item_id)
        return _item_to_dict(item) if item else None


def approve_item(item_id: int, reviewer_id: str = "system") -> bool:
    return _update_status(item_id, "approved", reviewer_id)


def reject_item(item_id: int, reviewer_id: str = "system") -> bool:
    return _update_status(item_id, "rejected", reviewer_id)


def _update_status(item_id: int, status: str, reviewer_id: str) -> bool:
    with SessionLocal() as session:
        item = session.get(ReviewQueueItem, item_id)
        if item is None:
            return False
        item.status = status
        item.reviewer_id = reviewer_id
        item.actioned_at = _now()
        session.commit()
        logger.info("Review item %d → %s by %s", item_id, status, reviewer_id)
        return True


def queue_stats() -> dict:
    """Return counts by status."""
    with SessionLocal() as session:
        rows = session.execute(
            text("SELECT status, COUNT(*) AS cnt FROM review_queue GROUP BY status")
        ).fetchall()
        return {row[0]: row[1] for row in rows}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _item_to_dict(item: ReviewQueueItem) -> dict:
    return {
        "id": item.id,
        "note_id": item.note_id,
        "entity_type": item.entity_type,
        "entity_value": item.entity_value,
        "confidence": item.confidence,
        "status": item.status,
        "reviewer_id": item.reviewer_id,
        "created_at": item.created_at,
        "actioned_at": item.actioned_at,
        "details": json.loads(item.details_json or "{}"),
    }


def populate_review_queue(note_id: str, scoring_result) -> int:
    """
    Convenience: enqueue all low-confidence entities from a ScoringResult.
    Returns count of items enqueued.
    """
    count = 0
    CONFIDENCE_THRESHOLD = 0.70
    for entity in scoring_result.scored_entities:
        if entity.confidence < CONFIDENCE_THRESHOLD:
            enqueue_low_confidence(
                note_id=note_id,
                entity_type=entity.entity_type,
                entity_value=entity.entity_value,
                confidence=entity.confidence,
                details=entity.details,
            )
            count += 1
    return count
