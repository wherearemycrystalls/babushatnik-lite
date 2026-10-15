# Security policy

## Reporting a vulnerability

Do not open a public issue for a credential leak, authentication bypass, data
exposure or remote-code-execution issue. Contact the repository owner privately
with the affected version, a minimal reproduction and the potential impact.
Acknowledgement is expected within three business days; a fix or mitigation is
prioritised according to impact.

## Operational policy

- Secrets live only in the protected production `.env` and GitHub Environment
  secrets. They are never committed, copied by the deployment workflow or
  printed by scripts.
- A release is built from a `v*` Git tag and published to GHCR with provenance
  and SBOM. The manual `Deploy production` workflow resolves the requested tag
  to an immutable OCI digest before it reaches the server.
- `DEPLOY_KNOWN_HOSTS` contains the production server's pinned host key. The
  workflow does not use `ssh-keyscan` or disable host-key verification.
- Dependency updates are proposed by Dependabot; CI runs `pip-audit`, dependency
  review for pull requests and weekly CodeQL analysis.
