# Security Policy

## Supported versions

Arkon is pre-1.0. Only the latest `main` and the most recent tagged release receive
security fixes.

| Version | Supported |
|---------|-----------|
| `main`  | ✅ |
| v0.1.x  | ✅ |
| < v0.1  | ❌ |

## Reporting a vulnerability

**Do not open a public issue.** Public issues are indexed immediately, which discloses the
flaw to everyone before a fix exists.

Use GitHub private vulnerability reporting:

**<https://github.com/sondt99/arkon-nlm/security/advisories/new>**

Please include:

- The affected component and file path
- A description of the flaw and why it is exploitable
- Reproduction steps or a proof of concept
- The impact you believe it has (data exposure, privilege escalation, RCE…)
- Any suggested remediation

You will get an acknowledgement within **5 working days** and a remediation plan or an
explanation of why it is not a vulnerability within **15 working days**.

Please do not disclose publicly until a fix has shipped. If you intend to publish on a
fixed timeline, tell us in the report so we can coordinate.

## Scope

Arkon is self-hosted, so the deployer owns the network boundary, TLS termination, and
credential management. In scope for this policy:

- Authentication and session handling
- The RBAC / permission engine — any path that returns data a role should not see
- MCP token issuance, scoping, and revocation
- Prompt injection that escalates into unauthorised tool calls or cross-tenant data
  access. Arkon ingests untrusted documents and feeds them to an LLM with tool access, so
  this is an in-scope class, not a theoretical one.
- Injection of any kind (SQL, command, SSRF), file-upload handling, path traversal
- Secrets leaking into logs, API responses, or the container image

Out of scope:

- Findings that require an already-compromised admin account
- Missing hardening on a deployment that ignores the documented configuration — for
  example, running with the placeholder `SECRET_KEY` or the example passwords from
  `.env.docker.example`
- Denial of service via sheer request volume against an instance with no rate limiting in
  front of it
- Vulnerabilities in third-party model providers

## Operator hardening checklist

Before exposing an instance beyond localhost:

- [ ] `SECRET_KEY` replaced with a fresh random value — never the example
- [ ] `POSTGRES_PASSWORD`, `REDIS_PASSWORD`, `MINIO_SECRET_KEY` all changed
- [ ] `DEFAULT_ADMIN_PASSWORD` changed, and the default admin's password rotated after
      first login
- [ ] TLS terminated in front of nginx; no plaintext HTTP exposed
- [ ] Only the nginx port published — the API, Postgres, Redis, and MinIO ports stay
      internal to the Compose network
- [ ] `.env.docker` is not committed and is not readable by other users on the host
- [ ] MCP tokens issued per person, scoped to the minimum knowledge types, and revoked on
      offboarding
