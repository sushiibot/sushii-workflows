"""Check the action boundary, not just individual helper behavior."""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

from test_release_presets import ROOT, WORKFLOWS

ACTION = ROOT / "actions/release-tools"


class ReleaseToolsTests(unittest.TestCase):
    def test_output_forwarding(self):
        action = yaml.safe_load((ACTION / "action.yml").read_text())
        self.assertEqual(action["runs"]["using"], "composite")
        self.assertEqual(action["runs"]["steps"][0]["id"], "run")
        for name in ("deployed", "image", "tag", "build_date", "image_version", "source_sha"):
            self.assertEqual(action["outputs"][name]["value"],
                             "${{ steps.run.outputs." + name + " }}")

    def test_entrypoint_is_isolated_from_caller_modules_and_optimization(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "json.py").write_text("raise RuntimeError('caller code executed')")
            output = root / "receipt"
            env = dict(os.environ, PYTHONPATH=directory, PYTHONOPTIMIZE="1",
                       RECEIPT_DIR=str(output), REPOSITORY="org/app", SOURCE_SHA="a" * 40,
                       IMAGE_VERSION="a" * 7, SERVICE="bots/staging", HOST="apps",
                       INFRA_SHA="b" * 40, GITHUB_RUN_ID="42", GITHUB_RUN_ATTEMPT="1")
            result = subprocess.run([sys.executable, "-I", str(ACTION / "run.py"),
                                     "deployment_receipt"], cwd=root, env=env,
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((output / "release.json").is_file())
            env.update(IMAGE="invalid", ARTIFACT="", DOCKERFILE="Dockerfile", BUILD_TARGET="")
            result = subprocess.run([sys.executable, "-I", str(ACTION / "run.py"),
                                     "build_identity"], cwd=root, env=env, capture_output=True)
            self.assertNotEqual(result.returncode, 0)  # Assertions remain enabled.

    def test_entrypoint_rejects_arbitrary_script_paths(self):
        result = subprocess.run([sys.executable, "-I", str(ACTION / "run.py"),
                                 "../../caller/script"], capture_output=True)
        self.assertNotEqual(result.returncode, 0)

    def test_workflows_only_select_existing_helpers(self):
        commands = set()
        for path in WORKFLOWS.glob("*.yml"):
            for job in yaml.safe_load(path.read_text())["jobs"].values():
                for step in job.get("steps", []):
                    if "sushii-workflows/actions/release-tools@" in step.get("uses", ""):
                        command = step["with"]["command"]
                        self.assertTrue((ACTION / (command + ".py")).is_file())
                        self.assertRegex(step["uses"].split("@")[1], r"^[0-9a-f]{40}$")
                        commands.add(command)
        self.assertEqual(len(commands), 8)


if __name__ == "__main__":
    unittest.main()
