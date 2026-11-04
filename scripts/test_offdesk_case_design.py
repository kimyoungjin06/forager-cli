import copy
import io
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import offdesk_case_design as worker


class CaseDesignTests(unittest.TestCase):
    def setUp(self):
        self.task_id = "TODO-2026102102"
        self.catalog = {"S03:L10-L10": {"evidence_id": "S03:L10-L10", "quote": "Complete external history passage."}}
        self.value = {"task_id": self.task_id, "scientific_acceptance": False,
            "question": "Which comparison can distinguish separation from a depth change?",
            "evidence_ids": list(self.catalog),
            "candidate_windows": [{"candidate": "Compare documented earlier and later coverage.",
                "rationale": "A candidate window is not an event date.", "evidence_ids": list(self.catalog)}],
            "comparison_universe": "Include competing predecessor branches.",
            "alternative_explanations": ["Earlier separation."],
            "required_inputs": ["Comparable independent old membership."],
            "checks": ["Common-old-Work transport conservation."],
            "unknowns": ["No supplied independent historical partition."],
            "next_action": "Prepare the required-data inventory."}

    def test_source_bound_design_passes_without_granting_acceptance(self):
        self.assertEqual(worker.validate_draft(self.value, self.task_id, self.catalog), self.value)

    def test_narrative_cap_fits_the_unchanged_whole_draft_reserve(self):
        value = copy.deepcopy(self.value)
        value["comparison_universe"] = "x" * 349
        worker.validate_draft(value, self.task_id, self.catalog)
        value["comparison_universe"] = "x" * 1000
        worker.validate_draft(value, self.task_id, self.catalog)
        value["comparison_universe"] = "x" * 4096
        with self.assertRaises(worker.ContractError):
            worker.validate_draft(value, self.task_id, self.catalog)

    def test_cached_complete_response_keeps_bytes_and_refuses_input_drift(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "runtime").mkdir()
            context = {"evidence": list(self.catalog.values())}
            schema = worker.schemas(self.task_id, list(self.catalog))[0]
            request = root / "request.json"
            response = root / "response.json"
            request.write_text(json.dumps({"model": "test", "format": schema, "prompt": worker.compact(context)}))
            response.write_text(json.dumps({"model": "test", "done": True, "done_reason": "stop", "response": json.dumps(self.value)}))
            selected = {"model": "test", "cached_drafts": {self.task_id: {
                "request": "request.json", "response": "response.json",
                "request_sha256": worker.sha(request), "response_sha256": worker.sha(response)}}}
            original = response.read_bytes()
            self.assertEqual(worker.cached_draft(root, selected, self.task_id, context, schema, self.catalog), self.value)
            self.assertEqual(response.read_bytes(), original)
            with self.assertRaises(worker.ContractError):
                worker.cached_draft(root, selected, self.task_id, {"evidence": ["changed"]}, schema, self.catalog)

    def test_full_criticism_preserves_long_text_and_repeated_known_citations(self):
        value = {"task_id": self.task_id, "scientific_acceptance": False, "verdict": "rework",
            "scope_limit": "Same-model critical notes only.",
            "findings": [{"issue": "long criticism " * 50, "evidence_ids": list(self.catalog) * 2}]}
        before = copy.deepcopy(value)
        worker.validate_review(value, self.task_id, self.catalog)
        self.assertEqual(value, before)
        value["findings"][0]["evidence_ids"].append("unbound")
        with self.assertRaises(worker.ContractError):
            worker.validate_review(value, self.task_id, self.catalog)

    def test_review_total_storage_limit_and_authority_remain_strict(self):
        value = {"task_id": self.task_id, "scientific_acceptance": False, "verdict": "rework",
            "scope_limit": "review only", "findings": [{"issue": "x" * 5000, "evidence_ids": list(self.catalog)}] * 4}
        with self.assertRaises(worker.ContractError):
            worker.validate_review(value, self.task_id, self.catalog)
        value["findings"] = []
        value["scientific_acceptance"] = True
        with self.assertRaises(worker.ContractError):
            worker.validate_review(value, self.task_id, self.catalog)

    def test_cached_review_requires_complete_unchanged_draft_and_context(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "runtime").mkdir()
            context = {"evidence": list(self.catalog.values())}
            schema = worker.schemas(self.task_id, list(self.catalog))[1]
            review = {"task_id": self.task_id, "scientific_acceptance": False, "verdict": "rework",
                "scope_limit": "same-model", "findings": [{"issue": "long note " * 80, "evidence_ids": list(self.catalog) * 2}]}
            request = root / "request.json"
            response = root / "response.json"
            request.write_text(json.dumps({"model": "test", "format": schema,
                "prompt": worker.compact(context) + "\n" + worker.compact(self.value)}))
            response.write_text(json.dumps({"model": "test", "done": True, "done_reason": "stop",
                "prompt_eval_count": 1000, "response": json.dumps(review)}))
            selected = {"model": "test", "cached_reviews": {self.task_id: {
                "request": "request.json", "response": "response.json",
                "request_sha256": worker.sha(request), "response_sha256": worker.sha(response)}}}
            self.assertEqual(worker.cached_review(root, selected, self.task_id, context, self.value, schema, self.catalog), review)
            changed = {**self.value, "question": "changed"}
            with self.assertRaises(worker.ContractError):
                worker.cached_review(root, selected, self.task_id, context, changed, schema, self.catalog)
    def test_unbound_or_duplicate_window_sources_are_refused(self):
        for refs in [["invented"], list(self.catalog) * 2]:
            value = copy.deepcopy(self.value)
            value["candidate_windows"][0]["evidence_ids"] = refs
            with self.assertRaises(worker.ContractError):
                worker.validate_draft(value, self.task_id, self.catalog)

    def test_wrong_task_acceptance_and_extra_fields_are_refused(self):
        for update in [{"task_id": "other"}, {"scientific_acceptance": True}, {"execute": True}]:
            with self.subTest(update=update), self.assertRaises(worker.ContractError):
                worker.validate_draft({**self.value, **update}, self.task_id, self.catalog)

    def test_whole_draft_reserve_is_enforced(self):
        value = copy.deepcopy(self.value)
        for key in ["alternative_explanations", "required_inputs", "checks", "unknowns"]:
            value[key] = ["x" * 300] * 6
        with self.assertRaisesRegex(worker.ContractError, "complete-review reserve"):
            worker.validate_draft(value, self.task_id, self.catalog)

    def test_review_retains_adverse_verdict_and_requires_bound_evidence(self):
        value = {"task_id": self.task_id, "scientific_acceptance": False, "verdict": "rework",
            "scope_limit": "Same-model source review only.",
            "findings": [{"issue": "Comparison universe is incomplete.", "evidence_ids": list(self.catalog)}]}
        self.assertEqual(worker.validate_review(value, self.task_id, self.catalog)["verdict"], "rework")
        value["findings"][0]["evidence_ids"] = ["invented"]
        with self.assertRaises(worker.ContractError):
            worker.validate_review(value, self.task_id, self.catalog)

    def test_full_review_keeps_late_passage_and_complete_draft(self):
        passage = "FULL HISTORY " * 80
        context = {"evidence": [{"quote": "first"}, {"quote": passage}], "omitted_references": []}
        task = {"owning_task": {"task_id": self.task_id}, "field_scope": "same question"}
        prompt = worker.prompt(task, context, {}, self.value)
        self.assertIn(passage, prompt)
        self.assertIn(worker.compact(self.value), prompt)

    def test_context_bound_is_conservative_and_refuses_overflow(self):
        self.assertGreater(worker.context_bound("Unicode: 한글", {}), len("Unicode: 한글"))
        with self.assertRaises(worker.ContractError):
            worker.context_bound("x" * 32768, {})

    def test_review_capacity_uses_complete_inputs_and_exact_reserve_boundary(self):
        task = {"owning_task": {"task_id": self.task_id}, "field_scope": "same scope"}
        context = {"evidence": [{"quote": "x" * 23000}]}
        schema = worker.schemas(self.task_id, list(self.catalog))[1]
        capacity = worker.review_capacity(task, context, schema)
        self.assertGreater(capacity, 4096)
        value = "x" * (capacity - 2)
        self.assertEqual(worker.context_bound(worker.prompt(task, context, schema, value), schema), 32768)
        with self.assertRaises(worker.ContractError):
            worker.context_bound(worker.prompt(task, context, schema, value + "x"), schema)

    def test_full_context_growth_reduces_capacity_without_omission(self):
        task = {"owning_task": {"task_id": self.task_id}, "field_scope": "scope"}
        schema = worker.schemas(self.task_id, list(self.catalog))[1]
        context = {"evidence": [{"quote": "x" * 23000}]}
        first = worker.review_capacity(task, context, schema)
        context["evidence"].append({"quote": "more full evidence" * 10})
        second = worker.review_capacity(task, context, schema)
        self.assertLess(second, first)
        self.assertEqual(len(context["evidence"]), 2)

    def test_metadata_transport_roundtrips_every_quote_kind_hash_and_reference(self):
        refs = [{"evidence_id": f"S1:L{i}-L{i}", "source_id": "S1", "source_sha256": "a" * 64,
            "line_start": i, "line_end": i, "kind": "external_history", "quote": "Full source " * 50,
            "kind_authority": "host_reviewed_source_scope", "scientific_acceptance": False} for i in range(1, 5)]
        context = {"evidence": refs, "source_versions": {"S1": "a" * 64}, "fields": ["example"]}
        original = copy.deepcopy(context)
        projected = worker.transport_context(context)
        self.assertLess(len(worker.compact(projected)), len(worker.compact(context)))
        self.assertEqual(worker.expand_context(projected), original)
        self.assertEqual(context, original)
        self.assertEqual(len(projected["evidence"]), 4)
        self.assertTrue(worker.contains_complete_context(worker.compact(projected), context))

    def test_metadata_transport_refuses_changed_versions_or_authority(self):
        refs = [{"source_id": "S1", "source_sha256": "a" * 64, "kind_authority": "host", "scientific_acceptance": False} for _ in range(2)]
        context = {"evidence": copy.deepcopy(refs), "source_versions": {"S1": "a" * 64}}
        context["evidence"][1]["source_sha256"] = "b" * 64
        with self.assertRaises(worker.ContractError):
            worker.transport_context(context)
        context["evidence"][1]["source_sha256"] = "a" * 64
        context["evidence"][1]["scientific_acceptance"] = True
        with self.assertRaises(worker.ContractError):
            worker.transport_context(context)

    def test_selected_capacity_changes_only_total_bytes_not_identity(self):
        value = copy.deepcopy(self.value)
        for key in ["required_inputs", "checks"]:
            value[key] = ["x" * 400] * 5
        with self.assertRaises(worker.ContractError):
            worker.validate_draft(value, self.task_id, self.catalog)
        worker.validate_draft(value, self.task_id, self.catalog, max_bytes=6000)
        value["scientific_acceptance"] = True
        with self.assertRaises(worker.ContractError):
            worker.validate_draft(value, self.task_id, self.catalog, max_bytes=6000)

    def test_input_drift_is_refused(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            p = root / "input.json"
            p.write_text("original")
            selection = {"file_sha256": {"input.json": worker.sha(p)}}
            worker.check_inputs(root, selection)
            p.write_text("changed")
            with self.assertRaises(worker.ContractError):
                worker.check_inputs(root, selection)

    def test_failed_request_is_counted_and_never_automatically_retried(self):
        selection = {"stop_epoch": time.time() + 4000, "max_requests": 1,
            "model": "test", "model_digest": "digest", "base_url": "http://local.invalid"}
        class Opener:
            def __init__(self):
                self.requests = 0

            def open(self, request, timeout):
                if isinstance(request, str):
                    return io.BytesIO(json.dumps({"models": [{"name": "test", "digest": "digest"}]}).encode())
                self.requests += 1
                raise TimeoutError("unknown remote outcome")

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "runtime").mkdir()
            opener = Opener()
            with patch.object(worker.urllib.request, "build_opener", return_value=opener), patch.object(worker.subprocess, "run", side_effect=TimeoutError("unknown remote outcome")) as transport:
                with self.assertRaises(TimeoutError):
                    worker.invoke(root, selection, self.task_id, "draft", "prompt", {})
                with self.assertRaisesRegex(worker.ContractError, "cap exhausted"):
                    worker.invoke(root, selection, self.task_id, "review", "prompt", {})
            self.assertEqual(transport.call_count, 1)
            self.assertEqual(len((root / "runtime/REQUEST_EVENTS.jsonl").read_text().splitlines()), 1)
            self.assertTrue((root / f"runtime/{self.task_id}_draft.transport_error.json").exists())
            self.assertFalse((root / "RESULT.json").exists())

    def test_closing_window_stops_before_a_request_is_reserved(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with self.assertRaisesRegex(worker.ContractError, "window remains"):
                worker.invoke(root, {"stop_epoch": time.time() + 100}, self.task_id, "draft", "", {})
            self.assertFalse((root / "runtime/REQUEST_EVENTS.jsonl").exists())

    def test_redirects_are_refused(self):
        with self.assertRaises(worker.ContractError):
            worker.NoRedirect().redirect_request(None, None, None, None, None, None)


if __name__ == "__main__":
    unittest.main()
