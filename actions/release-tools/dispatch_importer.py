"""Notify private release importer."""

def main():
    import json, os, subprocess
    payload = {"ref": "main", "inputs": {
        "source_repository": os.environ["GITHUB_REPOSITORY"],
        "run_id": os.environ["GITHUB_RUN_ID"],
        "target": os.environ["TARGET"]}}
    subprocess.run(["gh", "api", "--method", "POST",
                    "repos/sushiibot/sushii-ansible/actions/workflows/import-release.yml/dispatches",
                    "--input", "-"], input=json.dumps(payload), text=True, check=True)


if __name__ == "__main__":
    main()
