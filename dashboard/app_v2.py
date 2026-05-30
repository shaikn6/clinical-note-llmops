"""
Streamlit Dashboard V2 — ClinicalNote LLMOps

Tabs:
  1. Single Note Pipeline (V1) — full PII→NER→ICD-10→FHIR pipeline on one note
  2. Batch Processor          — launch 1 000-note parallel batch with live progress
  3. De-id Benchmark          — precision / recall / F1 per PII type
  4. Audit Log Explorer       — browse the HIPAA structured audit log
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

from pipeline.pii_scrubber import scrub_note
from pipeline.entity_extractor import extract_entities
from pipeline.confidence_scorer import score_extractions
from pipeline.fhir_mapper import validate_bundle
from fhir.fhir_r4_builder import (
    build_full_bundle,
    export_bundle_json,
    validate_r4_bundle,
)
from pipeline.batch_processor import run_batch, generate_synthetic_note
from deidentification.deid_benchmarker import run_benchmark, generate_report_pdf
from audit.hipaa_audit_logger import HIPAAAuditLogger

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="ClinicalNote LLMOps V2",
    page_icon="⚕️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# Session-scoped resources
# ---------------------------------------------------------------------------

AUDIT_DB_PATH = str(ROOT / "hipaa_audit_v2.db")


@st.cache_resource
def _get_audit_logger() -> HIPAAAuditLogger:
    return HIPAAAuditLogger(AUDIT_DB_PATH)


audit_logger = _get_audit_logger()

# ---------------------------------------------------------------------------
# Custom CSS
# ---------------------------------------------------------------------------

st.markdown(
    """
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
  .metric-card {
    background: #f1f5f9;
    border-radius: 10px;
    padding: 1rem;
    text-align: center;
  }
  .stTabs [data-baseweb="tab-list"] { gap: 4px; }
  .stTabs [data-baseweb="tab"] { border-radius: 6px 6px 0 0; }
</style>
""",
    unsafe_allow_html=True,
)

# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------

with st.sidebar:
    st.markdown("### ClinicalNote LLMOps V2")
    st.caption("FHIR R4 · Batch 1K · De-id Benchmark · HIPAA Audit")
    st.divider()

    mock_mode = os.getenv("MOCK_MODE", "true").lower() in ("true", "1", "yes")
    st.markdown(
        f"**Mode:** {'Mock (no real LLM calls)' if mock_mode else 'Live (OpenAI)'}",
    )
    st.caption("Set `MOCK_MODE=false` + `OPENAI_API_KEY` for live mode.")
    st.divider()

    total_audit = audit_logger.count()
    st.metric("Total audit events", total_audit)
    st.divider()
    st.markdown("**V2 features**")
    st.markdown("- Full FHIR R4 (6 resource types)")
    st.markdown("- Batch 1 000 notes (ThreadPoolExecutor)")
    st.markdown("- De-id benchmark (P/R/F1)")
    st.markdown("- HIPAA audit logger (SQLite + PDF)")

# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------

if "fhir_v2_cache" not in st.session_state:
    st.session_state.fhir_v2_cache: dict[str, dict] = {}

if "batch_result" not in st.session_state:
    st.session_state.batch_result = None

if "benchmark_result" not in st.session_state:
    st.session_state.benchmark_result = None

# ---------------------------------------------------------------------------
# Tabs
# ---------------------------------------------------------------------------

tab1, tab2, tab3, tab4 = st.tabs([
    "⚙️ Single Note Pipeline",
    "Batch Processor",
    "De-id Benchmark",
    "📋 Audit Log Explorer",
])

# ==========================================================================
# TAB 1 — Single Note Pipeline (V1 + V2 FHIR full bundle)
# ==========================================================================

with tab1:
    st.header("Single Note Pipeline (V2)")
    st.caption(
        "Full PII scrub → NER → ICD-10 → FHIR R4 Bundle (6 resource types). "
        "No raw PHI is ever passed to the LLM."
    )

    # Load sample notes for dropdown
    SAMPLE_NOTES_PATH = ROOT / "data" / "sample_notes.json"
    sample_notes: list[dict] = []
    if SAMPLE_NOTES_PATH.exists():
        with open(SAMPLE_NOTES_PATH) as f:
            sample_notes = json.load(f)

    # Also offer a few synthetic V2 notes
    v2_synthetic = [generate_synthetic_note(i) for i in range(5)]
    sample_labels = (
        ["(manual input)"]
        + [f"{n['note_id']} — {n['note_type']} ({n.get('department','?')})" for n in sample_notes]
        + [f"[V2-Synthetic] {n['note_id']} — {n['note_type']}" for n in v2_synthetic]
    )
    all_notes = sample_notes + v2_synthetic

    col_in, col_out = st.columns([1, 1], gap="large")

    with col_in:
        st.subheader("Input")
        selected = st.selectbox("Load sample note", sample_labels, key="v2_sample_select")

        if selected != "(manual input)":
            idx = sample_labels.index(selected) - 1
            default_note = all_notes[idx]
            note_id_val = default_note["note_id"]
            note_text_val = default_note.get("note_text", default_note.get("note_text", ""))
            note_type_val = default_note.get("note_type", "clinical_note")
        else:
            note_id_val = "V2-N999"
            note_text_val = ""
            note_type_val = "clinical_note"

        note_id = st.text_input("Note ID", value=note_id_val, key="v2_note_id")
        patient_gender = st.selectbox("Patient Gender (for FHIR)", ["unknown", "male", "female", "other"], key="v2_gender")
        note_text = st.text_area("Clinical Note Text", value=note_text_val, height=220, key="v2_note_text")
        operator_id = st.text_input("Operator ID (for audit)", value="clinician-001", key="v2_operator")

        process_btn = st.button("Run Pipeline", type="primary", use_container_width=True, key="v2_process")

    with col_out:
        st.subheader("Output")

        if process_btn and note_text.strip():
            with st.spinner("Scrubbing PII…"):
                scrub_result = scrub_note(note_text)

            audit_logger.log(
                operator_id=operator_id,
                action="phi_redaction",
                record_id=note_id,
                phi_types=list(scrub_result.phi_types.keys()),
                phi_count=scrub_result.phi_count,
                details={"scrubbing_mode": scrub_result.scrubbing_mode},
            )

            with st.spinner("Extracting entities…"):
                extraction = extract_entities(scrub_result.scrubbed_text)

            audit_logger.log(
                operator_id=operator_id,
                action="entity_extraction",
                record_id=note_id,
                phi_count=0,
                details={
                    "icd_count": len(extraction.icd_codes),
                    "med_count": len(extraction.medications),
                    "mode": extraction.extraction_mode,
                },
            )

            # Build FHIR R4 full bundle
            with st.spinner("Building FHIR R4 Bundle…"):
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
                patient_id_clean = f"patient-{note_id.lower().replace(' ', '-')}"
                try:
                    bundle = build_full_bundle(
                        note_id=note_id,
                        patient_id=patient_id_clean,
                        icd_codes=icd_dicts,
                        medications=med_dicts,
                        patient_gender=patient_gender,
                    )
                    fhir_valid = True
                    fhir_issues: list[str] = []
                except Exception as exc:
                    st.error(f"FHIR build error: {exc}")
                    bundle = {}
                    fhir_valid = False
                    fhir_issues = [str(exc)]

            st.session_state.fhir_v2_cache[note_id] = bundle

            audit_logger.log(
                operator_id=operator_id,
                action="fhir_export",
                record_id=note_id,
                resource_type="Bundle",
                phi_count=0,
                outcome="success" if fhir_valid else "failure",
                details={"total_resources": bundle.get("total", 0)},
            )

            # PHI strip
            st.markdown(
                f"""<div class="phi-strip">
                  <strong>{scrub_result.phi_count} PHI instances removed</strong><br/>
                  <span style="font-size:0.82rem;color:#0e7c7b;">
                    {' · '.join(f'{k}: {v}' for k, v in scrub_result.phi_types.items()) or 'None detected'}
                  </span>
                </div>""",
                unsafe_allow_html=True,
            )

            # Scrubbed text
            with st.expander("Scrubbed Text (sent to LLM)", expanded=False):
                st.code(scrub_result.scrubbed_text, language=None)

            # ICD codes
            st.markdown("**ICD-10 Codes**")
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
                    </div>""",
                    unsafe_allow_html=True,
                )

            # Medications
            st.markdown("**Medications**")
            for med in extraction.medications:
                st.markdown(
                    f"""<div class="entity-card">
                      <strong>{med.name}</strong> {med.dose} · {med.route} · {med.frequency}
                    </div>""",
                    unsafe_allow_html=True,
                )

            # FHIR bundle
            st.divider()
            if fhir_valid:
                st.success(f"FHIR R4 Bundle validated — {bundle.get('total', 0)} resources")
                entries = bundle.get("entry", [])
                res_types: dict[str, int] = {}
                for e in entries:
                    rt = e.get("resource", {}).get("resourceType", "Unknown")
                    res_types[rt] = res_types.get(rt, 0) + 1
                st.json(res_types)
                with st.expander("Full FHIR R4 Bundle JSON"):
                    st.code(export_bundle_json(bundle), language="json")
            else:
                for issue in fhir_issues:
                    st.error(issue)

        elif process_btn:
            st.warning("Please enter clinical note text.")
        else:
            st.info("Select a sample or enter a note, then click **Run Pipeline**.")

# ==========================================================================
# TAB 2 — Batch Processor
# ==========================================================================

with tab2:
    st.header("Batch Processor")
    st.caption(
        "Process up to 1 000 synthetic clinical notes in parallel. "
        "Pipeline: PII scrub → NER → ICD-10 → FHIR R4 Bundle. "
        "ThreadPoolExecutor concurrency with live progress."
    )

    col_cfg, col_res = st.columns([1, 2], gap="large")

    with col_cfg:
        n_notes = st.slider("Number of notes", min_value=10, max_value=1000, value=100, step=10, key="batch_n")
        max_workers = st.slider("Parallel workers", min_value=1, max_value=16, value=4, key="batch_workers")
        batch_seed = st.number_input("RNG seed (reproducibility)", value=42, key="batch_seed")
        st.caption(f"Estimated time: ~{max(1, n_notes // max(1, max_workers) // 10)}s")

        run_batch_btn = st.button("Run Batch", type="primary", use_container_width=True, key="run_batch")

    with col_res:
        if run_batch_btn:
            progress_bar = st.progress(0, text="Initialising batch…")
            status_text = st.empty()
            t_start = time.perf_counter()

            # Run batch (show_progress=False to avoid tqdm in Streamlit)
            with st.spinner(f"Processing {n_notes} notes with {max_workers} workers…"):
                batch_result = run_batch(
                    n_notes=n_notes,
                    max_workers=max_workers,
                    seed=int(batch_seed),
                    show_progress=False,
                )

            progress_bar.progress(1.0, text="Complete!")
            st.session_state.batch_result = batch_result

            audit_logger.log(
                operator_id="batch-service",
                action="batch_process",
                record_id=f"BATCH-{int(batch_seed)}-{n_notes}",
                resource_type="Batch",
                phi_count=sum(r.get("phi_count", 0) for r in batch_result.results),
                details=batch_result.to_dict(),
            )

        if st.session_state.batch_result is not None:
            br = st.session_state.batch_result

            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Notes Processed", br.n_requested)
            m2.metric("Succeeded", br.n_success)
            m3.metric("Errors", br.n_error)
            m4.metric("Throughput", f"{br.notes_per_second:.1f} notes/sec")

            st.metric("Total Time", f"{br.total_elapsed_sec:.2f}s")

            # Results table
            if br.results:
                df = pd.DataFrame([
                    {
                        "Note ID": r.get("note_id", ""),
                        "Type": r.get("note_type", ""),
                        "PHI": r.get("phi_count", 0),
                        "ICD": r.get("icd_count", 0),
                        "Meds": r.get("med_count", 0),
                        "FHIR Resources": r.get("fhir_resource_count", 0),
                        "ms": round(r.get("elapsed_ms", 0), 1),
                        "Status": r.get("status", ""),
                    }
                    for r in br.results[:200]
                ])
                st.dataframe(df, use_container_width=True, hide_index=True)

                # Throughput chart over time
                elapsed_list = [r.get("elapsed_ms", 0) for r in br.results]
                fig_tp = px.histogram(
                    x=elapsed_list,
                    nbins=40,
                    title="Per-Note Processing Time Distribution (ms)",
                    labels={"x": "Time (ms)", "y": "Note count"},
                    color_discrete_sequence=["#0e7c7b"],
                )
                st.plotly_chart(fig_tp, use_container_width=True)
        else:
            st.info("Configure the batch above and click **Run Batch**.")

# ==========================================================================
# TAB 3 — De-id Benchmark
# ==========================================================================

with tab3:
    st.header("De-identification Benchmark")
    st.caption(
        "Evaluates PII scrubber precision, recall, and F1 on a synthetic annotated "
        "test set (up to 100 notes with known PHI spans). "
        "Reports per-type metrics for NAME, DATE, PHONE, EMAIL, MRN."
    )

    col_bench_cfg, col_bench_res = st.columns([1, 2], gap="large")

    with col_bench_cfg:
        bench_n = st.slider("Test set size", min_value=10, max_value=100, value=100, step=10, key="bench_n")
        pdf_out = st.text_input("PDF output path", value="benchmark_report.pdf", key="bench_pdf")
        run_bench_btn = st.button("Run Benchmark", type="primary", use_container_width=True, key="run_bench")

    with col_bench_res:
        if run_bench_btn:
            with st.spinner(f"Running benchmark on {bench_n} annotated notes…"):
                benchmark = run_benchmark(bench_n)
                st.session_state.benchmark_result = benchmark

            pdf_saved = generate_report_pdf(benchmark, pdf_out)
            if pdf_saved:
                st.success(f"PDF report saved: {pdf_out}")

            audit_logger.log(
                operator_id="benchmark-service",
                action="phi_access",
                record_id="BENCHMARK",
                resource_type="Benchmark",
                phi_count=0,
                details={"n_notes": bench_n},
            )

        if st.session_state.benchmark_result is not None:
            bm = st.session_state.benchmark_result

            # Metrics table
            rows = []
            for pii_type, m in sorted(bm.metrics.items()):
                rows.append({
                    "PII Type":  pii_type,
                    "Precision": round(m.precision, 3),
                    "Recall":    round(m.recall, 3),
                    "F1":        round(m.f1, 3),
                    "TP":        m.tp,
                    "FN":        m.fn,
                })

            df_bench = pd.DataFrame(rows)
            st.dataframe(df_bench, use_container_width=True, hide_index=True)

            # Grouped bar chart
            fig_bench = go.Figure()
            for metric, color in [("Precision", "#1a6b8a"), ("Recall", "#0e7c7b"), ("F1", "#16a34a")]:
                fig_bench.add_trace(go.Bar(
                    name=metric,
                    x=df_bench["PII Type"],
                    y=df_bench[metric],
                    marker_color=color,
                    text=df_bench[metric].apply(lambda v: f"{v:.3f}"),
                    textposition="outside",
                ))
            fig_bench.update_layout(
                barmode="group",
                title="De-identification Quality: Precision / Recall / F1 per PII Type",
                yaxis=dict(range=[0, 1.1]),
                height=400,
            )
            st.plotly_chart(fig_bench, use_container_width=True)

            # Detailed summary
            with st.expander("Full Benchmark Summary"):
                st.code(bm.summary(), language=None)
        else:
            st.info("Click **Run Benchmark** to evaluate the PII scrubber.")

# ==========================================================================
# TAB 4 — Audit Log Explorer
# ==========================================================================

with tab4:
    st.header("HIPAA Audit Log Explorer")
    st.caption(
        "Structured audit trail of every PHI access and redaction. "
        "SQLite-backed. PDF report available. No raw PHI values stored."
    )

    col_f1, col_f2, col_f3 = st.columns(3)
    with col_f1:
        filter_op = st.text_input("Filter by Operator ID", key="audit_op_filter_v2")
    with col_f2:
        filter_action = st.selectbox(
            "Filter by Action",
            ["All"] + sorted(["phi_access", "phi_redaction", "entity_extraction",
                              "fhir_export", "batch_process", "record_view",
                              "review_approve", "review_reject", "pipeline_complete"]),
            key="audit_action_filter_v2",
        )
    with col_f3:
        filter_record = st.text_input("Filter by Record ID", key="audit_record_filter_v2")

    col_btn1, col_btn2, col_btn3 = st.columns(3)
    with col_btn1:
        if st.button("Refresh", key="audit_refresh_v2"):
            st.rerun()
    with col_btn2:
        pdf_audit_path = st.text_input("Audit PDF path", value="hipaa_audit_report.pdf", key="audit_pdf_path")
    with col_btn3:
        if st.button("Export PDF", key="audit_export_pdf"):
            with st.spinner("Generating HIPAA audit report PDF…"):
                ok = audit_logger.generate_report_pdf(pdf_audit_path)
            if ok:
                st.success(f"PDF saved: {pdf_audit_path}")
            else:
                st.error("PDF generation failed (matplotlib required).")

    # Fetch entries
    entries = audit_logger.get_entries(
        operator_id=filter_op or None,
        action=filter_action if filter_action != "All" else None,
        record_id=filter_record or None,
        limit=500,
    )

    # Operator / action summary metrics
    op_summary = audit_logger.operator_summary()
    phi_summary = audit_logger.phi_type_summary()

    total_col, op_col, phi_col = st.columns(3)
    total_col.metric("Total Events", audit_logger.count())
    op_col.metric("Unique Operators", len(op_summary))
    phi_col.metric("PHI Types Logged", len(phi_summary))

    if not entries:
        st.info("No audit entries match the current filters.")
    else:
        df_audit = pd.DataFrame([
            {
                "Timestamp":    e.timestamp[:19],
                "Operator":     e.operator_id,
                "Action":       e.action,
                "Record ID":    e.record_id,
                "Resource":     e.resource_type,
                "PHI Types":    ", ".join(e.phi_types) or "—",
                "PHI Count":    e.phi_count,
                "Outcome":      e.outcome,
                "Event ID":     e.event_id[:8],
            }
            for e in entries
        ])

        st.dataframe(
            df_audit,
            use_container_width=True,
            hide_index=True,
            column_config={
                "Outcome": st.column_config.TextColumn("Outcome"),
            },
        )
        st.caption(f"Showing {len(entries)} entries (max 500).")

        # PHI type breakdown chart
        if phi_summary:
            fig_phi = px.bar(
                x=list(phi_summary.keys()),
                y=list(phi_summary.values()),
                title="PHI Type Frequency Across All Audit Events",
                labels={"x": "PHI Type", "y": "Event Count"},
                color=list(phi_summary.values()),
                color_continuous_scale="Teal",
            )
            fig_phi.update_layout(height=300, showlegend=False)
            st.plotly_chart(fig_phi, use_container_width=True)
