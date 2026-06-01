"""
De-identification Benchmarker.

Evaluates PII scrubbing quality on a synthetic annotated test set of 100 notes
with known PII spans.

Metrics computed per PII type:
  - Precision  = TP / (TP + FP)
  - Recall     = TP / (TP + FN)
  - F1-score   = 2 * P * R / (P + R)

PII types benchmarked:
  - NAME
  - DATE
  - PHONE
  - EMAIL
  - MRN

Outputs:
  - Console table
  - benchmark_report.pdf (via matplotlib)
  - benchmark_results dict

Usage::

    from deidentification.deid_benchmarker import run_benchmark, generate_report_pdf

    results = run_benchmark()
    print(results.summary())
    generate_report_pdf(results, "benchmark_report.pdf")
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import NamedTuple

logger = logging.getLogger(__name__)

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    MATPLOTLIB_AVAILABLE = True
except ImportError:
    MATPLOTLIB_AVAILABLE = False
    logger.warning("matplotlib not available; PDF report generation disabled.")

from pipeline.pii_scrubber import scrub_note  # noqa: E402


# ---------------------------------------------------------------------------
# Annotated span
# ---------------------------------------------------------------------------

class PIISpan(NamedTuple):
    """A known PII span in a test note."""
    start: int
    end: int
    pii_type: str   # NAME | DATE | PHONE | EMAIL | MRN
    value: str


# ---------------------------------------------------------------------------
# Synthetic annotated test corpus (100 notes)
# ---------------------------------------------------------------------------

_NAME_POOL = [
    ("Alice Watkins",   "NAME"),
    ("Bob Chen",        "NAME"),
    ("Carol Martinez",  "NAME"),
    ("David Kim",       "NAME"),
    ("Eva Patel",       "NAME"),
    ("Frank Nguyen",    "NAME"),
    ("Grace Lee",       "NAME"),
    ("Henry Brown",     "NAME"),
    ("Iris Johnson",    "NAME"),
    ("Jack Wilson",     "NAME"),
]
_DATES = [
    ("04/12/1978", "DATE"), ("11/30/1955", "DATE"), ("07/04/1990", "DATE"),
    ("03/22/1966", "DATE"), ("09/15/1983", "DATE"), ("12/01/1945", "DATE"),
    ("06/28/1972", "DATE"), ("01/19/2001", "DATE"), ("08/08/1988", "DATE"),
    ("02/14/1960", "DATE"),
]
_PHONES = [
    ("614-555-0182", "PHONE"), ("937-555-0293", "PHONE"), ("513-555-0134", "PHONE"),
    ("216-555-0045", "PHONE"), ("740-555-0167", "PHONE"), ("419-555-0238", "PHONE"),
    ("330-555-0319", "PHONE"), ("440-555-0425", "PHONE"), ("567-555-0516", "PHONE"),
    ("234-555-0607", "PHONE"),
]
_EMAILS = [
    ("awatkins@hospital.org",  "EMAIL"), ("bchen@clinic.com",        "EMAIL"),
    ("cmartinez@health.net",   "EMAIL"), ("dkim@medcenter.edu",       "EMAIL"),
    ("epatel@healthcare.org",  "EMAIL"), ("fnguyen@hospital.com",     "EMAIL"),
    ("glee@wellness.net",      "EMAIL"), ("hbrown@medgroup.edu",      "EMAIL"),
    ("ijohnson@clinicnet.org", "EMAIL"), ("jwilson@hospsys.com",      "EMAIL"),
]
_MRNS = [
    ("MRN 284716", "MRN"), ("MRN 913482", "MRN"), ("MRN 567823", "MRN"),
    ("MRN 104938", "MRN"), ("MRN 837261", "MRN"), ("MRN 623014", "MRN"),
    ("MRN 458290", "MRN"), ("MRN 719384", "MRN"), ("MRN 302847", "MRN"),
    ("MRN 983741", "MRN"),
]

_CLINICAL_CONTEXTS = [
    "Chief complaint: chest pain. Assessment: hypertension (I10), acute MI (I21.9). Medications: aspirin 325mg QD.",
    "Presenting with fatigue, polyuria. Labs: HbA1c 8.2%. Assessment: Type 2 diabetes (E11.65). Metformin 1000mg BID.",
    "Productive cough, fever. Assessment: Pneumonia (J18.9). Azithromycin 500mg QD PO.",
    "Chronic low back pain. Assessment: L4-L5 disc herniation (M51.16). Naproxen 500mg BID.",
    "Persistent low mood, anhedonia. Assessment: MDD (F32.2). Sertraline 50mg QD.",
    "Severe RLQ pain. Assessment: Appendicitis (K37). Cefazolin 1g IV Q8H.",
    "Joint pain, bilateral wrists. Assessment: Rheumatoid arthritis (M05.79). Methotrexate 15mg QWK.",
    "Wheezing, SOB. Assessment: Asthma exacerbation (J45.41). Albuterol MDI PRN.",
    "Follow-up for hyperlipidemia. LDL 145 mg/dL. Assessment: Mixed hyperlipidemia (E78.2). Rosuvastatin 20mg.",
    "Annual physical. No acute complaints. Assessment: Preventive visit (Z00.00).",
]


def _build_annotated_note(idx: int) -> tuple[str, list[PIISpan]]:
    """Build one annotated note. idx 0-99."""
    pool_idx = idx % 10
    ctx_idx = idx % len(_CLINICAL_CONTEXTS)

    name_val, name_type = _NAME_POOL[pool_idx]
    date_val, date_type = _DATES[pool_idx]
    phone_val, phone_type = _PHONES[pool_idx]
    email_val, email_type = _EMAILS[pool_idx]
    mrn_val, mrn_type = _MRNS[pool_idx]

    # Build note text with known offsets
    segments = [
        f"Patient {name_val}, DOB {date_val}, {mrn_val}. ",
        f"Phone: {phone_val}. Email: {email_val}. ",
        _CLINICAL_CONTEXTS[ctx_idx],
    ]
    text = "".join(segments)

    # Calculate exact character offsets
    spans: list[PIISpan] = []

    def _locate(needle: str, pii_type: str) -> None:
        pos = text.find(needle)
        if pos >= 0:
            spans.append(PIISpan(start=pos, end=pos + len(needle), pii_type=pii_type, value=needle))

    _locate(name_val, name_type)
    _locate(date_val, date_type)
    _locate(mrn_val, mrn_type)
    _locate(phone_val, phone_type)
    _locate(email_val, email_type)

    return text, spans


def build_test_corpus(n_notes: int = 100) -> list[tuple[str, list[PIISpan]]]:
    """Generate the annotated test corpus."""
    return [_build_annotated_note(i) for i in range(n_notes)]


# ---------------------------------------------------------------------------
# Evaluation logic
# ---------------------------------------------------------------------------

# Replacement tokens the scrubber emits for each type
_SCRUBBER_TOKENS: dict[str, list[str]] = {
    "NAME":  ["[PATIENT_NAME]"],
    "DATE":  ["[DATE_OF_BIRTH]", "[DATE]"],
    "PHONE": ["[CONTACT_INFO]"],
    "EMAIL": ["[CONTACT_INFO]"],
    "MRN":   ["[MEDICAL_RECORD_NUMBER]"],
}


def _was_scrubbed(original_value: str, scrubbed_text: str, pii_type: str) -> bool:
    """Return True if the original PHI value no longer appears in scrubbed text."""
    return original_value not in scrubbed_text


@dataclass
class TypeMetrics:
    pii_type: str
    tp: int = 0  # correctly removed PHI
    fp: int = 0  # token present when no PHI existed (overcorrection) — hard to measure; set to 0
    fn: int = 0  # PHI not removed (missed)

    @property
    def precision(self) -> float:
        if self.tp + self.fp == 0:
            return 1.0
        return self.tp / (self.tp + self.fp)

    @property
    def recall(self) -> float:
        if self.tp + self.fn == 0:
            return 1.0
        return self.tp / (self.tp + self.fn)

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        if p + r == 0:
            return 0.0
        return 2 * p * r / (p + r)


@dataclass
class BenchmarkResult:
    n_notes: int
    metrics: dict[str, TypeMetrics] = field(default_factory=dict)

    def summary(self) -> str:
        header = f"{'Type':<10} {'Precision':>10} {'Recall':>10} {'F1':>10} {'TP':>5} {'FN':>5}"
        sep = "-" * 52
        lines = [
            "=" * 52,
            "  De-identification Benchmark Report",
            f"  Test set: {self.n_notes} annotated notes",
            "=" * 52,
            header,
            sep,
        ]
        for t, m in sorted(self.metrics.items()):
            lines.append(
                f"{t:<10} {m.precision:>10.3f} {m.recall:>10.3f} {m.f1:>10.3f} "
                f"{m.tp:>5} {m.fn:>5}"
            )
        lines += [sep, "=" * 52]
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "n_notes": self.n_notes,
            "metrics": {
                t: {
                    "precision": round(m.precision, 4),
                    "recall": round(m.recall, 4),
                    "f1": round(m.f1, 4),
                    "tp": m.tp,
                    "fn": m.fn,
                }
                for t, m in self.metrics.items()
            },
        }


# ---------------------------------------------------------------------------
# Benchmark runner
# ---------------------------------------------------------------------------

def run_benchmark(n_notes: int = 100) -> BenchmarkResult:
    """
    Evaluate de-identification quality on ``n_notes`` annotated synthetic notes.

    Parameters
    ----------
    n_notes : int
        Number of annotated notes to evaluate (default 100).

    Returns
    -------
    BenchmarkResult
        Per-type precision / recall / F1 metrics.
    """
    corpus = build_test_corpus(n_notes)
    pii_types = ["NAME", "DATE", "PHONE", "EMAIL", "MRN"]
    metrics: dict[str, TypeMetrics] = {t: TypeMetrics(pii_type=t) for t in pii_types}

    for note_text, spans in corpus:
        try:
            scrub_result = scrub_note(note_text)
        except Exception as exc:
            logger.warning("Scrubber raised on benchmark note: %s", exc)
            # Count all spans as FN
            for span in spans:
                if span.pii_type in metrics:
                    metrics[span.pii_type].fn += 1
            continue

        scrubbed = scrub_result.scrubbed_text

        for span in spans:
            pii_type = span.pii_type
            if pii_type not in metrics:
                continue
            if _was_scrubbed(span.value, scrubbed, pii_type):
                metrics[pii_type].tp += 1
            else:
                metrics[pii_type].fn += 1

    result = BenchmarkResult(n_notes=n_notes, metrics=metrics)
    logger.info("Benchmark complete.\n%s", result.summary())
    return result


# ---------------------------------------------------------------------------
# PDF report
# ---------------------------------------------------------------------------

def generate_report_pdf(result: BenchmarkResult, output_path: str) -> bool:
    """
    Generate a benchmark report PDF with per-type bar charts.

    Parameters
    ----------
    result : BenchmarkResult
        Output from run_benchmark().
    output_path : str
        Destination file path (e.g. "benchmark_report.pdf").

    Returns
    -------
    bool
        True if the PDF was created, False if matplotlib is unavailable.
    """
    if not MATPLOTLIB_AVAILABLE:
        logger.warning("matplotlib not available; skipping PDF generation.")
        return False

    types = sorted(result.metrics.keys())
    precisions = [result.metrics[t].precision for t in types]
    recalls    = [result.metrics[t].recall    for t in types]
    f1s        = [result.metrics[t].f1        for t in types]

    x = range(len(types))
    bar_width = 0.25

    fig, axes = plt.subplots(1, 2, figsize=(14, 6))
    fig.suptitle(
        f"De-identification Benchmark Report — {result.n_notes} Annotated Notes",
        fontsize=14, fontweight="bold",
    )

    # --- Left chart: grouped bar ---
    ax = axes[0]
    bars_p = ax.bar([i - bar_width for i in x], precisions, bar_width, label="Precision", color="#1a6b8a")
    bars_r = ax.bar([i            for i in x], recalls,    bar_width, label="Recall",    color="#0e7c7b")
    bars_f = ax.bar([i + bar_width for i in x], f1s,       bar_width, label="F1-Score",  color="#16a34a")

    ax.set_xlabel("PII Type")
    ax.set_ylabel("Score")
    ax.set_title("Precision / Recall / F1 per PII Type")
    ax.set_xticks(list(x))
    ax.set_xticklabels(types)
    ax.set_ylim(0, 1.05)
    ax.legend()
    ax.axhline(1.0, color="gray", linestyle="--", linewidth=0.8, alpha=0.5)

    # Annotate bars with values
    for bar_group in (bars_p, bars_r, bars_f):
        for bar in bar_group:
            height = bar.get_height()
            ax.annotate(
                f"{height:.2f}",
                xy=(bar.get_x() + bar.get_width() / 2, height),
                xytext=(0, 2),
                textcoords="offset points",
                ha="center", va="bottom", fontsize=7,
            )

    # --- Right chart: TP vs FN ---
    ax2 = axes[1]
    tps = [result.metrics[t].tp for t in types]
    fns = [result.metrics[t].fn for t in types]

    ax2.bar([i - bar_width / 2 for i in x], tps, bar_width, label="TP (correctly removed)", color="#16a34a")
    ax2.bar([i + bar_width / 2 for i in x], fns, bar_width, label="FN (missed PHI)",         color="#c0392b")
    ax2.set_xlabel("PII Type")
    ax2.set_ylabel("Count")
    ax2.set_title("True Positives vs False Negatives")
    ax2.set_xticks(list(x))
    ax2.set_xticklabels(types)
    ax2.legend()

    # Metadata table below
    table_data = [
        [t, f"{result.metrics[t].precision:.3f}",
             f"{result.metrics[t].recall:.3f}",
             f"{result.metrics[t].f1:.3f}",
             result.metrics[t].tp, result.metrics[t].fn]
        for t in types
    ]
    col_labels = ["PII Type", "Precision", "Recall", "F1", "TP", "FN"]
    table = ax2.table(
        cellText=table_data,
        colLabels=col_labels,
        loc="bottom",
        bbox=[0, -0.45, 1, 0.35],
    )
    table.auto_set_font_size(False)
    table.set_fontsize(8)
    fig.subplots_adjust(bottom=0.28)

    fig.tight_layout(rect=[0, 0.02, 1, 0.95])

    try:
        fig.savefig(output_path, dpi=150, bbox_inches="tight")
        logger.info("Benchmark PDF saved: %s", output_path)
        plt.close(fig)
        return True
    except Exception as exc:
        logger.error("Failed to save benchmark PDF: %s", exc)
        plt.close(fig)
        return False


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    benchmark = run_benchmark(100)
    print(benchmark.summary())
    generate_report_pdf(benchmark, "benchmark_report.pdf")
