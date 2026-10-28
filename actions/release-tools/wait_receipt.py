"""Wait for exact host receipt."""

def main():
    import json, os, subprocess, time
    context = f"release/{os.environ['TARGET']}/{os.environ['GITHUB_RUN_ID']}/{os.environ['GITHUB_RUN_ATTEMPT']}"
    path = f"repos/{os.environ['GITHUB_REPOSITORY']}/commits/{os.environ['GITHUB_SHA']}/statuses?per_page=100"
    deadline = time.monotonic() + 1800
    while time.monotonic() < deadline:
        pages = json.loads(subprocess.check_output(['gh', 'api', '--paginate', '--slurp', path], text=True))
        statuses = [status for page in pages for status in page]
        status = next((s for s in statuses if s['context'] == context and
                       (s.get('creator') or {}).get('id') == 342252709), None)
        if status and status['state'] == 'success':
            assert status['description'] == 'deployed ' + os.environ['DIGEST'], 'Receipt digest mismatch'
            with open(os.environ['GITHUB_OUTPUT'], 'a') as output:
                output.write('deployed=true\n')
            print('Host deployed the requested immutable artifact.')
            break
        if status and status['state'] in ('failure', 'error'):
            raise SystemExit('Release rejected or deployment failed; inspect the release status.')
        time.sleep(15)
    else:
        raise SystemExit('No matching host deployment receipt within 30 minutes.')


if __name__ == "__main__":
    main()
