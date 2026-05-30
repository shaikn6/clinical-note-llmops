"""
Tests for audit/hipaa_audit_logger.py

Covers:
  - HIPAAAuditLogger.log: valid events, invalid actions, invalid outcomes
  - HIPAAAuditLogger.get_entries: filters (operator, action, record_id)
  - HIPAAAuditLogger.count
  - HIPAAAuditLogger.phi_type_summary
  - HIPAAAuditLogger.operator_summary
  - HIPAAAuditLogger.access_report
  - _redact_details: strips obvious PHI patterns
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from audit.hipaa_audit_logger import (
    HIPAAAuditLogger,
    AuditEntry,
    VALID_ACTIONS,
    VALID_OUTCOMES,
    _redact_details,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def logger() -> HIPAAAuditLogger:
    """In-memory SQLite logger for each test."""
    return HIPAAAuditLogger(db_path=":memory:")


# ---------------------------------------------------------------------------
# log()
# ---------------------------------------------------------------------------

class TestLog:
    def test_returns_event_id_string(self, logger):
        eid = logger.log(operator_id="user-1", action="phi_redaction", record_id="NOTE-1")
        assert isinstance(eid, str)
        assert len(eid) == 36  # UUID format

    def test_event_stored_in_db(self, logger):
        eid = logger.log(operator_id="user-1", action="phi_redaction", record_id="NOTE-1")
        entries = logger.get_entries()
        assert any(e.event_id == eid for e in entries)

    def test_phi_types_stored(self, logger):
        eid = logger.log(
            operator_id="sys", action="phi_redaction", record_id="N1",
            phi_types=["NAME", "MRN"], phi_count=2,
        )
        entries = logger.get_entries()
        entry = next(e for e in entries if e.event_id == eid)
        assert "NAME" in entry.phi_types
        assert "MRN" in entry.phi_types

    def test_phi_count_stored(self, logger):
        eid = logger.log(operator_id="sys", action="entity_extraction", record_id="N2", phi_count=7)
        entry = next(e for e in logger.get_entries() if e.event_id == eid)
        assert entry.phi_count == 7

    def test_outcome_default_success(self, logger):
        eid = logger.log(operator_id="sys", action="fhir_export", record_id="N3")
        entry = next(e for e in logger.get_entries() if e.event_id == eid)
        assert entry.outcome == "success"

    def test_outcome_failure(self, logger):
        eid = logger.log(operator_id="sys", action="fhir_export", record_id="N4", outcome="failure")
        entry = next(e for e in logger.get_entries() if e.event_id == eid)
        assert entry.outcome == "failure"

    def test_invalid_action_raises(self, logger):
        with pytest.raises(ValueError, match="Invalid action"):
            logger.log(operator_id="u", action="banana", record_id="N")

    def test_invalid_outcome_raises(self, logger):
        with pytest.raises(ValueError, match="Invalid outcome"):
            logger.log(operator_id="u", action="phi_redaction", record_id="N", outcome="maybe")

    def test_empty_operator_id_raises(self, logger):
        with pytest.raises(ValueError, match="operator_id"):
            logger.log(operator_id="", action="phi_redaction", record_id="N")

    def test_empty_record_id_raises(self, logger):
        with pytest.raises(ValueError, match="record_id"):
            logger.log(operator_id="u", action="phi_redaction", record_id="")

    def test_multiple_events_stored(self, logger):
        for i in range(5):
            logger.log(operator_id=f"op-{i}", action="phi_access", record_id=f"R{i}")
        assert logger.count() == 5


# ---------------------------------------------------------------------------
# get_entries()
# ---------------------------------------------------------------------------

class TestGetEntries:
    def _seed(self, logger) -> None:
        logger.log(operator_id="alice", action="phi_redaction",  record_id="NOTE-A", phi_count=3)
        logger.log(operator_id="bob",   action="entity_extraction", record_id="NOTE-B", phi_count=0)
        logger.log(operator_id="alice", action="fhir_export",    record_id="NOTE-A", phi_count=0)

    def test_filter_by_operator(self, logger):
        self._seed(logger)
        entries = logger.get_entries(operator_id="alice")
        assert all(e.operator_id == "alice" for e in entries)
        assert len(entries) == 2

    def test_filter_by_action(self, logger):
        self._seed(logger)
        entries = logger.get_entries(action="fhir_export")
        assert all(e.action == "fhir_export" for e in entries)

    def test_filter_by_record_id(self, logger):
        self._seed(logger)
        entries = logger.get_entries(record_id="NOTE-A")
        assert all(e.record_id == "NOTE-A" for e in entries)
        assert len(entries) == 2

    def test_limit_respected(self, logger):
        for i in range(10):
            logger.log(operator_id="u", action="phi_access", record_id=f"R{i}")
        entries = logger.get_entries(limit=3)
        assert len(entries) <= 3

    def test_returns_audit_entry_objects(self, logger):
        logger.log(operator_id="u", action="phi_redaction", record_id="N")
        entries = logger.get_entries()
        assert all(isinstance(e, AuditEntry) for e in entries)


# ---------------------------------------------------------------------------
# count()
# ---------------------------------------------------------------------------

class TestCount:
    def test_count_zero_initially(self, logger):
        assert logger.count() == 0

    def test_count_increments(self, logger):
        for i in range(3):
            logger.log(operator_id="u", action="phi_access", record_id=f"R{i}")
        assert logger.count() == 3

    def test_count_filter_by_action(self, logger):
        logger.log(operator_id="u", action="phi_access",    record_id="R1")
        logger.log(operator_id="u", action="phi_redaction", record_id="R2")
        assert logger.count(action="phi_access") == 1


# ---------------------------------------------------------------------------
# phi_type_summary()
# ---------------------------------------------------------------------------

class TestPhiTypeSummary:
    def test_empty_returns_empty_dict(self, logger):
        assert logger.phi_type_summary() == {}

    def test_counts_phi_types(self, logger):
        logger.log(operator_id="u", action="phi_redaction", record_id="N1",
                   phi_types=["NAME", "MRN"])
        logger.log(operator_id="u", action="phi_redaction", record_id="N2",
                   phi_types=["NAME", "PHONE"])
        summary = logger.phi_type_summary()
        assert summary["NAME"] == 2
        assert summary["MRN"] == 1
        assert summary["PHONE"] == 1


# ---------------------------------------------------------------------------
# operator_summary() / access_report()
# ---------------------------------------------------------------------------

class TestOperatorSummaryAndReport:
    def test_operator_summary_counts(self, logger):
        logger.log(operator_id="alice", action="phi_access", record_id="R1")
        logger.log(operator_id="alice", action="phi_access", record_id="R2")
        logger.log(operator_id="bob",   action="phi_access", record_id="R3")
        summary = logger.operator_summary()
        assert summary["alice"] == 2
        assert summary["bob"] == 1

    def test_access_report_is_list(self, logger):
        logger.log(operator_id="u", action="record_view", record_id="N1")
        report = logger.access_report()
        assert isinstance(report, list)

    def test_access_report_has_required_keys(self, logger):
        logger.log(operator_id="u", action="record_view", record_id="N1")
        report = logger.access_report()
        required = {"timestamp", "operator_id", "action", "record_id", "phi_count", "outcome"}
        for row in report:
            assert required.issubset(set(row.keys()))


# ---------------------------------------------------------------------------
# _redact_details
# ---------------------------------------------------------------------------

class TestRedactDetails:
    def test_ssn_redacted(self):
        d = {"note": "SSN 123-45-6789 on file"}
        result = _redact_details(d)
        assert "123-45-6789" not in result["note"]

    def test_phone_redacted(self):
        d = {"contact": "Phone: 555-234-8901"}
        result = _redact_details(d)
        assert "555-234-8901" not in result["contact"]

    def test_email_redacted(self):
        d = {"info": "Email: user@hospital.com"}
        result = _redact_details(d)
        assert "user@hospital.com" not in result["info"]

    def test_non_string_values_passed_through(self):
        d = {"count": 42, "active": True}
        result = _redact_details(d)
        assert result["count"] == 42
        assert result["active"] is True

    def test_clean_text_unchanged(self):
        d = {"mode": "mock", "icd_count": "3"}
        result = _redact_details(d)
        assert result["mode"] == "mock"
