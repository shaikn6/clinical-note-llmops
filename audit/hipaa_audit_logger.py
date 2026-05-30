"""
HIPAA Audit Logger (V2).

Records every PHI access / redaction event in a structured, SQLite-backed
audit log.  No raw PHI values are ever stored — only aggregate metadata
(phi_count, phi_types, operator_id, record_id, action, timestamp).

Schema per audit entry:
  - event_id    : UUID
  - timestamp   : ISO-8601 UTC
  - operator_id : user / service that performed the action
  - action      : phi_access | phi_redaction | entity_extraction |
                  fhir_export | batch_process | record_view
  - record_id   : note / patient / batch identifier
  - resource_type : Patient | Note | Bundle | Batch | …
  - phi_types_detected : JSON list of PII type names (no values)
  - phi_count   : integer count of PHI instances
  - outcome     : success | failure | warning
  - details     : freeform JSON metadata (no PHI)

Outputs:
  - SQLite DB (default: hipaa_audit.db)
  - HIPAA-compliant PDF report: who accessed what, when

Usage::

    from audit.hipaa_audit_logger import HIPAAAuditLogger

    logger = HIPAAAuditLogger("hipaa_audit.db")
    logger.log(
        operator_id="clinician-001",
        action="phi_redaction",
        record_id="NOTE-12345",
        phi_count=5,
        phi_types=["NAME", "MRN", "DATE"],
    )
    logger.generate_report_pdf("hipaa_audit_report.pdf")
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Generator

log = logging.getLogger(__name__)

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.backends.backend_pdf import PdfPages
    MATPLOTLIB_AVAILABLE = True
except ImportError:
    MATPLOTLIB_AVAILABLE = False
    log.warning("matplotlib not available; PDF report generation disabled.")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VALID_ACTIONS = frozenset({
    "phi_access",
    "phi_redaction",
    "entity_extraction",
    "fhir_export",
    "batch_process",
    "record_view",
    "review_approve",
    "review_reject",
    "pipeline_complete",
})

VALID_OUTCOMES = frozenset({"success", "failure", "warning"})

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS hipaa_audit_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id        TEXT    NOT NULL UNIQUE,
    timestamp       TEXT    NOT NULL,
    operator_id     TEXT    NOT NULL,
    action          TEXT    NOT NULL,
    record_id       TEXT    NOT NULL,
    resource_type   TEXT    NOT NULL DEFAULT 'Note',
    phi_types       TEXT    NOT NULL DEFAULT '[]',
    phi_count       INTEGER NOT NULL DEFAULT 0,
    outcome         TEXT    NOT NULL DEFAULT 'success',
    details         TEXT    NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_operator ON hipaa_audit_log(operator_id);
CREATE INDEX IF NOT EXISTS idx_record   ON hipaa_audit_log(record_id);
CREATE INDEX IF NOT EXISTS idx_action   ON hipaa_audit_log(action);
CREATE INDEX IF NOT EXISTS idx_ts       ON hipaa_audit_log(timestamp);
"""


# ---------------------------------------------------------------------------
# Audit entry dataclass
# ---------------------------------------------------------------------------

@dataclass
class AuditEntry:
    event_id: str
    timestamp: str
    operator_id: str
    action: str
    record_id: str
    resource_type: str
    phi_types: list[str]
    phi_count: int
    outcome: str
    details: dict

    def to_dict(self) -> dict:
        return {
            "event_id": self.event_id,
            "timestamp": self.timestamp,
            "operator_id": self.operator_id,
            "action": self.action,
            "record_id": self.record_id,
            "resource_type": self.resource_type,
            "phi_types": self.phi_types,
            "phi_count": self.phi_count,
            "outcome": self.outcome,
            "details": self.details,
        }


# ---------------------------------------------------------------------------
# HIPAA Audit Logger
# ---------------------------------------------------------------------------

class HIPAAAuditLogger:
    """
    HIPAA-compliant structured audit logger backed by SQLite.

    Parameters
    ----------
    db_path : str | Path
        Path to the SQLite database file.  Use ":memory:" for tests.
    """

    def __init__(self, db_path: str | Path = "hipaa_audit.db") -> None:
        self._db_path = str(db_path)
        # For in-memory databases, keep a single persistent connection so the
        # table created in _init_db() survives across method calls.
        self._in_memory = (self._db_path == ":memory:")
        self._persistent_conn: sqlite3.Connection | None = None
        if self._in_memory:
            self._persistent_conn = sqlite3.connect(":memory:", check_same_thread=False)
            self._persistent_conn.row_factory = sqlite3.Row
        self._init_db()

    # ------------------------------------------------------------------ #
    # Internal DB helpers
    # ------------------------------------------------------------------ #

    def _connect(self) -> sqlite3.Connection:
        if self._in_memory and self._persistent_conn is not None:
            return self._persistent_conn
        conn = sqlite3.connect(self._db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _close(self, conn: sqlite3.Connection) -> None:
        """Close a connection only if it is not the shared in-memory connection."""
        if not self._in_memory:
            conn.close()

    @contextmanager
    def _db(self) -> Generator[sqlite3.Cursor, None, None]:
        conn = self._connect()
        try:
            cur = conn.cursor()
            yield cur
            conn.commit()
        finally:
            self._close(conn)

    def _init_db(self) -> None:
        conn = self._connect()
        try:
            conn.executescript(CREATE_TABLE_SQL)
            conn.commit()
        finally:
            if not self._in_memory:
                conn.close()
        log.info("HIPAA audit DB initialised: %s", self._db_path)

    # ------------------------------------------------------------------ #
    # Core logging method
    # ------------------------------------------------------------------ #

    def log(
        self,
        *,
        operator_id: str,
        action: str,
        record_id: str,
        resource_type: str = "Note",
        phi_types: list[str] | None = None,
        phi_count: int = 0,
        outcome: str = "success",
        details: dict[str, Any] | None = None,
    ) -> str:
        """
        Record one PHI access or redaction event.

        Parameters
        ----------
        operator_id : str
            ID of the user, service, or system performing the action.
        action : str
            One of VALID_ACTIONS.
        record_id : str
            Note ID, patient ID, or batch ID being accessed/processed.
        resource_type : str
            FHIR resource type or logical type (Note, Patient, Bundle, Batch …).
        phi_types : list[str]
            Names of PHI categories (e.g. ["NAME", "MRN"]).  No values.
        phi_count : int
            Number of PHI instances involved.
        outcome : str
            "success" | "failure" | "warning".
        details : dict
            Freeform metadata.  MUST NOT contain raw PHI values.

        Returns
        -------
        str
            UUID event_id.
        """
        if action not in VALID_ACTIONS:
            raise ValueError(
                f"Invalid action '{action}'. Must be one of {sorted(VALID_ACTIONS)}."
            )
        if outcome not in VALID_OUTCOMES:
            raise ValueError(
                f"Invalid outcome '{outcome}'. Must be one of {sorted(VALID_OUTCOMES)}."
            )
        if not operator_id:
            raise ValueError("'operator_id' must not be empty.")
        if not record_id:
            raise ValueError("'record_id' must not be empty.")

        event_id = str(uuid.uuid4())
        timestamp = datetime.now(timezone.utc).isoformat()
        phi_types_clean = [str(t) for t in (phi_types or [])]

        # Safety: strip any obvious PHI-looking values from details
        safe_details = _redact_details(details or {})

        with self._db() as cur:
            cur.execute(
                """
                INSERT INTO hipaa_audit_log
                (event_id, timestamp, operator_id, action, record_id,
                 resource_type, phi_types, phi_count, outcome, details)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    timestamp,
                    operator_id,
                    action,
                    record_id,
                    resource_type,
                    json.dumps(phi_types_clean),
                    phi_count,
                    outcome,
                    json.dumps(safe_details),
                ),
            )

        log.debug(
            "AUDIT [%s] operator=%s action=%s record=%s phi_count=%d outcome=%s",
            event_id[:8], operator_id, action, record_id, phi_count, outcome,
        )
        return event_id

    # ------------------------------------------------------------------ #
    # Query methods
    # ------------------------------------------------------------------ #

    def get_entries(
        self,
        *,
        operator_id: str | None = None,
        action: str | None = None,
        record_id: str | None = None,
        outcome: str | None = None,
        limit: int = 500,
        offset: int = 0,
    ) -> list[AuditEntry]:
        """
        Retrieve audit entries with optional filters.

        Returns a list of AuditEntry objects ordered by timestamp descending.
        """
        where_clauses: list[str] = []
        params: list[Any] = []

        if operator_id:
            where_clauses.append("operator_id = ?")
            params.append(operator_id)
        if action:
            where_clauses.append("action = ?")
            params.append(action)
        if record_id:
            where_clauses.append("record_id = ?")
            params.append(record_id)
        if outcome:
            where_clauses.append("outcome = ?")
            params.append(outcome)

        where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
        sql = f"""
            SELECT * FROM hipaa_audit_log
            {where_sql}
            ORDER BY timestamp DESC
            LIMIT ? OFFSET ?
        """
        params.extend([limit, offset])

        conn = self._connect()
        try:
            rows = conn.execute(sql, params).fetchall()
        finally:
            self._close(conn)

        return [_row_to_entry(row) for row in rows]

    def count(self, *, action: str | None = None, operator_id: str | None = None) -> int:
        """Return total count of entries matching filters."""
        where_parts: list[str] = []
        params: list[Any] = []
        if action:
            where_parts.append("action = ?")
            params.append(action)
        if operator_id:
            where_parts.append("operator_id = ?")
            params.append(operator_id)
        where_sql = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""
        sql = f"SELECT COUNT(*) FROM hipaa_audit_log {where_sql}"
        conn = self._connect()
        try:
            row = conn.execute(sql, params).fetchone()
            return row[0]
        finally:
            self._close(conn)

    def phi_type_summary(self) -> dict[str, int]:
        """Return aggregate counts of each PHI type across all logged events."""
        conn = self._connect()
        try:
            rows = conn.execute("SELECT phi_types FROM hipaa_audit_log").fetchall()
        finally:
            self._close(conn)

        counts: dict[str, int] = {}
        for row in rows:
            try:
                types = json.loads(row[0] or "[]")
                for t in types:
                    counts[t] = counts.get(t, 0) + 1
            except json.JSONDecodeError:
                pass
        return counts

    def operator_summary(self) -> dict[str, int]:
        """Return action count per operator."""
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT operator_id, COUNT(*) as cnt FROM hipaa_audit_log GROUP BY operator_id"
            ).fetchall()
        finally:
            self._close(conn)
        return {row[0]: row[1] for row in rows}

    def access_report(self) -> list[dict]:
        """
        Generate a 'who accessed what, when' flat report.

        Returns
        -------
        list[dict]
            Sorted by timestamp, one dict per event.
        """
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT timestamp, operator_id, action, record_id,
                       resource_type, phi_count, outcome
                FROM hipaa_audit_log
                ORDER BY timestamp ASC
                """
            ).fetchall()
        finally:
            self._close(conn)

        return [
            {
                "timestamp":     row["timestamp"],
                "operator_id":   row["operator_id"],
                "action":        row["action"],
                "record_id":     row["record_id"],
                "resource_type": row["resource_type"],
                "phi_count":     row["phi_count"],
                "outcome":       row["outcome"],
            }
            for row in rows
        ]

    # ------------------------------------------------------------------ #
    # PDF report generation
    # ------------------------------------------------------------------ #

    def generate_report_pdf(self, output_path: str) -> bool:
        """
        Generate a HIPAA-compliant audit report PDF.

        The report contains:
          Page 1: Cover / summary statistics
          Page 2: Access table (who accessed what, when)
          Page 3: PHI type breakdown bar chart
          Page 4: Operator activity chart

        Parameters
        ----------
        output_path : str
            Destination file path (e.g. "hipaa_audit_report.pdf").

        Returns
        -------
        bool
            True on success.
        """
        if not MATPLOTLIB_AVAILABLE:
            log.warning("matplotlib not installed; cannot generate PDF.")
            return False

        report = self.access_report()
        phi_summary = self.phi_type_summary()
        op_summary = self.operator_summary()
        total_events = self.count()
        total_phi = sum(r["phi_count"] for r in report)

        try:
            with PdfPages(output_path) as pdf:
                # ---- Page 1: Summary ----
                fig, ax = plt.subplots(figsize=(10, 7))
                ax.axis("off")
                summary_text = (
                    f"HIPAA Audit Report\n"
                    f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n"
                    f"Database:  {self._db_path}\n\n"
                    f"Total Events Logged : {total_events:,}\n"
                    f"Total PHI Instances : {total_phi:,}\n"
                    f"Unique Operators    : {len(op_summary):,}\n"
                    f"PHI Types Logged    : {', '.join(sorted(phi_summary.keys())) or 'None'}\n"
                )
                ax.text(
                    0.1, 0.65, summary_text,
                    transform=ax.transAxes, fontsize=13,
                    verticalalignment="top", fontfamily="monospace",
                    bbox=dict(boxstyle="round", facecolor="#e6f7f7", alpha=0.8),
                )
                ax.set_title("HIPAA Audit Report — Cover Page", fontsize=16, fontweight="bold", pad=20)
                pdf.savefig(fig, bbox_inches="tight")
                plt.close(fig)

                # ---- Page 2: Access table ----
                if report:
                    cols = ["Timestamp", "Operator", "Action", "Record ID", "PHI#", "Outcome"]
                    table_data = [
                        [
                            r["timestamp"][:19],
                            r["operator_id"][:20],
                            r["action"],
                            r["record_id"][:20],
                            r["phi_count"],
                            r["outcome"],
                        ]
                        for r in report[:50]  # cap at 50 rows for readability
                    ]

                    fig2, ax2 = plt.subplots(figsize=(14, max(4, min(len(table_data) * 0.35 + 2, 18))))
                    ax2.axis("off")
                    tbl = ax2.table(
                        cellText=table_data,
                        colLabels=cols,
                        loc="center",
                        cellLoc="left",
                    )
                    tbl.auto_set_font_size(False)
                    tbl.set_fontsize(7)
                    tbl.auto_set_column_width(col=list(range(len(cols))))

                    # Header row styling
                    for j in range(len(cols)):
                        tbl[0, j].set_facecolor("#0e7c7b")
                        tbl[0, j].set_text_props(color="white", fontweight="bold")

                    ax2.set_title(
                        f"Audit Access Log (first {min(50, len(report))} of {len(report)} events)",
                        fontsize=12, fontweight="bold",
                    )
                    pdf.savefig(fig2, bbox_inches="tight")
                    plt.close(fig2)

                # ---- Page 3: PHI type chart ----
                if phi_summary:
                    fig3, ax3 = plt.subplots(figsize=(10, 6))
                    types = sorted(phi_summary.keys())
                    counts = [phi_summary[t] for t in types]
                    colors = ["#1a6b8a", "#0e7c7b", "#16a34a", "#d97706", "#c0392b"]
                    ax3.bar(types, counts, color=colors[: len(types)])
                    ax3.set_xlabel("PHI Type")
                    ax3.set_ylabel("Event Count")
                    ax3.set_title("PHI Type Frequency in Audit Log", fontsize=12, fontweight="bold")
                    for i, (t, c) in enumerate(zip(types, counts)):
                        ax3.text(i, c + 0.1, str(c), ha="center", va="bottom", fontsize=10)
                    pdf.savefig(fig3, bbox_inches="tight")
                    plt.close(fig3)

                # ---- Page 4: Operator activity ----
                if op_summary:
                    fig4, ax4 = plt.subplots(figsize=(10, 6))
                    ops = list(op_summary.keys())[:20]  # top 20
                    op_counts = [op_summary[o] for o in ops]
                    ax4.barh(ops, op_counts, color="#0e7c7b")
                    ax4.set_xlabel("Events Logged")
                    ax4.set_title("Operator Activity (events per operator)", fontsize=12, fontweight="bold")
                    pdf.savefig(fig4, bbox_inches="tight")
                    plt.close(fig4)

            log.info("HIPAA audit report saved: %s", output_path)
            return True

        except Exception as exc:
            log.error("Failed to generate audit PDF: %s", exc)
            return False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PHI_PATTERN = re.compile(
    r"(\b\d{3}-\d{2}-\d{4}\b"       # SSN
    r"|\b\d{3}[-.\s]?\d{3}[-.\s]?\d{4}\b"  # phone
    r"|\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"  # email
    r"|\bMRN\s*:?\s*\d{5,10}\b)"    # MRN
)

def _redact_details(details: dict) -> dict:
    """
    Remove obvious PHI patterns from details values.
    Values are converted to strings before checking.
    """
    clean: dict[str, Any] = {}
    for k, v in details.items():
        if isinstance(v, str):
            clean[k] = _PHI_PATTERN.sub("[REDACTED]", v)
        else:
            clean[k] = v
    return clean


def _row_to_entry(row: sqlite3.Row) -> AuditEntry:
    return AuditEntry(
        event_id=row["event_id"],
        timestamp=row["timestamp"],
        operator_id=row["operator_id"],
        action=row["action"],
        record_id=row["record_id"],
        resource_type=row["resource_type"],
        phi_types=json.loads(row["phi_types"] or "[]"),
        phi_count=row["phi_count"],
        outcome=row["outcome"],
        details=json.loads(row["details"] or "{}"),
    )


# ---------------------------------------------------------------------------
# Module-level singleton helper (mirrors V1 pattern)
# ---------------------------------------------------------------------------

_default_logger: HIPAAAuditLogger | None = None


def get_default_logger(db_path: str = "hipaa_audit_v2.db") -> HIPAAAuditLogger:
    """Return (or create) the module-level singleton logger."""
    global _default_logger
    if _default_logger is None:
        _default_logger = HIPAAAuditLogger(db_path)
    return _default_logger
