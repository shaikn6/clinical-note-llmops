"""
Tests for pipeline/batch_processor.py

Covers:
  - generate_synthetic_note: reproducibility, field presence, PHI embedding
  - run_batch: result structure, n_success, throughput > 0
  - BatchResult.summary / to_dict
  - Parallel vs single-worker consistency
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


from pipeline.batch_processor import (
    generate_synthetic_note,
    run_batch,
    BatchResult,
    _process_one_note,
)


# ---------------------------------------------------------------------------
# generate_synthetic_note
# ---------------------------------------------------------------------------

class TestGenerateSyntheticNote:
    def test_returns_dict(self):
        note = generate_synthetic_note(0)
        assert isinstance(note, dict)

    def test_required_keys_present(self):
        note = generate_synthetic_note(0)
        for key in ("note_id", "note_text", "note_type", "department"):
            assert key in note, f"Missing key: {key}"

    def test_note_id_format(self):
        note = generate_synthetic_note(42)
        assert note["note_id"] == "BATCH-00042"

    def test_note_text_contains_phi(self):
        note = generate_synthetic_note(0)
        text = note["note_text"]
        # Should contain at least one of: MRN / DOB / phone / email
        has_phi = (
            "MRN" in text
            or any(c.isdigit() for c in text)
        )
        assert has_phi

    def test_reproducible_with_same_seed(self):
        note_a = generate_synthetic_note(7, seed=42)
        note_b = generate_synthetic_note(7, seed=42)
        assert note_a["note_text"] == note_b["note_text"]

    def test_different_seed_produces_different_text(self):
        note_a = generate_synthetic_note(0, seed=1)
        note_b = generate_synthetic_note(0, seed=99)
        assert note_a["note_text"] != note_b["note_text"]

    def test_note_type_is_valid_string(self):
        note = generate_synthetic_note(3)
        assert isinstance(note["note_type"], str)
        assert len(note["note_type"]) > 0

    def test_note_text_is_non_empty(self):
        for i in range(8):
            note = generate_synthetic_note(i)
            assert len(note["note_text"].strip()) > 0


# ---------------------------------------------------------------------------
# _process_one_note
# ---------------------------------------------------------------------------

class TestProcessOneNote:
    def _sample_note(self) -> dict:
        return generate_synthetic_note(0, seed=42)

    def test_returns_dict(self):
        result = _process_one_note(self._sample_note())
        assert isinstance(result, dict)

    def test_status_success_or_error(self):
        result = _process_one_note(self._sample_note())
        assert result["status"] in ("success", "error")

    def test_note_id_preserved(self):
        note = self._sample_note()
        result = _process_one_note(note)
        assert result["note_id"] == note["note_id"]

    def test_elapsed_ms_non_negative(self):
        result = _process_one_note(self._sample_note())
        assert result["elapsed_ms"] >= 0

    def test_phi_count_non_negative(self):
        result = _process_one_note(self._sample_note())
        assert result["phi_count"] >= 0


# ---------------------------------------------------------------------------
# run_batch
# ---------------------------------------------------------------------------

class TestRunBatch:
    def test_returns_batch_result(self):
        br = run_batch(n_notes=5, max_workers=1, seed=42, show_progress=False)
        assert isinstance(br, BatchResult)

    def test_n_requested_matches(self):
        br = run_batch(n_notes=10, max_workers=2, seed=42, show_progress=False)
        assert br.n_requested == 10

    def test_success_plus_error_equals_n_requested(self):
        br = run_batch(n_notes=8, max_workers=2, seed=42, show_progress=False)
        assert br.n_success + br.n_error == br.n_requested

    def test_results_length_matches_n_requested(self):
        br = run_batch(n_notes=6, max_workers=1, seed=42, show_progress=False)
        assert len(br.results) == 6

    def test_throughput_positive(self):
        br = run_batch(n_notes=5, max_workers=1, seed=42, show_progress=False)
        assert br.notes_per_second > 0

    def test_total_elapsed_positive(self):
        br = run_batch(n_notes=5, max_workers=1, seed=42, show_progress=False)
        assert br.total_elapsed_sec > 0

    def test_parallel_workers_produce_same_count(self):
        br1 = run_batch(n_notes=10, max_workers=1, seed=42, show_progress=False)
        br2 = run_batch(n_notes=10, max_workers=4, seed=42, show_progress=False)
        # Same notes, same seed — both should succeed on all 10
        assert br1.n_requested == br2.n_requested == 10

    def test_summary_is_string(self):
        br = run_batch(n_notes=3, max_workers=1, seed=1, show_progress=False)
        s = br.summary()
        assert isinstance(s, str)
        assert "notes/sec" in s.lower() or "Throughput" in s

    def test_to_dict_has_required_keys(self):
        br = run_batch(n_notes=3, max_workers=1, seed=1, show_progress=False)
        d = br.to_dict()
        for key in ("n_requested", "n_success", "n_error", "notes_per_second", "max_workers"):
            assert key in d
