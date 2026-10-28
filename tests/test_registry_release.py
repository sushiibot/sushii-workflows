"""Execute publisher and receipt logic with synthetic network responses."""
import hashlib
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_release_presets import workflow, step, helper_code


def python_step(title, job):
    return helper_code("_release-request.yml", title, job)


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

    def test_builder_supplies_trusted_utc_build_date(self):
        from datetime import datetime, timezone
        script = helper_code("build-verified.yml", "Derive image identity", "build")
        before = datetime.now(timezone.utc).replace(microsecond=0)
        with patch.dict(os.environ, {**self.env, "REPOSITORY": "example/worker", "IMAGE": "", "ARTIFACT": "",
                                    "DOCKERFILE": "Dockerfile", "BUILD_TARGET": "", "BUILD_DATE": "untrusted"}):
            exec(script, {})
        values = dict(line.split("=", 1) for line in Path(self.env["GITHUB_OUTPUT"]).read_text().splitlines())
        actual = datetime.fromisoformat(values["build_date"].replace("Z", "+00:00"))
        self.assertGreaterEqual(actual, before)
        self.assertLessEqual(actual, datetime.now(timezone.utc))
        args = step("build-verified.yml", "Build exact source", "build")["with"]["build-args"]
        self.assertIn("BUILD_DATE=${{ steps.identity.outputs.build_date }}", args)
        self.assertIn("GIT_HASH=${{ github.sha }}", args)

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
        code = helper_code("build-verified.yml", "Derive image identity", "build")
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

    def test_environment_gate_precedes_request_and_dispatch(self):
        wrapper = workflow("release-auto.yml")["jobs"]
        self.assertEqual(wrapper["registry"]["with"]["environment"], "${{ inputs.environment }}")
        jobs = workflow("_release-request.yml")["jobs"]
        self.assertEqual(jobs["publish"]["environment"], "${{ inputs.environment }}")
        self.assertEqual(jobs["dispatch"]["needs"], "publish")
        self.assertEqual(wrapper["registry"]["with"]["require_approval"], "${{ inputs.require_approval }}")
        self.assertEqual(jobs["publish"]["needs"], "preflight")

    def approved_fixture(self):
        self.env.update(REQUIRE_APPROVAL='true', ENVIRONMENT='production',
                        ALLOW_MANUAL='false', GITHUB_EVENT_NAME='push', GITHUB_RUN_ATTEMPT='1')
        environment = dict(id=10, name='production', protection_rules=[
            dict(type='required_reviewers', reviewers=[dict(type='User', reviewer=dict(id=20))])])
        history = [dict(state='approved', user=dict(id=20),
                        environments=[dict(id=10, name='production')])]
        return environment, history

    def publish_approved(self, environment, history):
        with patch('subprocess.check_output', side_effect=[json.dumps(environment), json.dumps(history)]):
            self.publish()

    def test_approved_intent_binds_environment_and_reviewer(self):
        self.publish_approved(*self.approved_fixture())
        request = json.loads((Path(self.tmp.name) / 'release-request/request.json').read_text())
        self.assertEqual(request['schema_version'], 3)
        self.assertEqual(request['approval'], dict(environment='production', environment_id=10, reviewer_id=20))
        self.assertEqual(request['run_attempt'], 1)

    def test_approval_rejects_reruns_manual_unsigned_and_bundles(self):
        for key, value in [('GITHUB_RUN_ATTEMPT', '2'), ('GITHUB_EVENT_NAME', 'workflow_dispatch'),
                           ('PROVENANCE', 'github-actions'), ('ARTIFACT_KIND', 'github-release-bundle'),
                           ('ALLOW_MANUAL', 'true'), ('ENVIRONMENT', '')]:
            with self.subTest(key=key):
                environment, history = self.approved_fixture()
                self.env[key] = value
                with self.assertRaises(AssertionError):
                    self.publish_approved(environment, history)
                self.env.pop(key)
        self.assertFalse((Path(self.tmp.name) / 'release-request/request.json').exists())

    def test_approval_rejects_removed_reviewers_team_or_recreated_environment(self):
        environment, history = self.approved_fixture()
        variants = []
        for field, value in [('id', 11), ('id', True), ('name', 'other'), ('protection_rules', [])]:
            variants.append(dict(environment, **{field: value}))
        for reviewer in [dict(type='Team', reviewer=dict(id=20)), dict(type='User', reviewer=dict(id=30)),
                         dict(type='User', reviewer=dict(id=True))]:
            variants.append(dict(environment, protection_rules=[dict(type='required_reviewers', reviewers=[reviewer])]))
        for changed in variants:
            with self.subTest(environment=changed), self.assertRaises(AssertionError):
                self.publish_approved(changed, history)
        self.assertFalse((Path(self.tmp.name) / 'release-request/request.json').exists())

    def test_approval_rejects_absent_rejected_ambiguous_or_foreign_history(self):
        environment, history = self.approved_fixture()
        variants = [[], history * 2, [dict(history[0], state='rejected')],
                    [dict(history[0], user=dict(id=99))],
                    [dict(history[0], environments=[dict(id=99, name='other')])],
                    [dict(history[0], environments=[dict(id=99, name='production')])],
                    history + [dict(history[0], state='rejected')]]
        for changed in variants:
            with self.subTest(history=changed), self.assertRaises(AssertionError):
                self.publish_approved(environment, changed)
        self.assertFalse((Path(self.tmp.name) / 'release-request/request.json').exists())

    def test_preflight_rejects_missing_gate_and_replays_before_publish(self):
        environment, _ = self.approved_fixture()
        code = python_step('Validate approval contract before the gate', 'preflight')
        self.env.update(PROVENANCE='attested', ARTIFACT_KIND='oci')
        with patch.dict(os.environ, self.env), patch('subprocess.check_output', return_value=json.dumps(environment)):
            exec(code, {})
        for changes in [dict(GITHUB_RUN_ATTEMPT='2'), dict(PROVENANCE='github-actions'),
                        dict(ARTIFACT_KIND='github-release-bundle'), dict(ENVIRONMENT='')]:
            with patch.dict(os.environ, dict(self.env, **changes)), self.assertRaises(AssertionError):
                exec(code, {})
        environment['protection_rules'][0]['reviewers'] = []
        with patch.dict(os.environ, self.env), patch('subprocess.check_output', return_value=json.dumps(environment)), self.assertRaises(AssertionError):
            exec(code, {})

    def test_registry_staged_preserves_digest_and_requires_host_success(self):
        jobs = workflow('release-staged.yml')['jobs']
        production = jobs['registry-production']
        self.assertEqual(production['needs'], 'registry-staging')
        self.assertEqual(production['if'], "needs.registry-staging.outputs.deployed == 'true'")
        self.assertTrue(production['with']['require_approval'])
        for name in ('registry-staging', 'registry-production'):
            self.assertEqual(jobs[name]['with']['artifact_digest'], '${{ inputs.artifact_digest }}')
            self.assertEqual(jobs[name]['with']['image_name'], '${{ inputs.image_name }}')
        self.assertNotIn('concurrency', production)

    def test_staged_rejects_partial_and_mixed_mode_inputs(self):
        script = step('release-staged.yml', 'Validate staged release mode', 'inputs')['run']
        env = dict(os.environ, STAGING_TARGET='staging', PRODUCTION_TARGET='production',
                   DIGEST=self.digest, IMAGE='', ARTIFACT='', VERSION='', LEGACY_PLACEMENT='',
                   STAGING_ENVIRONMENT='staging', PRODUCTION_ENVIRONMENT='production')
        def run(changes):
            return subprocess.run(['bash', '-e', '-c', script], env=dict(env, **changes),
                                  capture_output=True).returncode
        self.assertEqual(run({}), 0)
        for changes in [dict(STAGING_TARGET=''), dict(PRODUCTION_TARGET=''), dict(DIGEST=''),
                        dict(PRODUCTION_TARGET='staging'), dict(VERSION='abcdef0'),
                        dict(LEGACY_PLACEMENT='bots/example'), dict(PRODUCTION_ENVIRONMENT=''),
                        dict(PRODUCTION_ENVIRONMENT='staging')]:
            with self.subTest(changes=changes):
                self.assertNotEqual(run(changes), 0)
        self.assertEqual(run(dict(STAGING_TARGET='', PRODUCTION_TARGET='', DIGEST='', VERSION='abcdef0')), 0)
        self.assertNotEqual(run(dict(STAGING_TARGET='', PRODUCTION_TARGET='', DIGEST='')), 0)

    def test_build_artifact_cannot_overwrite_generated_intent(self):
        self.publish()
        request = Path(self.tmp.name) / 'release-request/request.json'
        original = request.read_bytes()
        evidence_dir = Path(self.tmp.name) / 'release-build-evidence'
        evidence_dir.mkdir()
        evidence = evidence_dir / 'build.sigstore.json'
        evidence.write_text('{}')
        malicious = evidence_dir / 'request.json'
        malicious.write_text('{"schema_version": 3}')
        code = python_step('Accept only build evidence from the artifact', 'publish')
        with patch.dict(os.environ, self.env), self.assertRaises(AssertionError):
            exec(code, {})
        self.assertEqual(request.read_bytes(), original)
        malicious.unlink()
        evidence.unlink()
        evidence.symlink_to(request)
        with patch.dict(os.environ, self.env), self.assertRaises(AssertionError):
            exec(code, {})
        evidence.unlink()
        evidence.write_text('{}')
        with patch.dict(os.environ, self.env):
            exec(code, {})
        self.assertEqual(request.read_bytes(), original)
        self.assertEqual((request.parent / 'build.sigstore.json').read_text(), '{}')
        download = step('_release-request.yml', "Fetch this run's build evidence", 'publish')
        self.assertEqual(download['with']['path'], '${{ runner.temp }}/release-build-evidence')
        steps = [s['name'] for s in workflow('_release-request.yml')['jobs']['publish']['steps']]
        self.assertLess(steps.index('Accept only build evidence from the artifact'),
                        steps.index('Sign explicit release intent'))

    def test_build_evidence_size_matches_importer_limit(self):
        self.publish()
        directory = Path(self.tmp.name) / 'release-build-evidence'
        directory.mkdir()
        source = directory / 'build.sigstore.json'
        code = python_step('Accept only build evidence from the artifact', 'publish')
        destination = Path(self.tmp.name) / 'release-request/build.sigstore.json'
        for size in (0, 4 * 1024 * 1024 + 1):
            source.write_bytes(b'x' * size)
            with patch.dict(os.environ, self.env), self.assertRaises(AssertionError):
                exec(code, {})
            self.assertFalse(destination.exists())
        source.write_bytes(b'x' * (4 * 1024 * 1024))
        with patch.dict(os.environ, self.env):
            exec(code, {})
        self.assertEqual(destination.stat().st_size, 4 * 1024 * 1024)

    def test_auto_rejects_mixed_and_incomplete_registry_inputs(self):
        script = step('release-auto.yml', 'Validate registry inputs', 'registry-inputs')['run']
        env = dict(os.environ, TARGET='production', DIGEST=self.digest, REGISTRY_ARTIFACT='',
                   MANUAL='false', PROVENANCE='attested', SERVICE='', HOST='', VERSION='')
        def run(changes):
            return subprocess.run(['bash', '-e', '-c', script], env=dict(env, **changes),
                                  capture_output=True).returncode
        self.assertEqual(run({}), 0)
        for changes in [dict(TARGET=''), dict(DIGEST=''), dict(SERVICE='bots/foo'),
                        dict(HOST='apps'), dict(VERSION='abcdef0'), dict(MANUAL='true')]:
            self.assertNotEqual(run(changes), 0)
        self.assertEqual(run(dict(TARGET='', DIGEST='', VERSION='abcdef0')), 0)
        self.assertEqual(run(dict(MANUAL='true', PROVENANCE='github-actions')), 0)
        self.assertNotEqual(run(dict(TARGET='', DIGEST='', REGISTRY_ARTIFACT='ghcr.io/org/app')), 0)

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
