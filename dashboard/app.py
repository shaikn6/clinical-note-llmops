"""
Streamlit Dashboard — Clinical Note LLMOps

Tabs:
  1. Process Note  — Run the full pipeline on a note, view results
  2. Review Queue  — List / approve / reject low-confidence items
  3. Pipeline Metrics — Plotly charts over all processed notes
  4. FHIR Explorer — JSON viewer of generated FHIR Bundles
  5. Audit Log     — Scrollable audit trail with filters
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import streamlit as st

# Add project root to path so pipeline imports work
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402
import plotly.express as px  # noqa: E402

from pipeline.pii_scrubber import scrub_note  # noqa: E402
from pipeline.entity_extractor import extract_entities  # noqa: E402
from pipeline.confidence_scorer import score_extractions  # noqa: E402
from pipeline.fhir_mapper import map_to_fhir, bundle_to_json, validate_bundle  # noqa: E402
from pipeline.review_queue import (  # noqa: E402
    init_db,
    populate_review_queue,
    get_all_items,
    approve_item,
    reject_item,
    queue_stats,
)
from pipeline.audit_logger import init_audit_db, log_operation, get_audit_log  # noqa: E402

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="ClinicalNote LLMOps",
    page_icon="⚕️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# Init DB once per session
# ---------------------------------------------------------------------------

@st.cache_resource
def _init():
    init_db()
    init_audit_db()
    return True

_init()

# ---------------------------------------------------------------------------
# Custom CSS
# ---------------------------------------------------------------------------

st.markdown("""
<style>
  .main .block-container { padding-top: 1.5rem; }
  .confidence-high   { color: #16a34a; font-weight: 700; }
  .confidence-medium { color: #d97706; font-weight: 700; }
  .confidence-low    { color: #c0392b; font-weight: 700; }
  .entity-card {
    background: #f8fafc;
    border: 1px solid #d1dde8;
    border-radius: 8px;
    padding: 0.75rem 1rem;
    margin-bottom: 0.5rem;
  }
  .phi-strip {
    background: #e6f7f7;
    border: 1px solid rgba(14,124,123,0.25);
    border-radius: 8px;
    padding: 0.75rem 1rem;
    margin-bottom: 1rem;
  }
  .stTabs [data-baseweb="tab-list"] { gap: 4px; }
  .stTabs [data-baseweb="tab"] { border-radius: 6px 6px 0 0; }
</style>
""", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------

with st.sidebar:
    st.image("https://img.shields.io/badge/HIPAA-Compliant-0e7c7b?style=flat-square", width=160)
    st.markdown("### ClinicalNote LLMOps")
    st.caption("Masters Research · University of Dayton · 2025")
    st.divider()

    mock_mode = os.getenv("MOCK_MODE", "true").lower() in ("true", "1", "yes")
    st.markdown(
        f"**Mode:** {'🟡 Mock (no real LLM calls)' if mock_mode else '🟢 Live (OpenAI)'}",
    )
    st.caption("Set `MOCK_MODE=false` and `OPENAI_API_KEY` for live mode.")
    st.divider()

    stats = queue_stats()
    pending = stats.get("pending", 0)
    if pending > 0:
        st.warning(f"⚠️ {pending} items pending review")
    else:
        st.success("✅ Review queue clear")

    audit_entries = get_audit_log(limit=1000)
    st.metric("Total operations logged", len(audit_entries))

# ---------------------------------------------------------------------------
# Load sample notes
# ---------------------------------------------------------------------------

SAMPLE_NOTES_PATH = ROOT / "data" / "sample_notes.json"

@st.cache_data
def load_sample_notes() -> list[dict]:
    if SAMPLE_NOTES_PATH.exists():
        with open(SAMPLE_NOTES_PATH) as f:
            return json.load(f)
    return []

SAMPLE_NOTES = load_sample_notes()

# Session state for FHIR bundles
if "fhir_cache" not in st.session_state:
    st.session_state.fhir_cache: dict[str, dict] = {}

if "processing_history" not in st.session_state:
    st.session_state.processing_history: list[dict] = []

# ---------------------------------------------------------------------------
# Tabs
# ---------------------------------------------------------------------------

tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "⚙️ Process Note",
    "🔍 Review Queue",
    "📊 Pipeline Metrics",
    "📦 FHIR Explorer",
    "📋 Audit Log",
])

# ==========================================================================
# TAB 1 — Process Note
# ==========================================================================

with tab1:
    st.header("Process Clinical Note")
    st.caption(
        "Enter or select a clinical note. PHI is scrubbed before any LLM call — "
        "raw text never leaves this system."
    )

    col_in, col_out = st.columns([1, 1], gap="large")

    with col_in:
        st.subheader("Input")

        # Sample selector
        sample_labels = ["(manual input)"] + [
            f"{n['note_id']} — {n['note_type']} ({n['department']})"
            for n in SAMPLE_NOTES
        ]
        selected = st.selectbox("Load sample note", sample_labels, key="sample_select")

        if selected != "(manual input)":
            idx = sample_labels.index(selected) - 1
            default_note = SAMPLE_NOTES[idx]
            note_id_val = default_note["note_id"]
            note_text_val = default_note["note_text"]
            note_type_val = default_note["note_type"]
        else:
            note_id_val = "N999"
            note_text_val = ""
            note_type_val = "clinical_note"

        note_id = st.text_input("Note ID", value=note_id_val)
        note_type = st.selectbox(
            "Note Type",
            ["discharge_summary", "outpatient_visit", "emergency_note",
             "progress_note", "psychiatry_note", "preventive_visit",
             "rheumatology_note", "clinical_note"],
            index=0 if note_type_val == "discharge_summary" else 0,
        )
        note_text = st.text_area("Clinical Note Text", value=note_text_val, height=220)

        process_btn = st.button("▶ Process Note", type="primary", use_container_width=True)

    with col_out:
        st.subheader("Output")

        if process_btn and note_text.strip():
            with st.spinner("Scrubbing PII…"):
                scrub_result = scrub_note(note_text)

            log_operation(
                note_id=note_id,
                operation="pii_scrubbing",
                phi_types_detected=list(scrub_result.phi_types.keys()),
                phi_count=scrub_result.phi_count,
            )

            with st.spinner("Extracting entities (spaCy + LLM)…"):
                extraction = extract_entities(scrub_result.scrubbed_text)

            log_operation(
                note_id=note_id,
                operation="entity_extraction",
                extraction_mode=extraction.extraction_mode,
                entity_count=len(extraction.icd_codes) + len(extraction.medications),
            )

            scoring = score_extractions(extraction)
            bundle = map_to_fhir(note_id, extraction.icd_codes, extraction.medications)
            st.session_state.fhir_cache[note_id] = bundle

            review_count = populate_review_queue(note_id, scoring)
            log_operation(
                note_id=note_id,
                operation="pipeline_complete",
                phi_count=scrub_result.phi_count,
                entity_count=len(extraction.icd_codes) + len(extraction.medications),
            )

            # Store for metrics tab
            st.session_state.processing_history.append({
                "note_id": note_id,
                "note_type": note_type,
                "phi_count": scrub_result.phi_count,
                "phi_types": scrub_result.phi_types,
                "icd_count": len(extraction.icd_codes),
                "med_count": len(extraction.medications),
                "extraction_mode": extraction.extraction_mode,
                "quality_score": scoring.note_quality_score,
                "review_count": review_count,
                "scrubbing_mode": scrub_result.scrubbing_mode,
            })

            # PHI strip
            st.markdown(
                f"""<div class="phi-strip">
                  <strong>🛡 {scrub_result.phi_count} PHI instances removed</strong><br/>
                  <span style="font-size:0.82rem;color:#0e7c7b;">
                    {' · '.join(f'{k}: {v}' for k, v in scrub_result.phi_types.items()) or 'None detected'}
                  </span>
                </div>""",
                unsafe_allow_html=True,
            )

            # Scrubbed text
            with st.expander("📄 Scrubbed Text (sent to LLM)", expanded=True):
                st.code(scrub_result.scrubbed_text, language=None)

            # ICD codes
            st.markdown("**🏥 ICD-10 Codes**")
            if extraction.icd_codes:
                for icd in extraction.icd_codes:
                    conf_class = (
                        "confidence-high" if icd.confidence >= 0.85
                        else "confidence-medium" if icd.confidence >= 0.60
                        else "confidence-low"
                    )
                    st.markdown(
                        f"""<div class="entity-card">
                          <strong>{icd.code}</strong> — {icd.description}<br/>
                          <span class="{conf_class}">{icd.confidence_label} ({icd.confidence:.0%})</span>
                          &nbsp;·&nbsp; <span style="color:#5c7a96;font-size:0.82rem;">source: {icd.source}</span>
                        </div>""",
                        unsafe_allow_html=True,
                    )
            else:
                st.info("No ICD-10 codes extracted.")

            # Medications
            st.markdown("**💊 Medications**")
            if extraction.medications:
                for med in extraction.medications:
                    conf_class = (
                        "confidence-high" if med.confidence >= 0.85
                        else "confidence-medium" if med.confidence >= 0.60
                        else "confidence-low"
                    )
                    st.markdown(
                        f"""<div class="entity-card">
                          <strong>{med.name}</strong> {med.dose} · {med.route} · {med.frequency}<br/>
                          <span class="{conf_class}">{med.confidence_label} ({med.confidence:.0%})</span>
                          &nbsp;·&nbsp; <span style="color:#5c7a96;font-size:0.82rem;">source: {med.source}</span>
                        </div>""",
                        unsafe_allow_html=True,
                    )
            else:
                st.info("No medications extracted.")

            # Review queue notification
            if review_count > 0:
                st.warning(f"⚠️ {review_count} low-confidence item(s) sent to review queue.")
            else:
                st.success("✅ All extractions above confidence threshold.")

            # Quality score
            quality_color = (
                "#16a34a" if scoring.note_quality_score >= 0.8
                else "#d97706" if scoring.note_quality_score >= 0.6
                else "#c0392b"
            )
            st.markdown(
                f"**Note Quality Score:** "
                f"<span style='color:{quality_color};font-weight:700;'>"
                f"{scoring.note_quality_score:.2f} / 1.00 ({scoring.overall_quality})</span>",
                unsafe_allow_html=True,
            )

        elif process_btn:
            st.warning("Please enter clinical note text.")
        else:
            st.info("Select a sample note or enter text, then click **Process Note**.")

# ==========================================================================
# TAB 2 — Review Queue
# ==========================================================================

with tab2:
    st.header("Human-in-the-Loop Review Queue")
    st.caption(
        "Entities with confidence < 0.70 are queued here for clinical review "
        "before inclusion in FHIR output."
    )

    col_stats_l, col_stats_m, col_stats_r = st.columns(3)
    stats = queue_stats()
    col_stats_l.metric("Pending", stats.get("pending", 0))
    col_stats_m.metric("Approved", stats.get("approved", 0))
    col_stats_r.metric("Rejected", stats.get("rejected", 0))

    st.divider()

    status_filter = st.selectbox(
        "Filter by status", ["All", "pending", "approved", "rejected"], key="q_status"
    )

    if st.button("🔄 Refresh Queue", key="refresh_queue"):
        st.rerun()

    items = get_all_items(limit=200)
    if status_filter != "All":
        items = [i for i in items if i["status"] == status_filter]

    if not items:
        st.info("No items in the review queue matching the filter.")
    else:
        for item in items:
            with st.container():
                c1, c2, c3, c4 = st.columns([3, 1, 1, 1])
                with c1:
                    status_emoji = {"pending": "🟡", "approved": "✅", "rejected": "❌"}.get(item["status"], "⬜")
                    st.markdown(
                        f"{status_emoji} **[{item['note_id']}]** {item['entity_type']} · "
                        f"`{item['entity_value'][:70]}{'...' if len(item['entity_value']) > 70 else ''}`"
                    )
                    st.caption(
                        f"Confidence: {item['confidence']:.0%} · Created: {item['created_at'][:19]}"
                    )
                with c2:
                    if item["status"] == "pending":
                        if st.button("✅ Approve", key=f"app_{item['id']}"):
                            approve_item(item["id"], reviewer_id="dashboard-user")
                            st.rerun()
                with c3:
                    if item["status"] == "pending":
                        if st.button("❌ Reject", key=f"rej_{item['id']}"):
                            reject_item(item["id"], reviewer_id="dashboard-user")
                            st.rerun()
                with c4:
                    st.caption(f"ID: {item['id']}")
                st.divider()

# ==========================================================================
# TAB 3 — Pipeline Metrics
# ==========================================================================

with tab3:
    st.header("Pipeline Metrics")

    # Use processing_history for live charts, supplement with audit log for totals
    history = st.session_state.processing_history
    audit_entries = get_audit_log(limit=500)

    if not history:
        st.info(
            "No notes processed in this session yet. "
            "Process some notes in the **Process Note** tab to see charts."
        )
        # Show demo data
        st.markdown("#### Demo metrics (sample data)")
        history = [
            {"note_id": f"N{i:03d}", "note_type": nt, "phi_count": pc,
             "phi_types": pt, "icd_count": ic, "med_count": mc,
             "quality_score": qs, "review_count": rc, "extraction_mode": "mock"}
            for i, (nt, pc, pt, ic, mc, qs, rc) in enumerate([
                ("discharge_summary", 5, {"PERSON":1,"MRN":1,"DOB":1,"PHONE":1,"ADDRESS":1}, 2, 3, 0.91, 0),
                ("outpatient_visit", 4, {"PERSON":1,"MRN":1,"DOB":1,"EMAIL":1}, 2, 2, 0.87, 1),
                ("emergency_note", 3, {"PERSON":1,"MRN":1,"ADDRESS":1}, 1, 2, 0.72, 1),
                ("psychiatry_note", 4, {"PERSON":1,"MRN":1,"DOB":1,"PHONE":1}, 1, 1, 0.93, 0),
                ("outpatient_visit", 3, {"PERSON":1,"MRN":1,"SSN":1}, 3, 3, 0.89, 0),
                ("preventive_visit", 2, {"PERSON":1,"MRN":1}, 3, 2, 0.85, 0),
                ("emergency_note", 5, {"PERSON":1,"MRN":1,"DOB":1,"EMAIL":1,"PHONE":1}, 1, 4, 0.68, 2),
                ("rheumatology_note", 4, {"PERSON":1,"MRN":1,"DOB":1,"EMAIL":1}, 1, 3, 0.90, 0),
                ("progress_note", 3, {"PERSON":1,"MRN":1,"ADDRESS":1}, 2, 3, 0.78, 1),
                ("outpatient_visit", 5, {"PERSON":1,"MRN":1,"DOB":1,"PHONE":1,"EMAIL":1}, 3, 2, 0.82, 0),
            ], 1)
        ]

    df = pd.DataFrame(history)

    row1_l, row1_r = st.columns(2)

    # PHI types distribution
    with row1_l:
        phi_type_counts: dict[str, int] = {}
        for row in history:
            for k, v in (row.get("phi_types") or {}).items():
                phi_type_counts[k] = phi_type_counts.get(k, 0) + v
        if phi_type_counts:
            fig_phi = px.pie(
                names=list(phi_type_counts.keys()),
                values=list(phi_type_counts.values()),
                title="PHI Types Detected",
                color_discrete_sequence=px.colors.sequential.Teal,
                hole=0.4,
            )
            fig_phi.update_layout(height=320, margin=dict(t=40, b=0, l=0, r=0))
            st.plotly_chart(fig_phi, use_container_width=True)

    # Confidence quality score distribution
    with row1_r:
        quality_scores = [r.get("quality_score", 0) for r in history]
        fig_conf = px.histogram(
            x=quality_scores,
            nbins=10,
            title="Note Quality Score Distribution",
            labels={"x": "Quality Score (0-1)", "y": "Count"},
            color_discrete_sequence=["#0e7c7b"],
        )
        fig_conf.update_layout(height=320, margin=dict(t=40, b=0, l=0, r=0))
        fig_conf.add_vline(x=0.85, line_dash="dash", line_color="#16a34a", annotation_text="HIGH threshold")
        fig_conf.add_vline(x=0.60, line_dash="dash", line_color="#d97706", annotation_text="MEDIUM threshold")
        st.plotly_chart(fig_conf, use_container_width=True)

    row2_l, row2_r = st.columns(2)

    # ICD code extraction frequency
    with row2_l:
        all_icd_counts: dict[str, int] = {}
        audit_completions = [e for e in audit_entries if e["operation"] == "entity_extraction"]
        # Supplement with processed history counts
        for row in history:
            all_icd_counts[row.get("note_type", "unknown")] = (
                all_icd_counts.get(row.get("note_type", "unknown"), 0) + row.get("icd_count", 0)
            )
        if all_icd_counts:
            fig_icd = px.bar(
                x=list(all_icd_counts.keys()),
                y=list(all_icd_counts.values()),
                title="ICD Codes Extracted by Note Type",
                labels={"x": "Note Type", "y": "ICD Codes"},
                color=list(all_icd_counts.values()),
                color_continuous_scale="Teal",
            )
            fig_icd.update_layout(height=320, margin=dict(t=40, b=60, l=0, r=0), xaxis_tickangle=-30)
            st.plotly_chart(fig_icd, use_container_width=True)

    # PHI count per note
    with row2_r:
        if history:
            note_ids = [r["note_id"] for r in history]
            phi_counts = [r.get("phi_count", 0) for r in history]
            fig_phi_bar = px.bar(
                x=note_ids,
                y=phi_counts,
                title="PHI Count per Note",
                labels={"x": "Note ID", "y": "PHI Instances Removed"},
                color=phi_counts,
                color_continuous_scale="Teal",
            )
            fig_phi_bar.update_layout(height=320, margin=dict(t=40, b=0, l=0, r=0))
            st.plotly_chart(fig_phi_bar, use_container_width=True)

    # Review queue trend
    st.subheader("Review Queue Status")
    stats = queue_stats()
    status_labels = list(stats.keys()) or ["pending", "approved", "rejected"]
    status_values = list(stats.values()) or [0, 0, 0]
    colors_map = {"pending": "#d97706", "approved": "#16a34a", "rejected": "#c0392b"}
    fig_queue = px.bar(
        x=status_labels,
        y=status_values,
        title="Review Queue Distribution",
        labels={"x": "Status", "y": "Count"},
        color=status_labels,
        color_discrete_map=colors_map,
    )
    fig_queue.update_layout(height=280, showlegend=False, margin=dict(t=40, b=0, l=0, r=0))
    st.plotly_chart(fig_queue, use_container_width=True)

# ==========================================================================
# TAB 4 — FHIR Explorer
# ==========================================================================

with tab4:
    st.header("FHIR R4 Bundle Explorer")
    st.caption("View and validate FHIR Bundles generated from processed notes.")

    if not st.session_state.fhir_cache:
        st.info("No FHIR Bundles generated yet. Process a note in the **Process Note** tab.")
    else:
        note_id_sel = st.selectbox(
            "Select Note",
            list(st.session_state.fhir_cache.keys()),
            key="fhir_sel",
        )
        bundle = st.session_state.fhir_cache.get(note_id_sel, {})

        # Validation
        is_valid, issues = validate_bundle(bundle)
        if is_valid:
            st.success("✅ FHIR Bundle passes R4 structure validation.")
        else:
            for issue in issues:
                st.error(f"❌ {issue}")

        # Summary metrics
        entries = bundle.get("entry", [])
        conditions    = [e for e in entries if e.get("resource", {}).get("resourceType") == "Condition"]
        med_stmts     = [e for e in entries if e.get("resource", {}).get("resourceType") == "MedicationStatement"]

        m1, m2, m3 = st.columns(3)
        m1.metric("Total Resources", len(entries))
        m2.metric("Conditions (ICD)", len(conditions))
        m3.metric("MedicationStatements", len(med_stmts))

        # JSON viewer
        st.markdown("#### Full Bundle JSON")
        bundle_json = bundle_to_json(bundle, indent=2)
        st.code(bundle_json, language="json")

        # Resource detail expanders
        st.markdown("#### Individual Resources")
        for entry in entries:
            resource = entry.get("resource", {})
            rt = resource.get("resourceType", "Unknown")
            rid = resource.get("id", "")[:8]
            with st.expander(f"{rt} · {rid}"):
                st.json(resource)

# ==========================================================================
# TAB 5 — Audit Log
# ==========================================================================

with tab5:
    st.header("Audit Log")
    st.caption(
        "Every pipeline operation is logged here and to AWS S3 (simulated). "
        "No PHI values are stored — only aggregate metadata."
    )

    col_f1, col_f2 = st.columns(2)
    with col_f1:
        filter_note = st.text_input("Filter by Note ID", key="audit_note_filter")
    with col_f2:
        filter_op = st.selectbox(
            "Filter by Operation",
            ["All", "pii_scrubbing", "entity_extraction", "fhir_mapping",
             "pipeline_complete", "approve_review_item", "reject_review_item"],
            key="audit_op_filter",
        )

    if st.button("🔄 Refresh Log", key="refresh_log"):
        st.rerun()

    entries = get_audit_log(note_id=filter_note if filter_note else None, limit=300)
    if filter_op != "All":
        entries = [e for e in entries if e["operation"] == filter_op]

    if not entries:
        st.info("No audit log entries found.")
    else:
        df_audit = pd.DataFrame([
            {
                "Timestamp": e["timestamp"][:19] if e["timestamp"] else "",
                "Note ID": e["note_id"],
                "Operation": e["operation"],
                "PHI Count": e.get("phi_count") or 0,
                "Entities": e.get("entity_count") or 0,
                "Mode": e.get("extraction_mode") or "—",
                "Status": e["status"],
                "Event ID": (e["event_id"] or "")[:8],
            }
            for e in entries
        ])

        st.dataframe(
            df_audit,
            use_container_width=True,
            hide_index=True,
            column_config={
                "Status": st.column_config.TextColumn(
                    "Status",
                    help="success / error",
                ),
            },
        )

        st.caption(f"Showing {len(entries)} entries. S3 bucket: hipaa-audit-logs (simulated).")
