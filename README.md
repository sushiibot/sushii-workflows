# sushii-workflows

Reusable GitHub Actions workflows for sushiibot projects.

## Release presets

Three presets wrap the existing `pull-deploy.yml` deployment mechanism:

- `release-auto.yml`: deploy after the caller's checks and build succeed.
- `release-staged.yml`: deploy staging, wait for host success, then deploy the same tag after production approval.
- `release-promote.yml`: promote a successful staging run through the production approval gate.

Projects own tests, triggers, and `needs` dependencies. These workflows do not define project checks.
The automatic and staged presets accept default-branch push events.
The automatic preset can also accept explicit manual dispatch with `allow_manual: true`.
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
  permissions: {contents: read, actions: read, statuses: write}
  with:
    image_version: ${{ needs.docker-build.outputs.image_version }}
  secrets:
    SUSHII_CI_PRIVATE_KEY: ${{ secrets.SUSHII_CI_PRIVATE_KEY }}
```

This job requires a successful build from the same push. It accepts a SHA tag that matches the source commit.
Private repository ancestry checks require `contents: read`.
An image appearing in the registry does not trigger deployment.

### Approval without staging

Use `release-auto.yml` with `environment: production` and `require_approval: true`.
This preserves project checks and the build, then waits for production approval before deployment.
Configure required reviewers first. Missing reviewers cause failure before any infrastructure write.
The caller must grant `actions: read` for this configuration check.

For existing manual publishers, set `allow_manual: true`.
Manual dispatch remains restricted to the default branch and uses that run's exact source revision.
This option does not select arbitrary historical versions or replace validated staging promotion.

Optional `timeout_minutes` and `job_timeout_minutes` preserve service-specific deployment budgets.
The job timeout must exceed the deployment timeout by at least five minutes.

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
  permissions: {contents: read, actions: read, statuses: write}
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
Automatic registry releases now have a separate authenticated path, described below.

Existing callers can continue to use `pull-deploy.yml` without the strict inputs.
Its default legacy behavior remains available, including the old missing-status shortcut.
The shared deployer inherits caller token permissions so old callers need not grant new scopes.
Callers must explicitly restrict permissions. The presets request only the scopes shown in the examples.

## Automatic registry releases

For an enabled private registry target, keep project checks as dependencies of `build-verified.yml`.
Call the pinned `release-auto.yml` with `target` and `artifact_digest` from the builder output.
Pass the existing `SUSHII_CI_PRIVATE_KEY` secret and expose the `SUSHII_CI_CLIENT_ID` variable.

The publisher signs an explicit request without using the App key.
A separate job uses that key to dispatch the private importer immediately after upload.
Its installation token requests only Actions write on `sushii-ansible`.
The importer independently verifies the request and build evidence against private registry policy.
The source job waits for the exact authenticated host receipt.

The CI App must have Actions write access to `sushii-ansible`; accept the updated installation permissions.
The private importer also uses the App to read source artifacts and publish source commit statuses.
This reuses the existing trusted-project App key; job token restrictions do not restrict a stolen App key.
No webhook server or importer cron is required. Manual importer dispatch remains available for recovery.
Approval-required services keep their existing preset paths during this pilot.

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

### Additional registry artifacts

`build-verified.yml` supports optional `image_name`, `dockerfile`, `build_target`, and
`artifact_name` inputs. Give multiple builds in one run distinct artifact names and pass
those same names to `release-auto.yml`. Project jobs still own their check dependencies.

For repositories without artifact attestations, set the builder's `attest: false` and
publisher's `provenance: github-actions`. The private registry must explicitly authorize
this mode. It verifies retained GitHub Actions evidence and trusts the source workflow;
this mode does not provide independent builder attestations. Evidence is retained for
90 days, subject to repository limits. Hosts that lag beyond retention need audited recovery.

For release bundles, pass `artifact_kind: github-release-bundle`, `provenance: github-actions`,
`release_id`, `release_asset_id`, and `artifact_digest`. IDs are strings at the reusable
workflow boundary. Obtain the digest from the uploaded asset, including when reusing an
existing release. `allow_manual: true` supports default-branch workflow dispatch only when
the private policy also allows it. It does not replace environment approval.

New registry callers must grant `packages: read` to the release job, even for bundles.
The publisher uses this permission to read private OCI manifests when applicable.
Approved promotion presets retain their existing behavior.
