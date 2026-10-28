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

    def test_github_actions_mode_retains_unsigned_placeholders(self):
        self.env['PROVENANCE'] = 'github-actions'
        self.publish()
        directory = Path(self.tmp.name) / 'release-request'
        for name in ('intent.sigstore.json', 'build.sigstore.json'):
            self.assertEqual(json.loads((directory / name).read_text()), {})
        self.assertEqual((directory / 'artifact-manifest.json').read_bytes(), self.manifest)

    def test_bundle_publisher_binds_asset_metadata(self):
        self.env.update(PROVENANCE='github-actions', ARTIFACT_KIND='github-release-bundle',
                        RELEASE_ID='10', RELEASE_ASSET_ID='20')
        metadata = dict(id=20, digest=self.digest, size=123, name='public-assets.tar.gz')
        with patch.dict(os.environ, self.env), patch('subprocess.check_output', return_value=json.dumps(metadata)):
            exec(python_step('Construct release intent', 'publish'), {})
        directory = Path(self.tmp.name) / 'release-request'
        request = json.loads((directory / 'request.json').read_text())
        self.assertEqual((request['schema_version'], request['release_id'], request['release_asset_id']), (2, 10, 20))
        self.assertEqual(json.loads((directory / 'artifact-manifest.json').read_text()),
                         dict(kind='github-release-bundle', release_id=10, release_asset_id=20,
                              name='public-assets.tar.gz', size=123))

    def test_bundle_rejects_unmatched_digest(self):
        self.env.update(PROVENANCE='github-actions', ARTIFACT_KIND='github-release-bundle',
                        RELEASE_ID='10', RELEASE_ASSET_ID='20')
        metadata = dict(id=20, digest='sha256:'+'f'*64, size=123, name='public-assets.tar.gz')
        with patch.dict(os.environ, self.env), patch('subprocess.check_output', return_value=json.dumps(metadata)):
            with self.assertRaises(AssertionError):
                exec(python_step('Construct release intent', 'publish'), {})

    def test_bundle_rejects_attested_mode(self):
        self.env.update(PROVENANCE='attested', ARTIFACT_KIND='github-release-bundle',
                        RELEASE_ID='10', RELEASE_ASSET_ID='20')
        with patch.dict(os.environ, self.env), self.assertRaises(AssertionError):
            exec(python_step('Construct release intent', 'publish'), {})

    def test_named_builds_use_exact_source_and_distinct_tags(self):
        job = workflow('build-verified.yml')['jobs']['build']
        script = next(s['run'] for s in job['steps'] if s.get('name') == 'Derive image identity')
        code = script.split("python3 - <<'PY'\n", 1)[1].rsplit('\nPY', 1)[0]
        env = dict(self.env, IMAGE='ghcr.io/example/custom', ARTIFACT='workspace',
                   REPOSITORY='example/worker', DOCKERFILE='packages/app/Dockerfile', BUILD_TARGET='workspace')
        with patch.dict(os.environ, env):
            exec(code, {})
        self.assertIn('tag=ghcr.io/example/custom:'+'a'*40+'-workspace', Path(self.env['GITHUB_OUTPUT']).read_text())
        build = next(s for s in job['steps'] if s.get('name') == 'Build exact source')['with']
        self.assertEqual(build['context'], 'https://github.com/${{ github.repository }}.git#${{ github.sha }}')
        self.assertEqual(build['target'], '${{ inputs.build_target }}')
        env['DOCKERFILE'] = '../Dockerfile'
        with patch.dict(os.environ, env), self.assertRaises(AssertionError):
            exec(code, {})

    def test_dispatch_uses_context_and_fixed_destination(self):
        with patch.dict(os.environ, self.env), patch("subprocess.run") as command:
            exec(python_step("Notify private release importer", "dispatch"), {})
        args, kwargs = command.call_args
        self.assertIn("repos/sushiibot/sushii-ansible/actions/workflows/import-release.yml/dispatches", args[0])
        self.assertTrue(kwargs["check"])
        self.assertEqual(json.loads(kwargs["input"]), {"ref": "main", "inputs": {
            "source_repository": "example/worker", "run_id": "123", "target": "production"}})
        jobs = workflow("_release-request.yml")["jobs"]
        self.assertEqual(jobs["dispatch"]["needs"], "publish")
        self.assertEqual(jobs["wait"]["needs"], "dispatch")
        self.assertEqual(jobs["dispatch"]["permissions"], {})
        token = step("_release-request.yml", "Mint importer dispatch token", "dispatch")["with"]
        self.assertEqual(token["repositories"], "sushii-ansible")
        self.assertEqual({k: v for k, v in token.items() if k.startswith("permission-")},
                         {"permission-actions": "write"})
        self.assertFalse(any("checkout@" in x.get("uses", "") for x in jobs["dispatch"]["steps"]))
        self.assertNotIn("secrets.", str(jobs["publish"]))
        self.assertNotIn("secrets.", str(jobs["wait"]))

    def test_isolated_signers_no_checkout_or_caller_scripts(self):
        for job in (workflow("build-verified.yml")["jobs"]["attest"],
                    workflow("_release-request.yml")["jobs"]["publish"]):
            self.assertNotIn("secrets", job)
            self.assertFalse(any("checkout@" in s.get("uses", "") for s in job["steps"]))
            self.assertEqual(job["permissions"]["id-token"], "write")


if __name__ == "__main__":
    unittest.main()
