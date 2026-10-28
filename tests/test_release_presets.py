"""Offline behavioral tests for release helpers and workflow orchestration."""

import copy
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github/workflows"


def workflow(name):
    return yaml.safe_load((WORKFLOWS / name).read_text())


def step(name, title, job="deploy"):
    return next(
        s for s in workflow(name)["jobs"][job]["steps"] if s.get("name") == title
    )


def helper_source(name, title, job):
    command = step(name, title, job)["with"]["command"]
    return (ROOT / "actions/release-tools" / (command + ".py")).read_text()


def helper_code(name, title, job):
    return helper_source(name, title, job) + "\nmain()\n"


class PromotionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.output = Path(self.temp.name) / "output"
        self.summary = Path(self.temp.name) / "summary"
        self.sha = "a" * 40
        self.source_run = {
            "repository": {"full_name": "org/app"},
            "head_repository": {"full_name": "org/app"},
            "event": "push",
            "head_branch": "main",
            "path": ".github/workflows/ci.yml",
            "status": "completed",
            "conclusion": "success",
            "head_sha": self.sha,
            "run_attempt": 2,
        }
        self.receipt = {
            "schema_version": 1,
            "repository": "org/app",
            "source_sha": self.sha,
            "run_id": 42,
            "run_attempt": 2,
            "service": "bots/staging",
            "host": "apps",
            "image_version": "a" * 7,
            "infra_sha": "b" * 40,
        }
        self.artifacts = [
            {
                "id": 19,
                "name": "staging-release-2",
                "expired": False,
                "size_in_bytes": 500,
            }
        ]
        self.statuses = [{"context": "deploy/apps/bots/staging", "state": "success"}]
        self.env = {
            "REPOSITORY": "org/app",
            "RUN_ID": "42",
            "WORKFLOW_PATH": ".github/workflows/ci.yml",
            "RECEIPT_NAME": "staging-release",
            "STAGING_SERVICE": "bots/staging",
            "STAGING_HOST": "apps",
            "GITHUB_OUTPUT": str(self.output),
            "GITHUB_STEP_SUMMARY": str(self.summary),
        }
        self.script = helper_source(
            "release-promote.yml", "Resolve successful staging receipt", "resolve"
        )
        self.scope = {"__name__": "test"}
        exec(compile(self.script, "<promotion workflow>", "exec"), self.scope)  # noqa: S102 - trusted local workflow under test
        self.archive_name = "release.json"

    def api(self, path, *, infra=False, binary=False):
        if path == "repos/org/app":
            return {"default_branch": "main"}
        if path == "repos/org/app/actions/runs/42":
            return self.source_run
        if "/artifacts?" in path:
            return {"artifacts": self.artifacts}
        if path.endswith("/zip"):
            data = io.BytesIO()
            with zipfile.ZipFile(data, "w") as archive:
                archive.writestr(self.archive_name, json.dumps(self.receipt))
            return data.getvalue()
        if "/statuses?" in path:
            self.assertTrue(infra)
            return self.statuses
        self.fail(f"Unexpected API request: {path}")

    def execute(self):
        with patch.dict(os.environ, self.env), patch.dict(self.scope, api=self.api):
            self.scope["main"]()

    def reject(self):
        with self.assertRaises(SystemExit):
            self.execute()
        self.assertFalse(self.output.exists())

    def test_success_preserves_exact_tag_and_source(self):
        self.execute()
        self.assertEqual(
            self.output.read_text(), f"image_version=aaaaaaa\nsource_sha={self.sha}\n"
        )
        self.assertIn("/actions/runs/42", self.summary.read_text())

    def test_actual_receipt_writer_round_trip(self):
        receipt_dir = Path(self.temp.name) / "receipt"
        env = dict(
            os.environ,
            RECEIPT_DIR=str(receipt_dir),
            REPOSITORY="org/app",
            SOURCE_SHA=self.sha,
            IMAGE_VERSION="a" * 7,
            SERVICE="bots/staging",
            HOST="apps",
            INFRA_SHA="b" * 40,
            GITHUB_RUN_ID="42",
            GITHUB_RUN_ATTEMPT="2",
        )
        command = step("pull-deploy.yml", "Record successful deployment")["with"]["command"]
        subprocess.run(
            [sys.executable, "-I", str(ROOT / "actions/release-tools/run.py"), command],
            env=env, check=True, capture_output=True
        )
        self.receipt = json.loads((receipt_dir / "release.json").read_text())
        self.execute()
        self.assertIn("image_version=aaaaaaa", self.output.read_text())

    def test_run_validation(self):
        for field, value in [
            ("event", "pull_request"),
            ("head_branch", "feature"),
            ("path", ".github/workflows/untrusted.yml"),
            ("conclusion", "failure"),
            ("status", "in_progress"),
            ("head_sha", "bad"),
            ("head_repository", {"full_name": "fork/app"}),
            ("repository", {"full_name": "other/app"}),
        ]:
            with self.subTest(field=field):
                old = self.source_run[field]
                self.source_run[field] = value
                self.reject()
                self.source_run[field] = old

    def test_receipt_binding(self):
        for field, value in [
            ("repository", "other/app"),
            ("run_id", 9),
            ("run_attempt", 1),
            ("source_sha", "c" * 40),
            ("service", "bots/production"),
            ("host", "other"),
            ("schema_version", 2),
            ("image_version", "latest"),
            ("image_version", "c" * 7),
            ("image_version", "aaaaaaa\nx=y"),
            ("infra_sha", "../bad"),
        ]:
            with self.subTest(field=field, value=value):
                old = self.receipt[field]
                self.receipt[field] = value
                self.reject()
                self.receipt[field] = old

    def test_missing_expired_duplicate_and_old_attempt_receipts(self):
        original = copy.deepcopy(self.artifacts)
        for value in [
            [],
            original * 2,
            [dict(original[0], expired=True)],
            [dict(original[0], size_in_bytes=1000000)],
            [dict(original[0], name="staging-release-1")],
        ]:
            with self.subTest(artifacts=value):
                self.artifacts = value
                self.reject()

    def test_archive_traversal_rejected_without_extraction(self):
        self.archive_name = "../release.json"
        self.reject()

    def test_host_failure_missing_and_superseding_status(self):
        for statuses in [
            [],
            [{"context": "deploy/apps/bots/staging", "state": "failure"}],
            [{"context": "deploy/apps/trust", "state": "error"}] + self.statuses,
            [{"context": "deploy/apps/bots/staging", "state": "pending"}]
            + self.statuses,
        ]:
            with self.subTest(statuses=statuses):
                old = self.statuses
                self.statuses = statuses
                self.reject()
                self.statuses = old

    def test_invalid_run_id(self):
        self.env["RUN_ID"] = "42/../../other"
        self.reject()

    def test_host_status_pagination(self):
        pages = [
            [{"context": f"other/{i}", "state": "success"} for i in range(100)],
            self.statuses,
        ]
        original = self.api

        def api(path, **kwargs):
            if "/statuses?" in path:
                return pages[int(path.rsplit("=", 1)[1]) - 1]
            return original(path, **kwargs)

        with patch.dict(os.environ, self.env), patch.dict(self.scope, api=api):
            self.scope["main"]()
        self.assertTrue(self.output.exists())


class PullDeployTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.env = dict(
            os.environ,
            PATH=f"{self.bin}:{os.environ['PATH']}",
            GITHUB_OUTPUT=str(self.root / "output"),
            GITHUB_STEP_SUMMARY=str(self.root / "summary"),
            STRICT="true",
            GH_TOKEN="fake",
            REPO_TOKEN="fake",
            CALLER_REPO="org/app",
            CALLER_SHA="a" * 40,
            SHA="b" * 40,
            CHANGED="false",
            CONTEXT="deploy/apps/bots/staging",
            TRUST_CONTEXT="deploy/apps/trust",
            TIMEOUT_MINUTES="1",
        )

    def executable(self, name, script):
        path = self.bin / name
        path.write_text("#!/bin/bash\n" + script)
        path.chmod(0o755)

    def run_step(self, title):
        script = step("pull-deploy.yml", title)["run"]
        return subprocess.run(
            ["bash", "-e", "-o", "pipefail", "-c", script],
            env=self.env,
            cwd=self.root,
            text=True,
            capture_output=True,
            timeout=3,
            check=False,
        )

    def test_missing_status_fails_only_in_strict_mode(self):
        self.executable(
            "gh", "if [[ $* == *'-X POST'* ]]; then exit 0; fi\necho '[]'\n"
        )
        self.assertNotEqual(self.run_step("Wait for host deploy").returncode, 0)
        self.env["STRICT"] = "false"
        self.assertEqual(self.run_step("Wait for host deploy").returncode, 0)
        self.assertFalse((self.root / "output").exists())

    def test_only_host_success_emits_deployed(self):
        for state in ["success", "failure", "error"]:
            with self.subTest(state=state):
                self.executable(
                    "gh",
                    "if [[ $* == *'-X POST'* ]]; then exit 0; fi\necho '"
                    + json.dumps(
                        [
                            {
                                "context": self.env["CONTEXT"],
                                "state": state,
                                "description": "result",
                            }
                        ]
                    )
                    + "'\n",
                )
                result = self.run_step("Wait for host deploy")
                self.assertEqual(
                    result.returncode == 0, state == "success", result.stderr
                )
                output = self.root / "output"
                self.assertEqual(output.exists(), state == "success")
                if output.exists():
                    self.assertEqual(output.read_text(), "deployed=true\n")
                    output.unlink()

    def setup_guard(self):
        self.env.update(INPUTS_SERVICE="bots/staging", INPUTS_IMAGE_VERSION="a" * 7)
        (self.root / "services/bots/staging").mkdir(parents=True)
        (self.root / "inventory/host_vars").mkdir(parents=True)
        (self.root / "services/bots/staging/vars.yml").write_text("version: bbbbbbb\n")
        self.executable(
            "yq",
            "case $1 in *version_key*) echo version;; *repo*) echo org/app;; *) echo bbbbbbb;; esac\n",
        )
        self.executable(
            "gh",
            """case "$*" in
          *aaaaaaa...main*) echo ahead;;
          *compare*) echo "${COMPARE:-ahead}";;
          *) echo main;;
        esac
        """,
        )

    def test_stale_version_fails_strict_but_legacy_skips(self):
        self.setup_guard()
        self.env["COMPARE"] = "behind"
        self.assertNotEqual(
            self.run_step("Skip if a newer version is already pinned").returncode, 0
        )
        self.env["STRICT"] = "false"
        self.assertEqual(
            self.run_step("Skip if a newer version is already pinned").returncode, 0
        )
        self.assertIn("skip=true", (self.root / "output").read_text())

    def test_failed_comparison_is_not_accepted(self):
        self.setup_guard()
        self.executable("gh", "exit 1\n")
        self.assertNotEqual(
            self.run_step("Skip if a newer version is already pinned").returncode, 0
        )

    def test_cross_repo_service_is_rejected(self):
        self.setup_guard()
        self.env["CALLER_REPO"] = "other/app"
        self.assertNotEqual(
            self.run_step("Skip if a newer version is already pinned").returncode, 0
        )

    def test_successful_guard(self):
        self.setup_guard()
        result = self.run_step("Skip if a newer version is already pinned")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_preset_inputs(self):
        self.env.update(
            SERVICE="bots/staging",
            HOST="apps",
            VERSION="a" * 7,
            SOURCE_SHA="a" * 40,
            RECEIPT_NAME="staging-release",
            TIMEOUT="15",
            JOB_TIMEOUT="30",
        )
        self.assertEqual(self.run_step("Validate preset inputs").returncode, 0)
        for field, bad in [
            ("SERVICE", ""),
            ("HOST", "$(false)"),
            ("VERSION", "latest"),
            ("SOURCE_SHA", "b" * 40),
            ("RECEIPT_NAME", "../receipt"),
            ("TIMEOUT", "30"),
            ("STRICT", "false"),
        ]:
            with self.subTest(field=field):
                old = self.env[field]
                self.env[field] = bad
                self.assertNotEqual(
                    self.run_step("Validate preset inputs").returncode, 0
                )
                self.env[field] = old


class WiringTests(unittest.TestCase):
    def test_staged_production_requires_real_host_success(self):
        jobs = workflow("release-staged.yml")["jobs"]
        self.assertEqual(jobs["production"]["needs"], "staging")
        self.assertEqual(
            jobs["production"]["if"], "needs.staging.outputs.deployed == 'true'"
        )
        for name in ("staging", "production"):
            self.assertTrue(jobs[name]["with"]["require_deploy_success"])
            self.assertEqual(
                jobs[name]["with"]["image_version"], "${{ inputs.image_version }}"
            )

    def test_production_paths_share_serialization_key(self):
        staged = workflow("release-staged.yml")["jobs"]["production"]
        manual = workflow("release-promote.yml")["jobs"]["production"]
        self.assertEqual(staged["concurrency"], manual["concurrency"])
        self.assertFalse(staged["concurrency"]["cancel-in-progress"])
        self.assertEqual(manual["needs"], "resolve")
        self.assertEqual(
            manual["with"]["source_sha"], "${{ needs.resolve.outputs.source_sha }}"
        )

    def test_legacy_strict_default_is_false(self):
        # PyYAML uses YAML 1.1, where the unquoted key 'on' is a boolean.
        inputs = workflow("pull-deploy.yml")[True]["workflow_call"]["inputs"]
        self.assertFalse(inputs["require_deploy_success"]["default"])
        self.assertEqual(inputs["receipt_name"]["default"], "")

    def test_approval_rule_check(self):
        script = step(
            "_release-approval.yml", "Check production approval configuration", "check"
        )["run"]
        with tempfile.TemporaryDirectory() as directory:
            gh = Path(directory) / "gh"
            gh.write_text('#!/bin/bash\nprintf "%s" "$RULES"\n')
            gh.chmod(0o755)
            env = dict(
                os.environ,
                PATH=f"{directory}:{os.environ['PATH']}",
                RUNNER_TEMP=directory,
                REPOSITORY="org/app",
                ENVIRONMENT="production",
            )
            for rules, success in [
                ([], False),
                ([{"type": "branch_policy"}], False),
                ([{"type": "required_reviewers", "reviewers": []}], False),
                ([{"type": "required_reviewers", "reviewers": [{"id": 1}]}], True),
            ]:
                env["RULES"] = json.dumps({"protection_rules": rules})
                result = subprocess.run(
                    ["bash", "-e", "-o", "pipefail", "-c", script],
                    env=env,
                    text=True,
                    capture_output=True,
                    check=False,
                )
                self.assertEqual(result.returncode == 0, success, result.stderr)


if __name__ == "__main__":
    unittest.main()
