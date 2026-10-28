"""Accept only build evidence from the artifact."""

def main():
    import os, shutil
    from pathlib import Path
    directory = Path(os.environ['RUNNER_TEMP']) / 'release-build-evidence'
    entries = list(directory.iterdir())
    source = directory / 'build.sigstore.json'
    assert entries == [source] and source.is_file() and not source.is_symlink(), 'Unexpected build artifact contents'
    assert 0 < source.stat().st_size <= 4 * 1024 * 1024, 'Invalid build evidence size'
    destination = Path(os.environ['RUNNER_TEMP']) / 'release-request/build.sigstore.json'
    with destination.open('xb') as output, source.open('rb') as evidence:
        shutil.copyfileobj(evidence, output)


if __name__ == "__main__":
    main()
