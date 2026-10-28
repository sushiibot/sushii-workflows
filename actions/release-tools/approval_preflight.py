"""Validate approval contract before the gate."""

def main():
    import json, os, subprocess, urllib.parse
    if os.environ['REQUIRE_APPROVAL'] == 'true':
        assert os.environ['PROVENANCE'] == 'attested'
        assert os.environ['ARTIFACT_KIND'] == 'oci'
        assert os.environ['ALLOW_MANUAL'] == 'false'
        assert os.environ['GITHUB_EVENT_NAME'] == 'push'
        assert os.environ['GITHUB_RUN_ATTEMPT'] == '1', 'Approved releases require a fresh push run'
        name = os.environ['ENVIRONMENT']
        assert name, 'An approval environment is required'
        path = f"repos/{os.environ['GITHUB_REPOSITORY']}/environments/{urllib.parse.quote(name, safe='')}"
        environment = json.loads(subprocess.check_output(['gh', 'api', path], text=True))
        assert environment['name'] == name
        assert type(environment['id']) is int and environment['id'] > 0
        rules = [r for r in environment['protection_rules'] if r['type'] == 'required_reviewers']
        assert len(rules) == 1 and rules[0]['reviewers'], 'Configure required individual reviewers'
        assert all(r['type'] == 'User' and type(r['reviewer']['id']) is int and r['reviewer']['id'] > 0
                   for r in rules[0]['reviewers']), 'Only individual reviewers are supported'


if __name__ == "__main__":
    main()
