import copy
import hashlib
import json
import unittest

import offdesk_evidence_contract as contract


class EvidenceContractTests(unittest.TestCase):
    def setUp(self):
        self.source = "Current cluster membership.\nCurrent citation participation: 72%.\nPast partitions have not been secured.\n"
        digest = hashlib.sha256(self.source.encode()).hexdigest()
        self.catalog = [
            contract.source_evidence("S1", self.source, digest, line, line, kind, {1, 2, 3})
            for line, kind in [(1, "current_membership"), (2, "current_cohort_statistics"), (3, "scope_limit")]
        ]
        self.value = {"field": "example", "summary": "Review notes only.",
                      "evidence_ids": [self.catalog[1]["evidence_id"]],
                      "unknowns": ["Historical partitions remain absent."],
                      "alternatives": ["Current participation is not annual lineage."],
                      "next_draft_action": "Prepare missing historical inputs.",
                      "scientific_acceptance": False}

    def observation(self, line=2, kind="current_cohort_statistics"):
        return {"kind": kind, "source_id": "S1", "line_start": line, "line_end": line,
                "statement": "Model prose remains unverified.", "quote": self.source.splitlines()[line - 1]}

    def test_schema_only_allows_selected_evidence_ids(self):
        schema = contract.field_schema("example", self.catalog)
        self.assertEqual(schema["properties"]["evidence_ids"]["items"]["enum"],
                         [item["evidence_id"] for item in self.catalog])
        self.assertFalse(schema["additionalProperties"])
        self.assertNotIn("kind", schema["properties"])
        self.assertNotIn("quote", schema["properties"])

    def test_host_owns_verbatim_quote_and_kind(self):
        bound = contract.bind_field(self.value, "example", self.catalog)
        self.assertEqual(bound["observations"][0], self.catalog[1])
        self.assertEqual(bound["observations"][0]["kind"], "current_cohort_statistics")
        self.assertFalse(bound["annual_c2c_verified"])
        self.assertFalse(bound["historical_partition_verified"])
        self.assertEqual(bound["model_prose_status"], "unverified_review_notes")

    def test_model_cannot_override_source_kind_or_quote(self):
        for name, value in [("kind", "annual_c2c"), ("quote", "A split was proven.")]:
            with self.subTest(name=name):
                altered = {**self.value, name: value}
                with self.assertRaises(contract.ContractError):
                    contract.bind_field(altered, "example", self.catalog)

    def test_unknown_duplicate_and_other_field_evidence_are_rejected(self):
        for ids in [["unknown"], [self.catalog[0]["evidence_id"]] * 2]:
            with self.subTest(ids=ids), self.assertRaises(contract.ContractError):
                contract.bind_field({**self.value, "evidence_ids": ids}, "example", self.catalog)
        with self.assertRaises(contract.ContractError):
            contract.bind_field(self.value, "other", self.catalog)
        with self.assertRaises(contract.ContractError):
            contract.bind_field(self.value, "example", [self.catalog[0]])

    def test_acceptance_unknown_keys_and_invalid_notes_are_rejected(self):
        for patch in [{"scientific_acceptance": True}, {"scientific_acceptance": 0},
                      {"unknowns": []}, {"summary": ""}, {"approval": True}]:
            with self.subTest(patch=patch), self.assertRaises(contract.ContractError):
                contract.bind_field({**self.value, **patch}, "example", self.catalog)

    def test_changed_or_unsupplied_source_never_enters_catalog(self):
        digest = hashlib.sha256(self.source.encode()).hexdigest()
        for source, first, last, supplied in [(self.source + "changed", 1, 1, {1}),
                                              (self.source, 1, 2, {1}),
                                              (self.source, True, 1, {1}),
                                              (self.source, 1, 99, {1})]:
            with self.subTest(first=first, last=last), self.assertRaises(contract.ContractError):
                contract.source_evidence("S1", source, digest, first, last, "current_membership", supplied)

    def test_correct_legacy_scope_is_retained_without_mutating_original(self):
        value = {"observations": [self.observation()]}
        before = json.dumps(value, sort_keys=True)
        audit = contract.audit_legacy_field(value, self.catalog)
        self.assertEqual(len(audit["kept"]), 1)
        self.assertEqual(audit["withheld"], [])
        self.assertEqual(json.dumps(value, sort_keys=True), before)
        self.assertFalse(audit["supports_scientific_truth"])

    def test_real_failure_classes_are_quarantined_without_relabelling(self):
        for line, kind in [(2, "annual_c2c"), (1, "historical_partition"),
                           (3, "annual_c2c"), (1, "method_contract"), (3, "unknowns")]:
            with self.subTest(line=line, kind=kind):
                value = {"observations": [self.observation(line, kind)]}
                original = copy.deepcopy(value)
                audit = contract.audit_legacy_field(value, self.catalog)
                self.assertEqual(len(audit["withheld"]), 1)
                self.assertEqual(audit["kept"], [])
                self.assertEqual(audit["withheld"][0]["original"]["kind"], kind)
                self.assertEqual(value, original)

    def test_mixed_scope_range_and_nonliteral_legacy_quotes_are_rejected(self):
        altered = self.observation(1, "current_membership")
        altered["line_end"] = 2
        altered["quote"] = self.source.splitlines()[0]
        for item in [altered, {**self.observation(), "quote": "Proven annual split"},
                     {**self.observation(), "line_start": False}, {"kind": "annual_c2c"}]:
            with self.subTest(item=item):
                audit = contract.audit_legacy_field({"observations": [item]}, self.catalog)
                self.assertEqual(audit["kept"], [])

    def test_duplicate_or_inconsistent_catalog_is_refused(self):
        with self.assertRaises(contract.ContractError):
            contract.field_schema("example", [self.catalog[0], self.catalog[0]])
        changed = {**self.catalog[0], "evidence_id": "S1:L2-L2"}
        with self.assertRaises(contract.ContractError):
            contract.field_schema("example", [changed])

    def test_same_model_review_is_bounded_and_never_accepts_science(self):
        value = {"verdict": "rework", "scope_limit": "Same-model excerpts only.",
                 "findings": [{"field": "example", "issue": "Missing partition evidence."}],
                 "scientific_acceptance": False}
        self.assertEqual(contract.validate_review(value, ["example"]), value)
        for patch in [{"verdict": "accepted"}, {"scientific_acceptance": True},
                      {"findings": [{"field": "other", "issue": "Unbound field"}]}]:
            with self.subTest(patch=patch), self.assertRaises(contract.ContractError):
                contract.validate_review({**value, **patch}, ["example"])

    def test_contract_hash_binds_exact_selection(self):
        first = contract.field_schema("example", self.catalog)
        second = contract.field_schema("example", [self.catalog[0]])
        self.assertNotEqual(contract.contract_sha256(first), contract.contract_sha256(second))
        self.assertEqual(contract.contract_sha256(first), contract.contract_sha256(copy.deepcopy(first)))

    def test_wire_schema_retains_structure_without_expanding_bounded_strings(self):
        full = contract.field_schema("example", self.catalog)
        wire = contract.ollama_schema(full)
        self.assertNotIn("maxLength", json.dumps(wire))
        self.assertNotIn("uniqueItems", json.dumps(wire))
        self.assertEqual(wire["required"], full["required"])
        self.assertEqual(wire["properties"]["evidence_ids"]["items"],
                         full["properties"]["evidence_ids"]["items"])
        self.assertFalse(wire["additionalProperties"])
        self.assertEqual(full["properties"]["summary"]["maxLength"], 2000)

    def test_wire_projection_does_not_relax_host_length_or_duplicate_checks(self):
        contract.ollama_schema(contract.field_schema("example", self.catalog))
        for patch in [{"summary": "x" * 2001},
                      {"evidence_ids": [self.catalog[0]["evidence_id"]] * 2}]:
            with self.subTest(patch=patch), self.assertRaises(contract.ContractError):
                contract.bind_field({**self.value, **patch}, "example", self.catalog)

    def test_review_context_keeps_late_history_and_complete_long_quotes(self):
        text = "Known history: " + "source evidence " * 30
        ref = contract.source_evidence("HISTORY", text, hashlib.sha256(text.encode()).hexdigest(),
                                       1, 1, "external_history", {1})
        field = contract.bind_field(self.value, "example", self.catalog)
        field["observations"] = [*self.catalog, ref]
        result = contract.build_review_context([field])
        self.assertEqual(len(result["evidence"]), 4)
        self.assertEqual(result["evidence"][-1]["quote"], text)
        self.assertIn("external_history", result["coverage"][0]["evidence_kinds"])
        self.assertEqual(result["fields"][0]["summary"], self.value["summary"])
        self.assertEqual(result["omitted_references"], [])
        self.assertEqual(result["truncated_quotes"], [])

    def test_review_context_deduplicates_shared_source_without_losing_field_coverage(self):
        first = contract.bind_field(self.value, "example", self.catalog)
        second = copy.deepcopy(first)
        second["field"] = "other"
        result = contract.build_review_context([first, second])
        self.assertEqual(len(result["evidence"]), 1)
        self.assertEqual(len(result["coverage"]), 2)
        self.assertTrue(all(item["emitted_references"] == 1 for item in result["coverage"]))

    def test_review_context_over_budget_refuses_without_silent_truncation(self):
        field = contract.bind_field(self.value, "example", self.catalog)
        before = copy.deepcopy(field)
        with self.assertRaisesRegex(contract.ContractError, "select explicit separate contexts"):
            contract.build_review_context([field], max_bytes=100)
        self.assertEqual(field, before)

    def test_review_context_refuses_mixed_versions_and_conflicting_shared_text(self):
        first = contract.bind_field(self.value, "example", self.catalog)
        for key, replacement in [("source_sha256", "0" * 64), ("quote", "changed")]:
            with self.subTest(key=key):
                second = copy.deepcopy(first)
                second["field"] = "other"
                second["observations"][0][key] = replacement
                with self.assertRaises(contract.ContractError):
                    contract.build_review_context([first, second])

    def test_review_context_never_accepts_duplicate_fields_or_acceptance_flags(self):
        field = contract.bind_field(self.value, "example", self.catalog)
        with self.assertRaises(contract.ContractError):
            contract.build_review_context([field, field])
        field["scientific_acceptance"] = True
        with self.assertRaises(contract.ContractError):
            contract.build_review_context([field])


if __name__ == "__main__":
    unittest.main()
