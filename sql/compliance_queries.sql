-- HIPAA Compliance Analytics Queries
-- Run against the audit schema defined in hipaa_audit_schema.sql
-- All queries assume: SET search_path = audit, public;

SET search_path = audit, public;

-- ── 1. PHI detection rate by note type ───────────────────────────────────────
-- How many PHI entities per note, segmented by clinical note type.

SELECT
  al.note_type,
  COUNT(DISTINCT al.id)                         AS notes_processed,
  SUM(al.phi_entity_count)                      AS total_phi_entities,
  ROUND(AVG(al.phi_entity_count), 2)            AS avg_phi_per_note,
  MAX(al.phi_entity_count)                      AS max_phi_per_note,
  ROUND(
    COUNT(DISTINCT al.id) FILTER (WHERE al.compliance_level = 'HIPAA_SAFE_HARBOR')::NUMERIC
    / NULLIF(COUNT(DISTINCT al.id), 0) * 100, 2
  )                                              AS safe_harbor_pct
FROM audit.audit_log al
WHERE al.processed_at >= NOW() - INTERVAL '30 days'
GROUP BY al.note_type
ORDER BY avg_phi_per_note DESC;


-- ── 2. False positive analysis by PHI category ───────────────────────────────
-- Identifies which entity types generate the most false positives.

SELECT
  d.entity_type,
  COUNT(*)                                           AS total_detections,
  COUNT(*) FILTER (WHERE d.is_false_positive)        AS false_positives,
  ROUND(
    COUNT(*) FILTER (WHERE d.is_false_positive)::NUMERIC
    / NULLIF(COUNT(*), 0) * 100, 2
  )                                                  AS false_positive_rate_pct,
  ROUND(AVG(d.confidence), 4)                        AS avg_confidence,
  ROUND(
    AVG(d.confidence) FILTER (WHERE d.is_false_positive), 4
  )                                                  AS avg_fp_confidence
FROM audit.phi_detections d
JOIN audit.audit_log al ON al.note_id = d.note_id
WHERE al.processed_at >= NOW() - INTERVAL '90 days'
  AND d.manually_reviewed = TRUE
GROUP BY d.entity_type
ORDER BY false_positive_rate_pct DESC;


-- ── 3. Model performance drift over time ──────────────────────────────────────
-- Weekly precision / recall trend per model version.

SELECT
  DATE_TRUNC('week', al.processed_at)           AS week,
  mv.model_id,
  mv.version,
  COUNT(DISTINCT al.id)                         AS notes_evaluated,
  COUNT(DISTINCT d.id) FILTER (WHERE NOT d.is_false_positive)  AS true_positives,
  COUNT(DISTINCT d.id) FILTER (WHERE d.is_false_positive)      AS false_positives,
  ROUND(
    COUNT(DISTINCT d.id) FILTER (WHERE NOT d.is_false_positive)::NUMERIC
    / NULLIF(COUNT(DISTINCT d.id), 0) * 100, 2
  )                                             AS empirical_precision_pct
FROM audit.audit_log al
JOIN audit.model_versions mv ON mv.id = al.model_version_id
LEFT JOIN audit.phi_detections d ON d.note_id = al.note_id
  AND d.model_version_id = al.model_version_id
  AND d.manually_reviewed = TRUE
WHERE al.processed_at >= NOW() - INTERVAL '180 days'
GROUP BY DATE_TRUNC('week', al.processed_at), mv.model_id, mv.version
ORDER BY week DESC, mv.model_id;


-- ── 4. Compliance dashboard — 30-day summary ─────────────────────────────────

SELECT
  COUNT(*)                                      AS total_notes,
  SUM(phi_entity_count)                         AS total_phi_removed,
  COUNT(*) FILTER (
    WHERE compliance_level = 'HIPAA_SAFE_HARBOR'
  )                                             AS safe_harbor_count,
  COUNT(*) FILTER (
    WHERE compliance_level = 'NON_COMPLIANT'
  )                                             AS non_compliant_count,
  COUNT(*) FILTER (WHERE flagged_for_review)    AS flagged_count,
  COUNT(*) FILTER (WHERE review_required AND reviewed_by IS NULL) AS pending_review,
  ROUND(AVG(processing_duration_ms), 0)         AS avg_processing_ms,
  ROUND(PERCENTILE_CONT(0.95) WITHIN GROUP (
    ORDER BY processing_duration_ms
  ), 0)                                         AS p95_processing_ms
FROM audit.audit_log
WHERE processed_at >= NOW() - INTERVAL '30 days';


-- ── 5. Notes requiring urgent human review ────────────────────────────────────

SELECT
  al.id,
  al.note_id,
  al.note_type,
  al.processed_at,
  al.phi_entity_count,
  al.compliance_level,
  STRING_AGG(DISTINCT d.entity_type::TEXT, ', ')  AS phi_types_found,
  al.reviewer_notes
FROM audit.audit_log al
JOIN audit.phi_detections d ON d.note_id = al.note_id
WHERE al.flagged_for_review = TRUE
  AND al.reviewed_by IS NULL
  AND al.processed_at >= NOW() - INTERVAL '7 days'
GROUP BY al.id, al.note_id, al.note_type, al.processed_at,
         al.phi_entity_count, al.compliance_level, al.reviewer_notes
ORDER BY
  CASE al.compliance_level
    WHEN 'NON_COMPLIANT' THEN 1
    WHEN 'PENDING'       THEN 2
    ELSE 3
  END,
  al.phi_entity_count DESC;


-- ── 6. Daily PHI volume trend (last 14 days) ──────────────────────────────────

SELECT
  DATE_TRUNC('day', processed_at)::DATE   AS processing_date,
  COUNT(*)                                AS notes_processed,
  SUM(phi_entity_count)                   AS phi_entities_removed,
  ROUND(AVG(phi_entity_count), 2)         AS avg_phi_per_note,
  COUNT(*) FILTER (WHERE compliance_level = 'HIPAA_SAFE_HARBOR')  AS compliant,
  COUNT(*) FILTER (WHERE compliance_level = 'NON_COMPLIANT')       AS non_compliant
FROM audit.audit_log
WHERE processed_at >= NOW() - INTERVAL '14 days'
GROUP BY DATE_TRUNC('day', processed_at)::DATE
ORDER BY processing_date DESC;


-- ── 7. High-confidence SSN / MRN detections this week ─────────────────────────
-- Critical PHI — verify none slipped through.

SELECT
  d.note_id,
  d.entity_type,
  d.confidence,
  d.replacement,
  d.detected_at,
  al.compliance_level,
  al.facility_id
FROM audit.phi_detections d
JOIN audit.audit_log al ON al.note_id = d.note_id
WHERE d.entity_type IN ('SSN', 'MRN', 'ACCOUNT')
  AND d.confidence >= 0.95
  AND d.detected_at >= NOW() - INTERVAL '7 days'
ORDER BY d.confidence DESC, d.detected_at DESC
LIMIT 100;
