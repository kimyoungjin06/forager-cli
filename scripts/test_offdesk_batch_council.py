"""Council claim, identity, context and fail-safe response regressions."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import offdesk_batch_council as council


class CouncilTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "cwd").mkdir()
        (self.root / "schema.json").write_text(json.dumps(council.SCHEMA))
        self.request = self.root / "request.json"
        self.response = self.root / "response.json"
        self.data = {"schema": "forager_four_phase_council_request.v1",
                     "run_id": "fixture", "checkpoint": 1, "record": {}}
        self.request.write_text(json.dumps(self.data))
        self.selection = {"run_id": "fixture", "stop_epoch": 9999999999,
                          "gpt_executable": "fake-gpt", "claude_executable": "fake-claude"}
        self.seen = []

    def invoke(self, argv, **kwargs):
        self.seen.append(argv[0])
        process = mock.Mock(returncode=0)
        review = {"decision": "continue", "reason": "Fixture evidence sufficient", "unresolved": []}
        if argv[0] == "fake-gpt":
            Path(argv[argv.index("-o") + 1]).write_text(json.dumps(review))
        else:
            kwargs["stdout"].write(json.dumps({"structured_output": review}).encode())
            kwargs["stdout"].flush()
        return process

    def review(self):
        with mock.patch.object(council.subprocess, "Popen", side_effect=self.invoke), \
                mock.patch.object(council.worker, "stop_group"):
            council.review_checkpoint(self.request, self.response, self.root, self.selection)

    def test_both_providers_and_exact_request_hash_required(self):
        self.review()
        data = json.loads(self.response.read_text())
        self.assertEqual(self.seen, ["fake-gpt", "fake-claude"])
        self.assertTrue(data["ready_to_continue"])
        self.assertEqual(data["request_sha256"], council.worker.sha(self.request))
        self.assertFalse(data["weekly_percent_hard_cap_enforced"])

    def test_claim_prevents_repeat_provider_invocation(self):
        self.review()
        with self.assertRaises(FileExistsError):
            self.review()
        self.assertEqual(self.seen, ["fake-gpt", "fake-claude"])

    def test_wrong_run_starts_no_provider(self):
        self.data["run_id"] = "other"
        self.request.write_text(json.dumps(self.data))
        with self.assertRaises(ValueError):
            self.review()
        self.assertEqual(self.seen, [])

    def test_oversized_context_starts_no_provider(self):
        self.data["summary"] = "a" * 16000
        self.request.write_text(json.dumps(self.data))
        with self.assertRaises(ValueError):
            self.review()
        self.assertEqual(self.seen, [])

    def test_invalid_review_causes_stop(self):
        base = self.invoke
        def invoke(argv, **kwargs):
            process = base(argv, **kwargs)
            if argv[0] == "fake-gpt":
                Path(argv[argv.index("-o") + 1]).write_text('{"decision":"continue"}')
            return process
        self.invoke = invoke
        self.review()
        self.assertFalse(json.loads(self.response.read_text())["ready_to_continue"])

    def test_changed_request_cannot_publish_continuation(self):
        base = self.invoke
        def invoke(argv, **kwargs):
            process = base(argv, **kwargs)
            self.request.write_text(json.dumps({**self.data, "changed": True}))
            return process
        self.invoke = invoke
        with self.assertRaises(ValueError):
            self.review()
        self.assertFalse(self.response.exists())


if __name__ == "__main__":
    unittest.main()
