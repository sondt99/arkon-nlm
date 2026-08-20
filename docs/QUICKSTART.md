# Quick start

Get a working Arkon on your machine in about 20 minutes.

Need production hardening, TLS, or a public hostname? Use [SETUP.md](SETUP.md) after this.

---

## 1. What you need

- Docker Engine + Docker Compose v2
- About 4 GB RAM free
- An API key from **one** of: Google AI Studio, OpenAI, Anthropic — **or** a local [Ollama](https://ollama.com) install

---

## 2. Clone and create the Docker network

```bash
git clone https://github.com/sondt99/arkon-nlm.git
cd arkon-nlm

# Compose joins this network as an *external* network. Create it once.
docker network create arkon_default
```

If you skip the network, `docker compose up` fails with “network arkon_default declared as external, but could not be found”.

---

## 3. Write your secrets

```bash
cp .env.docker.example .env.docker
```

Open `.env.docker` and set these. Do not leave the `change-me-…` values.

```env
SECRET_KEY=                    # python -c "import secrets; print(secrets.token_urlsafe(32))"
DEFAULT_ADMIN_EMAIL=admin@yourcompany.com
DEFAULT_ADMIN_PASSWORD=        # strong password
POSTGRES_PASSWORD=             # strong password
REDIS_PASSWORD=                # strong password
MINIO_ACCESS_KEY=              # NOT "minioadmin" — see below
MINIO_SECRET_KEY=              # strong password, not minioadmin123
DATABASE_URL=postgresql+asyncpg://arkon:<POSTGRES_PASSWORD>@postgres:5432/arkon
```

Every one of those is checked at startup, and the API **refuses to boot** on a known-weak value
rather than running with it. That includes `MINIO_ACCESS_KEY=minioadmin`, which the template still
ships — change it too, or the API exits with `MINIO_ACCESS_KEY is a known default.`

Leave these as they are for a local Docker run:

```env
CORS_ORIGINS=
NEXT_PUBLIC_API_URL=
MINIO_PUBLIC_ENDPOINT=localhost
MINIO_SECURE=false
NGINX_PORT=3119
```

`CORS_ORIGINS` empty is correct: the browser talks to nginx on one origin, so the API does not need a CORS wildcard.

> **File download links do not work on a non-80 port.** `MINIO_PUBLIC_ENDPOINT` goes straight into
> the presigned-URL signature, and nginx forwards `Host $host` — which strips the port. With
> `NGINX_PORT=3119` you get one of two failures and no third option: `localhost` produces links to
> port 80, where nothing is listening, and `localhost:3119` produces links that reach nginx and
> then fail MinIO's signature check with `SignatureDoesNotMatch`. Everything else on this page
> works either way; only downloading an uploaded file is affected. If you need it locally, set
> `NGINX_PORT=80` and keep `MINIO_PUBLIC_ENDPOINT=localhost` (then read the portal at
> `http://localhost` instead of `:3119`). Wiki **images** are unaffected — they are proxied through
> the API, not presigned.

---

## 4. Start the stack

Always pass `--env-file .env.docker`. Without it, Compose substitutes empty passwords and the containers cannot talk to each other.

```bash
docker compose --env-file .env.docker up -d --build
docker compose --env-file .env.docker ps
```

Migrations are applied by a one-shot `migrate` service that `api` waits on
(`depends_on: {migrate: {condition: service_completed_successfully}}`), so `up -d`
handles them and they cannot be skipped. They no longer run from every container's
entrypoint, which is what previously made `restart worker` re-migrate production.

**This fails closed.** If a pending revision would destroy data, `migrate` refuses and
exits 1, `api` never starts, and the schema is left untouched — verified against a
database at revision `013`, which yielded `migrate exit=1`, `api state=created`, and
the schema still at `013`. The API is never served against a stale schema.

To run the migration alone — to read the pre-flight output, or to pass
`ALLOW_DESTRUCTIVE_MIGRATIONS=1`:

```bash
docker compose --env-file .env.docker run --rm migrate
```

See DOCKER.md for the override and the backup command it prints.

Wait until `arkon_api`, `arkon_frontend`, and `arkon_nginx` are healthy (about 30–60 seconds).

Check the API through nginx:

```bash
curl http://localhost:3119/api/health
```

You want `"api": "healthy"` and `"database": "healthy"`.

The endpoint answers **503** — not 200 — when a dependency is unreachable, so an
orchestrator or uptime check can rely on the status code alone. If you have a monitor
asserting `200`, it will now correctly alert on a half-broken stack instead of passing.

---

## 5. Sign in

Open **http://localhost:3119**

Email and password = the `DEFAULT_ADMIN_*` values you set.

The default admin is created only when **no admin exists yet**. Changing the env vars later does not reset the password.

---

## 6. Configure AI

Go to **Settings**. You need at least embedding + LLM.

### Google (good default)

| Slot | Provider | Typical model |
|---|---|---|
| Embedding | Google | `text-embedding-004` |
| LLM | Google | `gemini-2.5-flash` or `gemini-2.5-pro` |
| Vision (optional) | Google | `gemini-2.0-flash` |

Paste the same Google AI Studio key into each slot you use. Click **Test** on each.

### OpenAI / Anthropic

Same idea: pick the provider, pick a model, paste the key, test.

Anthropic has no embedding model — keep embedding on Google, OpenAI, Ollama, 9Router, or Omniroute.

### Omniroute

Set in `.env.docker` (or `.env.local`) and recreate the API container — no Settings save required for the LLM slot:

```env
OMNIROUTE_API_KEY=sk-...
OMNIROUTE_BASE_URL=https://ai.nosiaht.com/v1
OMNIROUTE_MODEL=nosiaht
```

Or pick **Omniroute** in Settings, paste the key + base URL, **Fetch Models**, choose `nosiaht` or `glm/glm-5.3`, **Test**. This proxy's `nosiaht` alias currently routes to GLM-5.3; it does not offer embeddings.

### Ollama (offline)

On the host:

```bash
ollama pull nomic-embed-text
ollama pull qwen2.5:14b
```

In Settings, provider **Ollama**, base URL `http://host.docker.internal:11434/v1`, then **Fetch Models**.

---

## 7. Create a category and upload a file

1. **Knowledge Types → New** — e.g. `SOP` / slug `sop`.
2. **Documents → Upload** — drop a PDF or DOCX, pick that knowledge type, scope **Global**.
3. Watch the status: `pending` → `processing` → `plan_ready` or `ready`.

If it sits on `pending`, the worker is not running: `docker compose --env-file .env.docker logs worker`.

If you see **Review Plan**, open it and **Approve**. That is the MRP plan-review step. After approval the writer runs and the wiki pages appear.

---

## 8. Read the wiki

Open **Wiki**. Search or browse the pages that were just written.

---

## 9. Connect Claude (optional)

1. **Profile → MCP token** (or **Employees → [you] → Generate token**).
2. Copy the `ark_…` value. It is shown once.
3. Add this to Claude Desktop `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "arkon": {
      "url": "http://localhost:3119/mcp",
      "headers": {
        "Authorization": "Bearer ark_xxxxxxxx"
      }
    }
  }
}
```

Restart Claude Desktop. Ask: “Search the Arkon wiki for [something in your file].”

Details: [MCP.md](MCP.md).

---

## Checklist

- [ ] `arkon_default` network exists
- [ ] `.env.docker` has unique secrets (not the example strings, and not `minioadmin`)
- [ ] `docker compose --env-file .env.docker ps` shows healthy
- [ ] Login works at http://localhost:3119
- [ ] Embedding + LLM tests pass
- [ ] One document reached `ready` (or you approved its plan)
- [ ] Wiki shows at least one page

---

## Next

| Goal | Doc |
|---|---|
| Deploy on a server | [SETUP.md](SETUP.md) |
| Add people and roles | [ADMIN-GUIDE.md](ADMIN-GUIDE.md) |
| Understand the compiler | [WIKI.md](WIKI.md) |
| Something failed | [TROUBLESHOOTING.md](TROUBLESHOOTING.md) |
