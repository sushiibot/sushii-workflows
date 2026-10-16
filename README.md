# sushii-workflows

Reusable GitHub Actions workflows for sushiibot projects. See individual workflow
files in `.github/workflows/` for inputs and usage.

## Deploys

Services deploy through `pull-deploy.yml`: it bumps the image tag in sushii-ansible,
and the host's pull deployer applies the commit and reports back as a commit status.

It authenticates as the sushii-ci GitHub App:

| Name | Kind | Value |
|------|------|-------|
| `SUSHII_CI_CLIENT_ID` | variable | sushii-ci App client ID |
| `SUSHII_CI_PRIVATE_KEY` | secret | sushii-ci App private key |
