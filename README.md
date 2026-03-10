# sushii-workflows

Reusable GitHub Actions workflows for sushiibot projects. See individual workflow
files in `.github/workflows/` for inputs and usage.

## Secrets

Four secrets are required by the deploy workflow. Set them once org-wide (requires
`admin:org` token scope) or per-repo:

```bash
cp .env.example .env
# fill in values

gh secret set -f .env --org sushiibot
# or: gh secret set -f .env --repo sushiibot/<repo>
```

| Secret | How to get it |
|--------|--------------|
| `ANSIBLE_REPO_TOKEN` | GitHub PAT with `repo` scope |
| `ANSIBLE_SSH_PRIVATE_KEY` | Deploy private key from sushii-ansible |
| `ANSIBLE_KNOWN_HOSTS` | `ssh-keyscan` output for deployment servers |
| `ANSIBLE_VAULT_PASSWORD` | Vault password from sushii-ansible |

Multiline values (`ANSIBLE_SSH_PRIVATE_KEY`, `ANSIBLE_KNOWN_HOSTS`) must be
double-quoted in `.env`. See `.env.example`.
