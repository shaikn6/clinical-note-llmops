"""
Tests for deidentification/deid_benchmarker.py

Covers:
  - build_test_corpus: size, annotation presence, PHI values in notes
  - PIISpan: named-tuple fields
  - TypeMetrics: precision / recall / F1 computations
  - run_benchmark: structure, metric types, recall correctness
  - BenchmarkResult: summary / to_dict
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from deidentification.deid_benchmarker import (
    build_test_corpus,
    run_benchmark,
    BenchmarkResult,
    TypeMetrics,
    PIISpan,
    _was_scrubbed,
    _build_annotated_note,
)


# ---------------------------------------------------------------------------
# build_test_corpus
# ---------------------------------------------------------------------------

class TestBuildTestCorpus:
    def test_corpus_size(self):
        corpus = build_test_corpus(100)
        assert len(corpus) == 100

    def test_each_item_is_tuple_of_two(self):
        corpus = build_test_corpus(5)
        for item in corpus:
            assert len(item) == 2

    def test_note_text_is_string(self):
        corpus = build_test_corpus(5)
        for text, _ in corpus:
            assert isinstance(text, str)

    def test_spans_are_list(self):
        corpus = build_test_corpus(5)
        for _, spans in corpus:
            assert isinstance(spans, list)

    def test_each_span_is_piispan(self):
        corpus = build_test_corpus(5)
        for _, spans in corpus:
            for span in spans:
                assert isinstance(span, PIISpan)

    def test_spans_have_pii_values_in_note(self):
        corpus = build_test_corpus(5)
        for text, spans in corpus:
            for span in spans:
                # The annotated value must appear in the original note
                assert span.value in text, (
                    f"Span value '{span.value}' not found in note text"
                )

    def test_pii_types_cover_expected_categories(self):
        corpus = build_test_corpus(20)
        all_types = {span.pii_type for _, spans in corpus for span in spans}
        expected = {"NAME", "DATE", "PHONE", "EMAIL", "MRN"}
        assert expected.issubset(all_types), f"Missing PII types: {expected - all_types}"

    def test_smaller_corpus_size(self):
        corpus = build_test_corpus(10)
        assert len(corpus) == 10


# ---------------------------------------------------------------------------
# TypeMetrics
# ---------------------------------------------------------------------------

class TestTypeMetrics:
    def test_precision_perfect(self):
        m = TypeMetrics(pii_type="NAME", tp=10, fp=0, fn=0)
        assert m.precision == pytest.approx(1.0)

    def test_recall_perfect(self):
        m = TypeMetrics(pii_type="NAME", tp=10, fp=0, fn=0)
        assert m.recall == pytest.approx(1.0)

    def test_f1_perfect(self):
        m = TypeMetrics(pii_type="NAME", tp=10, fp=0, fn=0)
        assert m.f1 == pytest.approx(1.0)

    def test_recall_with_false_negatives(self):
        m = TypeMetrics(pii_type="MRN", tp=8, fp=0, fn=2)
        assert m.recall == pytest.approx(0.8, abs=0.001)

    def test_f1_zero_when_all_fn(self):
        m = TypeMetrics(pii_type="PHONE", tp=0, fp=0, fn=5)
        # recall = 0, f1 = 0
        assert m.f1 == pytest.approx(0.0)

    def test_precision_zero_denominantor_returns_one(self):
        # TP=0, FP=0 → precision defaults to 1.0
        m = TypeMetrics(pii_type="EMAIL", tp=0, fp=0, fn=0)
        assert m.precision == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# _was_scrubbed
# ---------------------------------------------------------------------------

class TestWasScrubbed:
    def test_scrubbed_when_value_absent(self):
        assert _was_scrubbed("John Smith", "Hello [PATIENT_NAME], MRN 123.", "NAME") is True

    def test_not_scrubbed_when_value_present(self):
        assert _was_scrubbed("John Smith", "Hello John Smith, MRN 123.", "NAME") is False

    def test_mrn_scrubbed(self):
        assert _was_scrubbed("MRN 789234", "Patient [MEDICAL_RECORD_NUMBER].", "MRN") is True

    def test_email_scrubbed(self):
        assert _was_scrubbed("user@hospital.com", "Contact [CONTACT_INFO].", "EMAIL") is True


# ---------------------------------------------------------------------------
# run_benchmark
# ---------------------------------------------------------------------------

class TestRunBenchmark:
    def test_returns_benchmark_result(self):
        result = run_benchmark(10)
        assert isinstance(result, BenchmarkResult)

    def test_n_notes_recorded(self):
        result = run_benchmark(10)
        assert result.n_notes == 10

    def test_all_pii_types_in_metrics(self):
        result = run_benchmark(10)
        for pii_type in ("NAME", "DATE", "PHONE", "EMAIL", "MRN"):
            assert pii_type in result.metrics, f"Missing PII type: {pii_type}"

    def test_recall_between_zero_and_one(self):
        result = run_benchmark(10)
        for pii_type, m in result.metrics.items():
            assert 0.0 <= m.recall <= 1.0, f"{pii_type} recall out of range: {m.recall}"

    def test_f1_between_zero_and_one(self):
        result = run_benchmark(10)
        for pii_type, m in result.metrics.items():
            assert 0.0 <= m.f1 <= 1.0, f"{pii_type} F1 out of range: {m.f1}"

    def test_tp_plus_fn_equals_total_spans(self):
        """For each PII type, TP + FN should equal total annotated spans of that type."""
        n = 10
        corpus = build_test_corpus(n)
        pii_types = ["NAME", "DATE", "PHONE", "EMAIL", "MRN"]
        expected_totals: dict[str, int] = {t: 0 for t in pii_types}
        for _, spans in corpus:
            for span in spans:
                if span.pii_type in expected_totals:
                    expected_totals[span.pii_type] += 1

        result = run_benchmark(n)
        for pii_type in pii_types:
            m = result.metrics[pii_type]
            actual_total = m.tp + m.fn
            assert actual_total == expected_totals[pii_type], (
                f"{pii_type}: expected {expected_totals[pii_type]} spans, "
                f"got TP+FN={actual_total}"
            )

    def test_summary_is_string(self):
        result = run_benchmark(5)
        s = result.summary()
        assert isinstance(s, str)
        assert "Precision" in s

    def test_to_dict_has_metrics_key(self):
        result = run_benchmark(5)
        d = result.to_dict()
        assert "metrics" in d
        assert "n_notes" in d

    def test_metrics_dict_has_f1_field(self):
        result = run_benchmark(5)
        d = result.to_dict()
        for pii_type in ("NAME", "DATE"):
            assert "f1" in d["metrics"][pii_type]
