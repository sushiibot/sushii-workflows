# sushii-workflows

Reusable GitHub Actions workflows for sushiibot projects.

## Release presets

Three presets wrap the existing `pull-deploy.yml` deployment mechanism:

- `release-auto.yml`: deploy after the caller's checks and build succeed.
- `release-staged.yml`: deploy staging, wait for host success, then deploy the same tag after production approval.
- `release-promote.yml`: promote a successful staging run through the production approval gate.

Projects own tests, triggers, and `needs` dependencies. These workflows do not define project checks.
The automatic and staged presets accept only default-branch push events.
The promotion preset accepts only manual dispatch events on the default branch.

These presets are ready for a controlled pilot. No project caller or GitHub environment configuration changes accompany their implementation.
The production bot keeps its existing host-run blue/green workflow. Its migration requires a separate review of timeout and executor requirements.

### One-time configuration

Configure the existing deployment credentials in each caller repository:

- Variable `SUSHII_CI_CLIENT_ID`: the sushii-ci GitHub App client ID.
- Secret `SUSHII_CI_PRIVATE_KEY`: the sushii-ci GitHub App private key.

Configure repository variables for the selected preset:

- Automatic: `RELEASE_SERVICE` and optionally `RELEASE_HOST`.
- Staged or promotion: `STAGING_SERVICE`, `PRODUCTION_SERVICE`, and optionally `STAGING_HOST` and `PRODUCTION_HOST`.

Host variables default to `apps`. Service variables have no defaults and must identify existing registered services.
Explicit workflow inputs can override these variables for repositories with several instances.
These variables are temporary placement mappings. They are not the future private registry or a confidentiality boundary.
Workflow logs and deployment statuses can still expose internal placement details through the existing deployer.

Before using a production preset, configure required reviewers on its GitHub environment.
The environment defaults to `production`. The presets stop if the environment lacks required reviewers or its configuration cannot be read.
GitHub owns the actual approval gate, reviewer eligibility, self-review rules, and administrator bypass policy.
Required reviewers in private repositories depend on the GitHub plan.

### Automatic release

Add this job to existing CI. Replace `WORKFLOW_COMMIT_SHA` with the reviewed shared-workflow commit SHA.
Replace `test` and `docker-build` with the actual project job IDs.

```yaml
release:
  needs: [test, docker-build]
  uses: sushiibot/sushii-workflows/.github/workflows/release-auto.yml@WORKFLOW_COMMIT_SHA
  permissions: {contents: read, statuses: write}
  with:
    image_version: ${{ needs.docker-build.outputs.image_version }}
  secrets:
    SUSHII_CI_PRIVATE_KEY: ${{ secrets.SUSHII_CI_PRIVATE_KEY }}
```

This job requires a successful build from the same push. It accepts a SHA tag that matches the source commit.
Private repository ancestry checks require `contents: read`.
An image appearing in the registry does not trigger deployment.

### Staging followed by approval

Use the same caller job with these two changes:

```yaml
uses: sushiibot/sushii-workflows/.github/workflows/release-staged.yml@WORKFLOW_COMMIT_SHA
permissions: {contents: read, actions: read, statuses: write}
```

The staged preset uses `STAGING_SERVICE` and `PRODUCTION_SERVICE`.
Optional inputs select `staging_environment` and `production_environment`.
The defaults are `staging` and `production`.
Production starts only after the host reports staging success and GitHub releases the protected production job.
Both jobs receive the exact same tag.

### Manual promotion

For staging CI, use the automatic preset with staging placement and a receipt:

```yaml
release:
  needs: [test, docker-build]
  uses: sushiibot/sushii-workflows/.github/workflows/release-auto.yml@WORKFLOW_COMMIT_SHA
  permissions: {contents: read, statuses: write}
  with:
    service: ${{ vars.STAGING_SERVICE }}
    host: ${{ vars.STAGING_HOST || 'apps' }}
    environment: staging
    image_version: ${{ needs.docker-build.outputs.image_version }}
    receipt_name: staging-release
  secrets:
    SUSHII_CI_PRIVATE_KEY: ${{ secrets.SUSHII_CI_PRIVATE_KEY }}
```

Add this separate caller workflow as `.github/workflows/promote.yml`:

```yaml
name: Promote staging
on:
  workflow_dispatch:
    inputs:
      staging_run_id:
        description: Successful staging run ID from its GitHub URL
        required: true
        type: string
permissions: {}
jobs:
  promote:
    uses: sushiibot/sushii-workflows/.github/workflows/release-promote.yml@WORKFLOW_COMMIT_SHA
    permissions: {contents: read, actions: read, statuses: write}
    with:
      staging_run_id: ${{ inputs.staging_run_id }}
    secrets:
      SUSHII_CI_PRIVATE_KEY: ${{ secrets.SUSHII_CI_PRIVATE_KEY }}
```

The default source workflow is `.github/workflows/ci.yml`.
If staging uses another file, set `staging_workflow` in the caller YAML.
Do not expose the trusted workflow path or deployment destinations as dispatch inputs.

The resolver requires a successful default-branch push run from the same repository and the configured workflow.
It reads the receipt for the latest run attempt and checks the source SHA, tag, staging service, and host.
It also checks the infrastructure commit's current host status.
It does not execute or extract code from the receipt archive.
Expired receipts fail closed. Run staging again to obtain fresh evidence.
Receipts expire after 30 days, or sooner if repository policy or an operator removes them.
Rerunning only some jobs can leave no receipt for the latest attempt. Rerun the complete staging workflow in that case.

### Approval links and notices

Job summaries link to the current GitHub run for approval and status.
For manual promotion, notices can link to the project's `actions/workflows/promote.yml` page and show the successful staging run ID.
The workflow dispatch form requires that ID. A link does not prefill or submit a release request.
Discord notifications can use these ordinary links. These presets do not send Discord messages or require an interaction server.

### Guarantees and limits

The presets enable strict host-result handling in `pull-deploy.yml`.
Missing status, failed ancestry checks, another repository's service, and older pins stop the release.
Only an actual host success sets the `deployed` output.
A pin without deployment evidence cannot authorize promotion.

The production jobs share a concurrency group per repository and production service.
An active job is not cancelled. GitHub can replace pending jobs, so this is not a durable FIFO queue.
After approval, the deployer checks ancestry again before changing the pin.
Existing host trust checks and holds still apply.

This stage uses existing SHA tags and the existing sushii-ci infrastructure-write credential.
It preserves a tag, not an immutable image digest. A moved container tag remains outside these guarantees.
Receipts rely on trusted default-branch workflows and the existing host status publisher.
They are not signed provenance or an authorization boundary against a compromised project workflow.
The registry publisher, digest pinning, least-privilege release authorization, and durable receipts remain separate migration work.

Existing callers can continue to use `pull-deploy.yml` without the strict inputs.
Its default legacy behavior remains available, including the old missing-status shortcut.
The shared deployer inherits caller token permissions so old callers need not grant new scopes.
Callers must explicitly restrict permissions. The presets request only the scopes shown in the examples.

## Validation

Run the offline tests:

```sh
uv run --python 3.13 --with pyyaml==6.0.2 python -m unittest discover -s tests -v
```

Run the workflow syntax checker:

```sh
go run github.com/rhysd/actionlint/cmd/actionlint@v1.7.7
```

Tests execute the embedded promotion resolver and shell guards with fake API responses.
They do not decrypt secrets, mutate GitHub configuration, or deploy services.
A live pilot must still exercise GitHub approval, artifact transfer, and host status reporting.

## References

- [Reusable workflow permissions and nesting](https://docs.github.com/en/actions/reference/workflows-and-actions/reusing-workflow-configurations)
- [Deployment environments and approval rules](https://docs.github.com/en/actions/reference/workflows-and-actions/deployments-and-environments)
- [Workflow artifact API](https://docs.github.com/en/rest/actions/artifacts)
