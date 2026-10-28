"""Resolve successful staging receipt."""

import io
import json
import os
import re
import subprocess
import zipfile
from pathlib import Path


def require(condition, message):
    if not condition:
        raise SystemExit(message)


def api(path, *, infra=False, binary=False):
    env = dict(os.environ)
    if infra:
        env['GH_TOKEN'] = env['INFRA_TOKEN']
    data = subprocess.run(['gh', 'api', path], env=env, check=True,
                          capture_output=True, timeout=30).stdout
    return data if binary else json.loads(data)


def pages(path, key=None, *, infra=False):
    result = []
    for page in range(1, 101):
        data = api(f'{path}?per_page=100&page={page}', infra=infra)
        items = data[key] if key else data
        result.extend(items)
        if len(items) < 100:
            return result
    raise SystemExit('API pagination limit exceeded')


def main():
    repo = os.environ['REPOSITORY']
    run_id = os.environ['RUN_ID']
    workflow = os.environ['WORKFLOW_PATH']
    receipt_name = os.environ['RECEIPT_NAME']
    require(re.fullmatch(r'[1-9][0-9]*', run_id), 'Expected a numeric staging run ID')
    require(re.fullmatch(r'\.github/workflows/[A-Za-z0-9_-]+\.ya?ml', workflow), 'Invalid workflow path')
    require(re.fullmatch(r'[A-Za-z0-9_-]+', receipt_name), 'Invalid receipt name')
    metadata = api(f'repos/{repo}')
    run = api(f'repos/{repo}/actions/runs/{run_id}')
    require(run['repository']['full_name'] == repo and run['head_repository']['full_name'] == repo,
            'Run belongs to another repository or fork')
    require(run['event'] == 'push' and run['head_branch'] == metadata['default_branch'],
            'Only default-branch push runs can be promoted')
    require(run['path'] == workflow, 'Run did not use the configured staging workflow')
    require(run['status'] == 'completed' and run['conclusion'] == 'success', 'Staging run did not succeed')
    sha = run['head_sha']
    require(re.fullmatch(r'[0-9a-f]{40}', sha), 'Invalid source SHA')
    artifacts = pages(f'repos/{repo}/actions/runs/{run_id}/artifacts', 'artifacts')
    matches = [a for a in artifacts if a['name'] == f"{receipt_name}-{run['run_attempt']}"]
    require(len(matches) == 1, 'No unique receipt for the latest run attempt; rerun staging')
    artifact = matches[0]
    require(not artifact['expired'] and 0 < artifact['size_in_bytes'] <= 65536, 'Receipt expired or oversized')
    data = api(f"repos/{repo}/actions/artifacts/{artifact['id']}/zip", binary=True)
    require(len(data) <= 65536, 'Receipt archive too large')
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        require(archive.namelist() == ['release.json'], 'Unexpected receipt archive contents')
        require(archive.getinfo('release.json').file_size <= 16384, 'Receipt too large')
        receipt = json.loads(archive.read('release.json'))
    expected = dict(schema_version=1, repository=repo, source_sha=sha,
                    run_id=int(run_id), run_attempt=run['run_attempt'],
                    service=os.environ['STAGING_SERVICE'], host=os.environ['STAGING_HOST'])
    require(all(receipt.get(k) == v for k, v in expected.items()), 'Receipt does not match staging run and target')
    version = receipt.get('image_version', '')
    infra_sha = receipt.get('infra_sha', '')
    require(isinstance(version, str) and re.fullmatch(r'[0-9a-f]{7,40}', version)
            and sha.startswith(version), 'Receipt tag does not match the tested source commit')
    require(isinstance(infra_sha, str) and re.fullmatch(r'[0-9a-f]{40}', infra_sha), 'Invalid infrastructure SHA')
    statuses = pages(f'repos/sushiibot/sushii-ansible/commits/{infra_sha}/statuses', infra=True)
    latest = {}
    for status in statuses:
        latest.setdefault(status['context'], status['state'])
    context = f"deploy/{expected['host']}/{expected['service']}"
    require(latest.get(context) == 'success', 'Host has no successful staging status for this receipt')
    require(latest.get(f"deploy/{expected['host']}/trust") not in ('failure', 'error'), 'Host rejected the infrastructure commit')
    with Path(os.environ['GITHUB_OUTPUT']).open('a') as output:
        output.write(f'image_version={version}\nsource_sha={sha}\n')
    with Path(os.environ['GITHUB_STEP_SUMMARY']).open('a') as summary:
        summary.write(f'Promoting `{version}` from [successful staging run]'
                      f'(https://github.com/{repo}/actions/runs/{run_id}).\n')


if __name__ == '__main__':
    main()