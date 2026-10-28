"""Record successful deployment."""

def main():
    import json, os
    from pathlib import Path
    Path(os.environ["RECEIPT_DIR"]).mkdir(parents=True, exist_ok=True)
    fields = {k.lower(): os.environ[k] for k in (
        "REPOSITORY", "SOURCE_SHA", "IMAGE_VERSION", "SERVICE", "HOST", "INFRA_SHA")}
    fields.update(schema_version=1, run_id=int(os.environ["GITHUB_RUN_ID"]),
                  run_attempt=int(os.environ["GITHUB_RUN_ATTEMPT"]))
    Path(os.environ["RECEIPT_DIR"], "release.json").write_text(json.dumps(fields))


if __name__ == "__main__":
    main()
