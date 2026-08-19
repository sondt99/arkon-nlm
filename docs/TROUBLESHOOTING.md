# Troubleshooting

Work top-down: Compose status → health → the service log that is actually failing.

```bash
docker compose --env-file .env.docker ps
curl -s http://localhost:3119/api/health
docker compose --env-file .env.docker logs --tail=80 api
docker compose --env-file .env.docker logs --tail=80 worker
```

In the default Docker setup the API is **not** on `localhost:5055`. Use nginx on **3119**, or `docker exec`.

```bash
docker exec arkon_api python -c "import urllib.request; print(urllib.request.urlopen('http://localhost:5055/health').read())"
```

---

## Start-up

### `network arkon_default declared as external`

```bash
docker network create arkon_default
docker compose --env-file .env.docker up -d
```

### Container exits immediately

```bash
docker compose --env-file .env.docker logs api
```

| Message | Cause |
|---|---|
| `SECRET_KEY is still the default value` | You copied the example and did not change it |
| `DEFAULT_ADMIN_PASSWORD is a weak default` | Same — pick a real password |
| `MINIO_ACCESS_KEY is a known default` | `MINIO_ACCESS_KEY=minioadmin` is refused — the template still ships it |
| `MINIO_SECRET_KEY is a known default` | Checked independently of the access key; change both |
| `REDIS_PASSWORD is unset or a known default` | Must be non-empty and match Compose's `--requirepass` |
| `DATABASE_URL still embeds a default password` | Still contains `arkon_secret` or `change-me-postgres-password` |
| `CORS_ORIGINS is '*'` | Leave it empty behind nginx, or set explicit origins |
| password authentication failed | `POSTGRES_PASSWORD` ≠ password in `DATABASE_URL` |
| Redis `NOAUTH` / `invalid password` | `REDIS_PASSWORD` empty in env but Compose started Redis with `--requirepass` |

Always start with `--env-file .env.docker`.

### `port is already allocated`

Something else owns `3119`. Set `NGINX_PORT=3120` in `.env.docker` and recreate nginx.

### Health is `degraded`

`GET /api/health` reports `api`, `database`, `worker`.

| Field | Look at |
|---|---|
| `database: error` | `docker compose --env-file .env.docker logs postgres` — usually a password mismatch |
| `worker: error` | Redis password, or Redis not healthy yet |

---

## Login

### “Too many login attempts”

Nginx + Redis rate-limit the login path. Wait 5 minutes or:

```bash
docker exec arkon_redis redis-cli -a "$REDIS_PASSWORD" --no-auth-warning KEYS 'arkon:login_attempts:*'
```

### Admin password in `.env.docker` does nothing

The env password is used **only** to seed the first admin. After that, change the password in **Profile**.

### Cannot reach the UI from another machine

nginx binds `127.0.0.1:3119` by default. Use SSH tunnel, change the bind in `docker-compose.yml`, or put another proxy on the `arkon_default` network.

---

## Uploads and the wiki

### Status stays `pending`

The document worker is down or cannot see Redis.

```bash
docker compose --env-file .env.docker logs worker
docker compose --env-file .env.docker ps worker
```

### Status `error`

Open the source row and read the error. Typical causes:

- Embedding or LLM not configured / test failed
- Provider quota or invalid key
- File is empty, encrypted, or a scan-only PDF with no extractable text and no vision model

Retry: **Documents → ⋯ → Retry**.

### Stuck on **Review Plan**

That is expected when auto-approve is off. Open the plan and **Approve** or **Reject**. To skip review on a trusted pipeline, set `MRP_AUTO_APPROVE_PLAN=true` and recreate the worker.

### Wiki pages missing after `ready`

Check worker logs for `COMMIT`. A failed commit rolls back the whole batch. Also confirm you are looking at the same scope (global vs a workspace) and knowledge type your user can see.

### Images broken in the wiki

Presigned URLs use `MINIO_PUBLIC_ENDPOINT`. On Docker it must be a host the **browser** can open, and nginx must serve `/arkon-files/`. After changing it, recreate `api` (and rebuild frontend only if you also changed `NEXT_PUBLIC_API_URL`).

---

## Settings / AI

### Test LLM fails

- Key prefix: Google `AIza…`, OpenAI `sk-…`, Anthropic `sk-ant-…`
- Ollama URL from inside Docker is `http://host.docker.internal:11434/v1`, not `localhost`
- 9Router: click **Fetch Models** after setting the base URL

### Changed embedding model, search is empty or errors

Dimension must match a table (768 / 1024 / 1536 / 3072). The Settings UI starts a re-embed job. Watch **Settings → Embeddings** until it finishes.

---

## MCP / Claude

| Symptom | Fix |
|---|---|
| Tools do not appear | Restart Claude Desktop after editing the config |
| Connection refused | URL must be reachable from the **client**. Docker: `http://localhost:3119/mcp` |
| Authentication required | Header is `Authorization: Bearer ark_…` (include `Bearer `) |
| Invalid or inactive token | Generate a new one under Profile or Employees |
| Empty search results | Employee’s `doc:read` scope or workspace membership excludes those sources |
| 404 on `/mcp` | Old setups without the nginx `/mcp` location — update and recreate nginx |

---

## NotebookLM

| Symptom | Fix |
|---|---|
| Import verifies `false` | Cookies went stale. Re-export and paste within a few minutes. See [notebooklm-auth.md](notebooklm-auth.md) |
| Works then dies after hours | Import a **master token** (preferred) so the worker can mint fresh cookies |
| Session lost after recreate | Volume `notebooklm_data` was wiped |

---

## Frontend

### UI loads, every API call fails

`NEXT_PUBLIC_API_URL` was set to `http://localhost:5055` and the API port is not published. Clear it, then:

```bash
docker compose --env-file .env.docker up -d --build frontend
```

### CORS error in the browser

You are hitting the API on a different origin than the UI. Either:

- Use nginx (same origin), `CORS_ORIGINS=` empty, or
- Set `CORS_ORIGINS` to the exact UI origin (scheme + host + port).
