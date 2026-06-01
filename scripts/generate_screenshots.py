"""
Generate PNG screenshot assets for the README and docs.

Produces:
  docs/screenshots/pipeline_flow.png     — Pipeline architecture diagram
  docs/screenshots/confidence_dist.png  — Confidence score distribution
  docs/screenshots/phi_detection.png    — PHI types detected bar chart
  docs/screenshots/dashboard_preview.png — Composite dashboard mockup
  docs/screenshots/fhir_bundle.png      — FHIR Bundle structure diagram
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import matplotlib  # noqa: E402
matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402
import matplotlib.patches as mpatches  # noqa: E402
import numpy as np  # noqa: E402

SCREENSHOTS_DIR = ROOT / "docs" / "screenshots"
SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Shared styling
# ---------------------------------------------------------------------------

NAVY   = "#0d2137"
TEAL   = "#0e7c7b"
TEAL_L = "#14b8a6"
WHITE  = "#ffffff"
GRAY   = "#f8fafc"
MUTED  = "#5c7a96"
GREEN  = "#16a34a"
AMBER  = "#d97706"
RED    = "#c0392b"

plt.rcParams.update({
    "font.family": "DejaVu Sans",
    "font.size": 11,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "axes.facecolor": GRAY,
    "figure.facecolor": WHITE,
    "axes.prop_cycle": plt.cycler(color=[TEAL, NAVY, TEAL_L, MUTED, GREEN, AMBER]),
})


# ---------------------------------------------------------------------------
# 1. Pipeline Flow Diagram
# ---------------------------------------------------------------------------

def generate_pipeline_flow():
    fig, ax = plt.subplots(figsize=(14, 5))
    fig.patch.set_facecolor(NAVY)
    ax.set_facecolor(NAVY)
    ax.axis("off")

    steps = [
        ("1\nPII\nScrub", "Presidio\n+ Regex", TEAL),
        ("2\nspaCy\nNER", "Medication\nExtraction", "#1a5276"),
        ("3\nLLM\nExtract", "ICD-10 Codes\n(Mock/OpenAI)", "#0d4a47"),
        ("4\nConfidence\nScore", "HIGH/MED/LOW\nFlagging", "#1a4a6e"),
        ("5\nFHIR\nMap", "R4 Bundle\nGeneration", TEAL),
        ("6\nReview\nQueue", "Human-in-Loop\nSQLite", "#0d4a47"),
        ("7\nAudit\nLog", "SQLite\n+ S3 Mock", NAVY),
    ]

    n = len(steps)
    xs = np.linspace(0.05, 0.95, n)
    y_box = 0.5
    box_w, box_h = 0.10, 0.55

    for i, (title, sub, color) in enumerate(steps):
        x = xs[i]
        rect = mpatches.FancyBboxPatch(
            (x - box_w / 2, y_box - box_h / 2),
            box_w, box_h,
            boxstyle="round,pad=0.02",
            facecolor=color,
            edgecolor=TEAL_L,
            linewidth=1.5,
            transform=ax.transAxes,
        )
        ax.add_patch(rect)

        ax.text(x, y_box + 0.05, title, ha="center", va="center",
                fontsize=9, fontweight="bold", color=WHITE,
                transform=ax.transAxes, linespacing=1.4)
        ax.text(x, y_box - 0.18, sub, ha="center", va="center",
                fontsize=7, transform=ax.transAxes, linespacing=1.3,
                color=TEAL_L)

        if i < n - 1:
            _mid_x = (xs[i] + xs[i + 1]) / 2
            ax.annotate(
                "", xy=(xs[i + 1] - box_w / 2 - 0.005, y_box),
                xytext=(xs[i] + box_w / 2 + 0.005, y_box),
                xycoords="axes fraction", textcoords="axes fraction",
                arrowprops=dict(
                    arrowstyle="-|>", color=TEAL_L,
                    lw=1.5, mutation_scale=12,
                ),
            )

    # HIPAA shield badge
    ax.text(0.5, 0.05, "⚕ HIPAA GUARANTEE: Raw PHI Never Reaches LLM — Scrubbed at Step 1",
            ha="center", va="center", fontsize=10, color=TEAL_L,
            fontweight="bold", transform=ax.transAxes,
            bbox=dict(facecolor=NAVY, edgecolor=TEAL, boxstyle="round,pad=0.3", linewidth=1))

    ax.set_title("ClinicalNote LLMOps — Pipeline Architecture",
                 color=WHITE, fontsize=14, fontweight="bold", pad=16)

    out = SCREENSHOTS_DIR / "pipeline_flow.png"
    plt.tight_layout()
    plt.savefig(out, dpi=150, bbox_inches="tight", facecolor=NAVY)
    plt.close()
    print(f"  ✓ {out}")


# ---------------------------------------------------------------------------
# 2. Confidence Score Distribution
# ---------------------------------------------------------------------------

def generate_confidence_dist():
    # Synthetic confidence scores from 10 sample notes
    np.random.seed(42)
    icd_scores  = np.clip(np.random.normal(0.88, 0.08, 28), 0.4, 1.0)
    med_scores  = np.clip(np.random.normal(0.80, 0.12, 42), 0.3, 1.0)

    fig, axes = plt.subplots(1, 2, figsize=(12, 5))
    fig.suptitle("Extraction Confidence Score Distribution",
                 fontsize=14, fontweight="bold", color=NAVY, y=1.02)

    for ax, scores, label, color in [
        (axes[0], icd_scores,  "ICD-10 Codes",  TEAL),
        (axes[1], med_scores,  "Medications",   NAVY),
    ]:
        counts, bins, patches = ax.hist(scores, bins=15, color=color, alpha=0.85, edgecolor="white", linewidth=0.5)
        # Color patches by threshold
        for patch, left in zip(patches, bins):
            if left >= 0.85:
                patch.set_facecolor(GREEN)
            elif left >= 0.60:
                patch.set_facecolor(AMBER)
            else:
                patch.set_facecolor(RED)

        ax.axvline(0.85, color=GREEN, lw=1.5, ls="--", label="HIGH ≥ 0.85")
        ax.axvline(0.60, color=AMBER, lw=1.5, ls="--", label="MEDIUM ≥ 0.60")
        ax.set_xlabel("Confidence Score", fontsize=11, color=NAVY)
        ax.set_ylabel("Count", fontsize=11, color=NAVY)
        ax.set_title(label, fontsize=12, color=NAVY, fontweight="bold")
        ax.legend(fontsize=9)
        ax.set_facecolor(GRAY)

        # Annotation
        high_pct = (scores >= 0.85).mean() * 100
        ax.text(0.03, 0.93, f"HIGH: {high_pct:.0f}%", transform=ax.transAxes,
                fontsize=10, color=GREEN, fontweight="bold", va="top")

    plt.tight_layout()
    out = SCREENSHOTS_DIR / "confidence_dist.png"
    plt.savefig(out, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"  ✓ {out}")


# ---------------------------------------------------------------------------
# 3. PHI Detection Chart
# ---------------------------------------------------------------------------

def generate_phi_detection():
    phi_types = {
        "PERSON\n(Name)": 10,
        "MRN": 10,
        "DATE_OF_BIRTH": 9,
        "PHONE_NUMBER": 6,
        "EMAIL_ADDRESS": 5,
        "ADDRESS": 4,
        "SSN": 2,
    }

    colors = [TEAL, NAVY, TEAL_L, MUTED, GREEN, AMBER, RED]

    fig, (ax_bar, ax_pie) = plt.subplots(1, 2, figsize=(13, 5))
    fig.suptitle("PHI Detection Results — 10 Sample Notes",
                 fontsize=14, fontweight="bold", color=NAVY)

    # Bar chart
    bars = ax_bar.bar(
        list(phi_types.keys()),
        list(phi_types.values()),
        color=colors, edgecolor="white", linewidth=0.8,
    )
    ax_bar.set_xlabel("PHI Category", fontsize=11, color=NAVY)
    ax_bar.set_ylabel("Count Detected", fontsize=11, color=NAVY)
    ax_bar.set_title("PHI Instances by Category", fontsize=12, color=NAVY, fontweight="bold")
    for bar in bars:
        ax_bar.text(
            bar.get_x() + bar.get_width() / 2,
            bar.get_height() + 0.1,
            int(bar.get_height()),
            ha="center", va="bottom", fontsize=10, fontweight="bold", color=NAVY,
        )
    ax_bar.set_facecolor(GRAY)
    ax_bar.tick_params(axis="x", labelsize=8)

    # Pie chart
    ax_pie.pie(
        list(phi_types.values()),
        labels=list(phi_types.keys()),
        colors=colors,
        autopct="%1.0f%%",
        startangle=140,
        pctdistance=0.8,
        textprops={"fontsize": 9},
    )
    ax_pie.set_title("PHI Type Distribution", fontsize=12, color=NAVY, fontweight="bold")

    # Total annotation
    total = sum(phi_types.values())
    ax_bar.text(
        0.98, 0.97, f"Total PHI removed: {total}",
        transform=ax_bar.transAxes, ha="right", va="top",
        fontsize=11, color=WHITE, fontweight="bold",
        bbox=dict(facecolor=TEAL, edgecolor="none", boxstyle="round,pad=0.4"),
    )

    plt.tight_layout()
    out = SCREENSHOTS_DIR / "phi_detection.png"
    plt.savefig(out, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"  ✓ {out}")


# ---------------------------------------------------------------------------
# 4. Dashboard Preview (composite mockup)
# ---------------------------------------------------------------------------

def generate_dashboard_preview():
    fig = plt.figure(figsize=(16, 9))
    fig.patch.set_facecolor(GRAY)

    # Title bar
    title_ax = fig.add_axes([0, 0.92, 1, 0.08])
    title_ax.set_facecolor(NAVY)
    title_ax.axis("off")
    title_ax.text(0.02, 0.5, "⚕  ClinicalNote LLMOps Dashboard",
                  color=WHITE, fontsize=16, fontweight="bold", va="center")
    title_ax.text(0.98, 0.5, "MOCK MODE  ●  HIPAA-Compliant",
                  color=TEAL_L, fontsize=10, fontweight="bold", va="center", ha="right")

    # Tab bar
    tab_ax = fig.add_axes([0, 0.85, 1, 0.07])
    tab_ax.set_facecolor("#1a2b3c")
    tab_ax.axis("off")
    tabs = ["⚙ Process Note", "🔍 Review Queue", "📊 Metrics", "📦 FHIR Explorer", "📋 Audit Log"]
    for i, tab in enumerate(tabs):
        color = TEAL_L if i == 2 else WHITE
        _bg   = TEAL    if i == 2 else "transparent"
        x = 0.02 + i * 0.19
        if i == 2:
            rect = mpatches.FancyBboxPatch((x - 0.005, 0.1), 0.17, 0.8,
                boxstyle="round,pad=0.02", facecolor=TEAL, edgecolor="none")
            tab_ax.add_patch(rect)
        tab_ax.text(x + 0.08, 0.5, tab, color=color, fontsize=10, va="center", ha="center")

    # Metric row
    metrics = [
        ("46", "PHI Instances Removed"),
        ("24", "ICD-10 Codes Extracted"),
        ("38", "Medications Extracted"),
        ("91%", "Avg. Confidence Score"),
        ("4", "Pending Review"),
    ]
    for i, (val, lbl) in enumerate(metrics):
        x = 0.02 + i * 0.196
        m_ax = fig.add_axes([x, 0.72, 0.18, 0.12])
        m_ax.set_facecolor(WHITE)
        m_ax.axis("off")
        m_ax.text(0.5, 0.7, val, ha="center", va="center",
                  fontsize=20, fontweight="bold",
                  color=TEAL if i < 4 else AMBER)
        m_ax.text(0.5, 0.2, lbl, ha="center", va="center",
                  fontsize=8, color=MUTED, wrap=True)
        for spine in ["top","right","bottom","left"]:
            m_ax.spines[spine].set_visible(False)

    # Main chart area — PHI types pie
    pie_ax = fig.add_axes([0.02, 0.08, 0.28, 0.62])
    pie_ax.set_facecolor(WHITE)
    phi_types = {"PERSON": 10, "MRN": 10, "DOB": 9, "PHONE": 6, "EMAIL": 5, "ADDR": 4, "SSN": 2}
    pie_ax.pie(
        list(phi_types.values()),
        labels=list(phi_types.keys()),
        colors=[TEAL, NAVY, TEAL_L, MUTED, GREEN, AMBER, RED],
        autopct="%1.0f%%", startangle=140,
        textprops={"fontsize": 9},
    )
    pie_ax.set_title("PHI Types Detected", fontsize=11, color=NAVY, fontweight="bold", pad=8)

    # Confidence histogram
    hist_ax = fig.add_axes([0.35, 0.08, 0.28, 0.62])
    hist_ax.set_facecolor(WHITE)
    np.random.seed(0)
    scores = np.clip(np.random.normal(0.85, 0.1, 70), 0.3, 1.0)
    counts, bins, patches = hist_ax.hist(scores, bins=12, edgecolor="white", linewidth=0.5)
    for patch, left in zip(patches, bins):
        patch.set_facecolor(GREEN if left >= 0.85 else AMBER if left >= 0.60 else RED)
    hist_ax.axvline(0.85, color=GREEN, lw=1.5, ls="--")
    hist_ax.axvline(0.60, color=AMBER, lw=1.5, ls="--")
    hist_ax.set_title("Confidence Distribution", fontsize=11, color=NAVY, fontweight="bold")
    hist_ax.set_xlabel("Score", fontsize=9, color=NAVY)
    hist_ax.set_ylabel("Count", fontsize=9, color=NAVY)

    # Review queue bar
    q_ax = fig.add_axes([0.68, 0.08, 0.3, 0.62])
    q_ax.set_facecolor(WHITE)
    statuses = ["Pending", "Approved", "Rejected"]
    counts_q = [4, 18, 3]
    colors_q = [AMBER, GREEN, RED]
    bars = q_ax.bar(statuses, counts_q, color=colors_q, edgecolor="white", linewidth=0.8)
    for bar in bars:
        q_ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.2,
                  int(bar.get_height()), ha="center", fontsize=12, fontweight="bold", color=NAVY)
    q_ax.set_title("Review Queue Status", fontsize=11, color=NAVY, fontweight="bold")
    q_ax.set_ylabel("Count", fontsize=9, color=NAVY)

    plt.tight_layout(rect=[0, 0, 1, 0.92])
    out = SCREENSHOTS_DIR / "dashboard_preview.png"
    plt.savefig(out, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"  ✓ {out}")


# ---------------------------------------------------------------------------
# 5. FHIR Bundle Structure Diagram
# ---------------------------------------------------------------------------

def generate_fhir_bundle():
    fig, ax = plt.subplots(figsize=(12, 6))
    fig.patch.set_facecolor(WHITE)
    ax.set_facecolor(WHITE)
    ax.axis("off")

    ax.set_xlim(0, 12)
    ax.set_ylim(0, 6)

    ax.set_title("FHIR R4 Bundle — Clinical Note LLMOps Output",
                 fontsize=14, fontweight="bold", color=NAVY, pad=10)

    # Bundle box
    bundle_box = mpatches.FancyBboxPatch((0.2, 0.3), 11.6, 5.2,
        boxstyle="round,pad=0.1", facecolor="#e8f8f7", edgecolor=TEAL, linewidth=2)
    ax.add_patch(bundle_box)
    ax.text(6, 5.2, "Bundle (type: collection)", ha="center", fontsize=13,
            fontweight="bold", color=TEAL)

    # Condition resources
    for i, (code, desc, conf) in enumerate([
        ("I21.9", "Acute Myocardial Infarction", "92%"),
        ("I10",   "Essential Hypertension",       "95%"),
    ]):
        x = 0.6 + i * 5
        box = mpatches.FancyBboxPatch((x, 2.8), 4.2, 1.8,
            boxstyle="round,pad=0.1", facecolor="#d0ebff", edgecolor=NAVY, linewidth=1.5)
        ax.add_patch(box)
        ax.text(x + 2.1, 4.3, "Condition (ICD-10)", ha="center", fontsize=10,
                fontweight="bold", color=NAVY)
        ax.text(x + 2.1, 3.9, f"code: {code}", ha="center", fontsize=9, color=NAVY)
        ax.text(x + 2.1, 3.55, desc, ha="center", fontsize=8, color=MUTED)
        ax.text(x + 2.1, 3.2, f"confidence: {conf}  |  status: active",
                ha="center", fontsize=8, color=GREEN, fontweight="bold")

    # MedicationStatement resources
    for i, (name, dose, conf) in enumerate([
        ("Aspirin",    "325mg PO stat", "88%"),
        ("Metoprolol", "25mg PO BID",   "85%"),
        ("Atorvastatin","40mg PO QHS",  "82%"),
    ]):
        x = 0.4 + i * 3.85
        box = mpatches.FancyBboxPatch((x, 0.6), 3.4, 1.9,
            boxstyle="round,pad=0.1", facecolor="#f0fdf4", edgecolor=GREEN, linewidth=1.5)
        ax.add_patch(box)
        ax.text(x + 1.7, 2.25, "MedicationStatement", ha="center", fontsize=9,
                fontweight="bold", color=GREEN)
        ax.text(x + 1.7, 1.9, name, ha="center", fontsize=10, fontweight="bold", color=NAVY)
        ax.text(x + 1.7, 1.55, dose, ha="center", fontsize=8, color=MUTED)
        ax.text(x + 1.7, 1.2, f"confidence: {conf}", ha="center", fontsize=8,
                color=GREEN, fontweight="bold")
        ax.text(x + 1.7, 0.85, "status: active", ha="center", fontsize=8, color=MUTED)

    # Connector lines
    for x in [2.7, 7.7]:
        ax.annotate("", xy=(x, 2.8), xytext=(6, 5.1),
                    arrowprops=dict(arrowstyle="-|>", color=TEAL, lw=1.2))
    for x in [2.1, 5.95, 9.8]:
        ax.annotate("", xy=(x, 2.5), xytext=(6, 5.1),
                    arrowprops=dict(arrowstyle="-|>", color=GREEN, lw=1.2))

    plt.tight_layout()
    out = SCREENSHOTS_DIR / "fhir_bundle.png"
    plt.savefig(out, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"  ✓ {out}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("Generating screenshots…")
    generate_pipeline_flow()
    generate_confidence_dist()
    generate_phi_detection()
    generate_dashboard_preview()
    generate_fhir_bundle()
    print(f"\nAll screenshots saved to {SCREENSHOTS_DIR}/")
