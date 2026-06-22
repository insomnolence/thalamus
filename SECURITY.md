# Security Policy

Thalamus is a research/dogfood project at single-user scale, but it touches things worth handling
carefully: it runs an MCP server, connects to a Neo4j database, and ingests source code and
developer notes. If you find a vulnerability, thank you — please report it responsibly.

## Reporting a vulnerability

**Please do not open a public issue for a security problem.**

Use GitHub's private vulnerability reporting: go to the repository's **Security** tab →
**Report a vulnerability** (GitHub Security Advisories). That opens a private channel with the
maintainers.

Helpful to include:

- what the issue is and the impact you see,
- steps (or a minimal proof-of-concept) to reproduce,
- the version / commit you tested.

## What to expect

This is a small, unfunded project, so there is **no formal SLA**. Reports are triaged on a
best-effort basis. Fixes land on the default branch; there is no separate backport stream while the
project is pre-1.0.

## Scope notes

- The bundled `docker-compose.yml` and the example configs use a **local-dev placeholder password**
  and bind to `localhost`. Exposing Neo4j or the HTTP MCP transport beyond localhost without
  changing the password and setting `THALAMUS_HTTP_TOKEN` is a misconfiguration, not a Thalamus
  vulnerability — see [`examples/README.md`](examples/README.md) and the warnings in
  `scripts/serve-http.sh`.
- Hardening the brain against adversarial/untrusted ingested content is an active design area; see
  [`docs/deep-dives/security.md`](docs/deep-dives/security.md) for the threat model and roadmap.
