-- HIPAA Audit Schema
-- Tracks PHI de-identification activity, model versions, and compliance reporting.
-- Designed for PostgreSQL 14+.

SET search_path = audit, public;
CREATE SCHEMA IF NOT EXISTS audit;

-- ── ENUM types ───────────────────────────────────────────────────────────────

CREATE TYPE audit.phi_category AS ENUM (
  'PERSON', 'DATE', 'LOCATION', 'PHONE', 'EMAIL',
  'SSN', 'MRN', 'DEVICE', 'URL', 'IP_ADDRESS',
  'VEHICLE', 'ACCOUNT', 'LICENSE', 'CERTIFICATE', 'AGE', 'UNKNOWN'
);

CREATE TYPE audit.note_type AS ENUM (
  'DISCHARGE', 'PROGRESS', 'RADIOLOGY', 'PATHOLOGY', 'OPERATIVE', 'OTHER'
);

CREATE TYPE audit.compliance_level AS ENUM (
  'HIPAA_SAFE_HARBOR', 'HIPAA_EXPERT_DETERMINATION', 'NON_COMPLIANT', 'PENDING'
);

CREATE TYPE audit.model_status AS ENUM (
  'ACTIVE', 'DEPRECATED', 'TESTING', 'RETIRED'
);

-- ── model_versions ───────────────────────────────────────────────────────────

CREATE TABLE audit.model_versions (
  id                  UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
  model_id            VARCHAR(64)     NOT NULL,
  version             VARCHAR(32)     NOT NULL,
  description         TEXT,
  deployed_at         TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
  retired_at          TIMESTAMPTZ,
  status              audit.model_status NOT NULL DEFAULT 'TESTING',
  precision_score     NUMERIC(5, 4)   CHECK (precision_score BETWEEN 0 AND 1),
  recall_score        NUMERIC(5, 4)   CHECK (recall_score BETWEEN 0 AND 1),
  f1_score            NUMERIC(5, 4)   CHECK (f1_score BETWEEN 0 AND 1),
  false_positive_rate NUMERIC(5, 4)   CHECK (false_positive_rate BETWEEN 0 AND 1),
  false_negative_rate NUMERIC(5, 4)   CHECK (false_negative_rate BETWEEN 0 AND 1),
  training_notes_count INTEGER,
  created_by          VARCHAR(128),
  UNIQUE (model_id, version)
);

COMMENT ON TABLE audit.model_versions IS
  'Registry of PHI detection model versions with performance metrics.';

-- ── phi_detections ───────────────────────────────────────────────────────────

CREATE TABLE audit.phi_detections (
  id               UUID              PRIMARY KEY DEFAULT gen_random_uuid(),
  note_id          VARCHAR(128)      NOT NULL,
  note_hash        CHAR(64)          NOT NULL,        -- SHA-256 of original note
  model_version_id UUID              NOT NULL REFERENCES audit.model_versions(id),
  entity_type      audit.phi_category NOT NULL,
  char_start       INTEGER           NOT NULL CHECK (char_start >= 0),
  char_end         INTEGER           NOT NULL CHECK (char_end > char_start),
  replacement      VARCHAR(128)      NOT NULL,
  confidence       NUMERIC(5, 4)     NOT NULL CHECK (confidence BETWEEN 0 AND 1),
  manually_reviewed BOOLEAN          NOT NULL DEFAULT FALSE,
  is_false_positive BOOLEAN          NOT NULL DEFAULT FALSE,
  reviewer_notes   TEXT,
  detected_at      TIMESTAMPTZ       NOT NULL DEFAULT NOW(),
  CONSTRAINT valid_char_range CHECK (char_end > char_start)
);

CREATE INDEX idx_phi_detections_note     ON audit.phi_detections (note_id);
CREATE INDEX idx_phi_detections_type     ON audit.phi_detections (entity_type);
CREATE INDEX idx_phi_detections_model    ON audit.phi_detections (model_version_id);
CREATE INDEX idx_phi_detections_detected ON audit.phi_detections (detected_at DESC);
CREATE INDEX idx_phi_detections_fp       ON audit.phi_detections (is_false_positive) WHERE is_false_positive = TRUE;

COMMENT ON TABLE audit.phi_detections IS
  'Individual PHI entity detections per clinical note. One row per entity span.';

-- ── audit_log ────────────────────────────────────────────────────────────────

CREATE TABLE audit.audit_log (
  id                    UUID                   PRIMARY KEY DEFAULT gen_random_uuid(),
  note_id               VARCHAR(128)           NOT NULL,
  patient_mrn_hash      CHAR(64),              -- SHA-256 of MRN, never plaintext
  note_type             audit.note_type        NOT NULL DEFAULT 'OTHER',
  note_hash             CHAR(64)               NOT NULL,
  model_version_id      UUID                   NOT NULL REFERENCES audit.model_versions(id),
  processed_at          TIMESTAMPTZ            NOT NULL DEFAULT NOW(),
  processing_duration_ms INTEGER               NOT NULL CHECK (processing_duration_ms >= 0),
  phi_entity_count      INTEGER                NOT NULL DEFAULT 0 CHECK (phi_entity_count >= 0),
  compliance_level      audit.compliance_level NOT NULL DEFAULT 'PENDING',
  hipaa_rule            VARCHAR(16)            DEFAULT '164.514(b)',
  audit_trail_complete  BOOLEAN                NOT NULL DEFAULT FALSE,
  review_required       BOOLEAN                NOT NULL DEFAULT FALSE,
  flagged_for_review    BOOLEAN                NOT NULL DEFAULT FALSE,
  reviewed_by           VARCHAR(128),
  reviewed_at           TIMESTAMPTZ,
  reviewer_notes        TEXT,
  source_system         VARCHAR(64),           -- e.g. 'EPIC_FHIR_R4', 'MANUAL_UPLOAD'
  facility_id           VARCHAR(32),
  CONSTRAINT reviewed_has_reviewer CHECK (
    (reviewed_at IS NULL) OR (reviewed_by IS NOT NULL)
  )
);

CREATE INDEX idx_audit_log_note       ON audit.audit_log (note_id);
CREATE INDEX idx_audit_log_processed  ON audit.audit_log (processed_at DESC);
CREATE INDEX idx_audit_log_compliance ON audit.audit_log (compliance_level);
CREATE INDEX idx_audit_log_flagged    ON audit.audit_log (flagged_for_review) WHERE flagged_for_review = TRUE;
CREATE INDEX idx_audit_log_facility   ON audit.audit_log (facility_id);

COMMENT ON TABLE audit.audit_log IS
  'Per-note audit record for HIPAA de-identification pipeline. No PHI stored.';

-- ── compliance_reports ───────────────────────────────────────────────────────

CREATE TABLE audit.compliance_reports (
  id                       UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  report_period_start      DATE        NOT NULL,
  report_period_end        DATE        NOT NULL,
  generated_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  generated_by             VARCHAR(128),
  total_notes_processed    INTEGER     NOT NULL DEFAULT 0,
  total_phi_entities       INTEGER     NOT NULL DEFAULT 0,
  compliant_notes          INTEGER     NOT NULL DEFAULT 0,
  non_compliant_notes      INTEGER     NOT NULL DEFAULT 0,
  compliance_rate          NUMERIC(5, 2) GENERATED ALWAYS AS (
    CASE WHEN total_notes_processed = 0 THEN 0
         ELSE ROUND(compliant_notes::NUMERIC / total_notes_processed * 100, 2)
    END
  ) STORED,
  avg_processing_ms        INTEGER,
  false_positive_count     INTEGER     NOT NULL DEFAULT 0,
  false_negative_estimate  INTEGER     NOT NULL DEFAULT 0,
  flagged_for_review_count INTEGER     NOT NULL DEFAULT 0,
  report_notes             TEXT,
  submitted_to_compliance  BOOLEAN     NOT NULL DEFAULT FALSE,
  submitted_at             TIMESTAMPTZ,
  CONSTRAINT valid_period CHECK (report_period_end >= report_period_start),
  CONSTRAINT valid_counts CHECK (
    compliant_notes + non_compliant_notes <= total_notes_processed
  )
);

CREATE INDEX idx_compliance_reports_period ON audit.compliance_reports (report_period_start, report_period_end);

COMMENT ON TABLE audit.compliance_reports IS
  'Aggregated HIPAA compliance reports. Generated periodically for audit submissions.';

-- ── phi_type_daily_summary (materialized view) ────────────────────────────────

CREATE MATERIALIZED VIEW audit.phi_type_daily_summary AS
SELECT
  DATE_TRUNC('day', d.detected_at) AS detection_date,
  d.entity_type,
  COUNT(*) AS detection_count,
  AVG(d.confidence) AS avg_confidence,
  COUNT(*) FILTER (WHERE d.is_false_positive) AS false_positive_count
FROM audit.phi_detections d
GROUP BY DATE_TRUNC('day', d.detected_at), d.entity_type
WITH DATA;

CREATE UNIQUE INDEX idx_phi_daily_summary_pk
  ON audit.phi_type_daily_summary (detection_date, entity_type);

COMMENT ON MATERIALIZED VIEW audit.phi_type_daily_summary IS
  'Pre-aggregated daily PHI detection counts per entity type. Refresh nightly.';

-- ── Row-level security ────────────────────────────────────────────────────────

ALTER TABLE audit.audit_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit.phi_detections ENABLE ROW LEVEL SECURITY;

-- Only compliance role can see full audit records
CREATE POLICY audit_log_compliance_read ON audit.audit_log
  FOR SELECT USING (current_user = 'compliance_role' OR current_user = 'audit_admin');

-- Analysts can only see aggregate-level data via the materialized view
CREATE POLICY phi_detections_analyst ON audit.phi_detections
  FOR SELECT USING (current_user IN ('compliance_role', 'audit_admin', 'model_trainer'));
