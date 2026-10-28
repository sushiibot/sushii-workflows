"""Execute publisher and receipt logic with synthetic network responses."""
import hashlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_release_presets import workflow, step


def python_step(title, job):
    script = step("_release-request.yml", title, job)["run"]
    return script.split("python3 - <<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]


class RegistryReleaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.manifest = b'{"schemaVersion":2}'
        self.digest = "sha256:" + hashlib.sha256(self.manifest).hexdigest()
        self.env = {"TARGET": "production", "DIGEST": self.digest, "GITHUB_SHA": "a"*40,
                    "GITHUB_RUN_ID": "123", "GITHUB_RUN_ATTEMPT": "2",
                    "GITHUB_REPOSITORY": "example/worker", "RUNNER_TEMP": self.tmp.name,
                    "GITHUB_OUTPUT": str(Path(self.tmp.name) / "output")}

    def publish(self, manifest=None):
        responses = [io.BytesIO(b'{"token":"anonymous-test"}'), io.BytesIO(manifest or self.manifest)]
        with patch.dict(os.environ, self.env), patch("urllib.request.urlopen", side_effect=responses):
            exec(python_step("Construct release intent", "publish"), {})

    def test_exact_bytes_and_context_retained(self):
        self.publish()
        directory = Path(self.tmp.name) / "release-request"
        request = json.loads((directory / "request.json").read_text())
        self.assertEqual(request, {"schema_version": 1, "target": "production",
                                  "artifact_digest": self.digest, "source_sha": "a"*40,
                                  "run_id": 123, "run_attempt": 2})
        self.assertEqual((directory / "artifact-manifest.json").read_bytes(), self.manifest)

    def test_mutable_digest_and_manifest_mismatch_denied(self):
        self.env["DIGEST"] = "latest"
        with self.assertRaises(AssertionError):
            self.publish()
        self.env["DIGEST"] = self.digest
        with self.assertRaises(AssertionError):
            self.publish(b'{"wrong":true}')

    def wait(self, statuses, timeout=False):
        with patch.dict(os.environ, self.env), patch("subprocess.check_output", return_value=json.dumps([statuses])), \
                patch("time.sleep"), patch("time.monotonic", side_effect=[0, 0, 1801] if timeout else [0, 0]):
            exec(python_step("Wait for exact host receipt", "wait"), {})

    def status(self, **changes):
        return {"context": "release/production/123/2", "state": "success",
                "description": "deployed " + self.digest, "creator": {"id": 342252709}, **changes}

    def test_exact_receipt_succeeds(self):
        self.wait([self.status()])
        self.assertEqual(Path(self.env["GITHUB_OUTPUT"]).read_text(), "deployed=true\n")

    def test_receipt_on_second_page_succeeds(self):
        pages = [[self.status(context="other")], [self.status()]]
        with patch.dict(os.environ, self.env), patch("subprocess.check_output", return_value=json.dumps(pages)) as command,                 patch("time.monotonic", side_effect=[0, 0]):
            exec(python_step("Wait for exact host receipt", "wait"), {})
        self.assertIn("--paginate", command.call_args.args[0])
        self.assertEqual(Path(self.env["GITHUB_OUTPUT"]).read_text(), "deployed=true\n")

    def test_spoofed_author_or_other_attempt_cannot_succeed(self):
        for status in (self.status(creator={"id": 1}), self.status(context="release/production/123/1")):
            with self.assertRaises(SystemExit):
                self.wait([status], timeout=True)
        self.assertFalse(Path(self.env["GITHUB_OUTPUT"]).exists())

    def test_wrong_digest_and_failure_denied(self):
        with self.assertRaises(AssertionError):
            self.wait([self.status(description="deployed sha256:"+"b"*64)])
        with self.assertRaises(SystemExit):
            self.wait([self.status(state="error")])

    def test_isolated_signers_no_checkout_or_caller_scripts(self):
        for job in (workflow("build-verified.yml")["jobs"]["attest"],
                    workflow("_release-request.yml")["jobs"]["publish"]):
            self.assertNotIn("secrets", job)
            self.assertFalse(any("checkout@" in s.get("uses", "") for s in job["steps"]))
            self.assertEqual(job["permissions"]["id-token"], "write")


if __name__ == "__main__":
    unittest.main()
