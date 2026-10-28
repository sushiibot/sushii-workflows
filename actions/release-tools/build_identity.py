"""Derive image identity."""

def main():
    import os, re
    from datetime import datetime, timezone
    image = os.environ['IMAGE'] or 'ghcr.io/' + os.environ['REPOSITORY'].lower()
    assert re.fullmatch(r'ghcr\.io/[a-z0-9._-]+/[a-z0-9._/-]+', image)
    artifact = os.environ['ARTIFACT']
    assert not artifact or re.fullmatch(r'[a-z0-9][a-z0-9-]*', artifact)
    path = os.environ['DOCKERFILE']
    assert re.fullmatch(r'[A-Za-z0-9_./-]+', path) and not path.startswith('/') and '..' not in path.split('/')
    assert not os.environ['BUILD_TARGET'] or re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', os.environ['BUILD_TARGET'])
    suffix = '-' + artifact if artifact else ''
    build_date = datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')
    with open(os.environ['GITHUB_OUTPUT'], 'a') as output:
        output.write(f'image={image}\ntag={image}:{os.environ["GITHUB_SHA"]}{suffix}\nbuild_date={build_date}\n')


if __name__ == "__main__":
    main()
