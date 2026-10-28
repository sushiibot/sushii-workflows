"""Construct release intent."""

def main():
    import json, os, re
    import hashlib, urllib.parse, urllib.request
    from pathlib import Path
    target, digest = os.environ['TARGET'], os.environ['DIGEST']
    assert re.fullmatch(r'[a-z0-9][a-z0-9-]*', target), 'Invalid target'
    assert re.fullmatch(r'sha256:[0-9a-f]{64}', digest), 'Invalid digest'
    request = dict(schema_version=1, target=target, artifact_digest=digest,
                   source_sha=os.environ['GITHUB_SHA'], run_id=int(os.environ['GITHUB_RUN_ID']),
                   run_attempt=int(os.environ['GITHUB_RUN_ATTEMPT']))
    mode = os.environ.get('PROVENANCE', 'attested')
    kind = os.environ.get('ARTIFACT_KIND', 'oci')
    if os.environ.get('REQUIRE_APPROVAL', 'false') == 'true':
        import subprocess
        assert mode == 'attested' and kind == 'oci', 'Approval requires attested OCI evidence'
        assert os.environ.get('ALLOW_MANUAL', 'false') == 'false'
        assert os.environ['GITHUB_EVENT_NAME'] == 'push' and request['run_attempt'] == 1
        name = os.environ['ENVIRONMENT']
        assert name
        repository = os.environ['GITHUB_REPOSITORY']
        def api(path):
            return json.loads(subprocess.check_output(['gh', 'api', path], text=True))
        environment = api(f"repos/{repository}/environments/{urllib.parse.quote(name, safe='')}")
        assert environment['name'] == name
        environment_id = environment['id']
        assert type(environment_id) is int and environment_id > 0
        rules = [r for r in environment['protection_rules'] if r['type'] == 'required_reviewers']
        assert len(rules) == 1 and rules[0]['reviewers']
        reviewers = rules[0]['reviewers']
        assert all(r['type'] == 'User' and type(r['reviewer']['id']) is int and r['reviewer']['id'] > 0
                   for r in reviewers), 'Only individual reviewers are supported'
        reviewer_ids = {r['reviewer']['id'] for r in reviewers}
        history = api(f"repos/{repository}/actions/runs/{request['run_id']}/approvals")
        decisions = [d for d in history if any(e.get('id') == environment_id or e.get('name') == name
                                              for e in d.get('environments', []))]
        assert len(decisions) == 1, 'Missing or ambiguous approval history'
        decision = decisions[0]
        assert decision['state'] == 'approved'
        assert any(e.get('id') == environment_id and e.get('name') == name
                   for e in decision['environments'])
        reviewer_id = decision['user']['id']
        assert type(reviewer_id) is int and reviewer_id in reviewer_ids
        request.update(schema_version=3, approval=dict(environment=name, environment_id=environment_id,
                                                      reviewer_id=reviewer_id))
    directory = Path(os.environ['RUNNER_TEMP']) / 'release-request'
    directory.mkdir()
    (directory / 'request.json').write_text(json.dumps(request, sort_keys=True) + '\n')
    mode = os.environ.get('PROVENANCE', 'attested')
    kind = os.environ.get('ARTIFACT_KIND', 'oci')
    assert mode in ('attested', 'github-actions')
    repository = os.environ['GITHUB_REPOSITORY'].lower()
    if kind == 'oci':
        import base64
        image = os.environ.get('IMAGE') or 'ghcr.io/' + repository
        assert re.fullmatch(r'ghcr\.io/[a-z0-9._-]+/[a-z0-9._/-]+', image)
        package = image.removeprefix('ghcr.io/')
        query = urllib.parse.urlencode({'service': 'ghcr.io', 'scope': f'repository:{package}:pull'})
        headers = {}
        if os.environ.get('GH_TOKEN'):
            basic = base64.b64encode(('token:' + os.environ['GH_TOKEN']).encode()).decode()
            headers['Authorization'] = 'Basic ' + basic
        token_req = urllib.request.Request('https://ghcr.io/token?' + query, headers=headers)
        token = json.load(urllib.request.urlopen(token_req, timeout=30))['token']
        req = urllib.request.Request(f'https://ghcr.io/v2/{package}/manifests/{digest}', headers={
            'Authorization': 'Bearer ' + token,
            'Accept': 'application/vnd.oci.image.index.v1+json,application/vnd.docker.distribution.manifest.list.v2+json,application/vnd.oci.image.manifest.v1+json'})
        with urllib.request.urlopen(req, timeout=30) as response:
            manifest = response.read(4 * 1024 * 1024 + 1)
        assert len(manifest) <= 4 * 1024 * 1024
        assert 'sha256:' + hashlib.sha256(manifest).hexdigest() == digest
    else:
        import subprocess
        assert kind == 'github-release-bundle' and mode == 'github-actions'
        ids = [os.environ['RELEASE_ID'], os.environ['RELEASE_ASSET_ID']]
        assert all(re.fullmatch(r'[1-9][0-9]{0,18}', value) for value in ids)
        release_id, asset_id = map(int, ids)
        metadata = json.loads(subprocess.check_output(['gh', 'api', f'repos/{repository}/releases/assets/{asset_id}'], text=True))
        assert metadata['id'] == asset_id and metadata['digest'] == digest
        assert 0 < metadata['size'] <= 32 * 1024 * 1024
        request.update(schema_version=2, release_id=release_id, release_asset_id=asset_id)
        (directory / 'request.json').write_text(json.dumps(request, sort_keys=True) + '\n')
        manifest = (json.dumps(dict(kind=kind, release_id=release_id, release_asset_id=asset_id,
                                   name=metadata['name'], size=metadata['size']), sort_keys=True) + '\n').encode()
    (directory / 'artifact-manifest.json').write_bytes(manifest)
    if mode == 'github-actions':
        for name in ('intent.sigstore.json', 'build.sigstore.json'):
            (directory / name).write_text('{}\n')


if __name__ == "__main__":
    main()
