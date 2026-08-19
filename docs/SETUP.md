# Setup and deploy

Two ways to run Arkon:

| Mode | When |
|---|---|
| **Docker** (this page) | Server or “just run it” |
| **Host + local services** | Daily development — [HOW_TO_RUN.md](HOW_TO_RUN.md) |

Repository: [github.com/sondt99/arkon-nlm](https://github.com/sondt99/arkon-nlm)

---

## What the Docker stack is

```text
Browser  ──►  nginx :3119
                 ├── /           → frontend :3000
                 ├── /api/*      → api :5055
                 ├── /mcp        → api :5055
                 └── /arkon-files/* → minio :9000

api  +  worker  +  worker_skills
         │
    postgres · redis · minio
```

Default Compose publishes **one** host port: `127.0.0.1:3119`. Postgres, Redis, MinIO, and the API are not exposed on the host. That is intentional.

---

## 1. Server prerequisites

- Linux (Ubuntu 22.04+ is fine) or Docker Desktop
- Docker Engine 24+ and Compose v2
- 4+ CPU, 8 GB RAM recommended
- An AI provider key (or Ollama on the host)

```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker "$USER"
```

---

## 2. Clone

```bash
git clone https://github.com/sondt99/arkon-nlm.git
cd arkon-nlm
docker network create arkon_default
```

`arkon_default` is declared `external: true` so a LAN reverse proxy (nginx-proxy-manager, Caddy, another nginx) can join the same network and reach `arkon_nginx` by name. Compose will not create it for you.

---

## 3. Configure `.env.docker`

```bash
cp .env.docker.example .env.docker
```

### Secrets (required)

Generate them. Do not use the example strings — the API refuses to start with the default `SECRET_KEY` / admin password / MinIO factory pair.

```env
SECRET_KEY=                          # secrets.token_urlsafe(32)
DEFAULT_ADMIN_EMAIL=admin@yourcompany.com
DEFAULT_ADMIN_PASSWORD=

POSTGRES_USER=arkon
POSTGRES_PASSWORD=
POSTGRES_DB=arkon
DATABASE_URL=postgresql+asyncpg://arkon:<POSTGRES_PASSWORD>@postgres:5432/arkon

REDIS_PASSWORD=
MINIO_ACCESS_KEY=something-other-than-minioadmin
MINIO_SECRET_KEY=
```

`POSTGRES_*` and `DATABASE_URL` must match. Changing MinIO keys after the first start requires wiping the MinIO volume (`docker compose --env-file .env.docker down -v` — this deletes files).

### Public URL (required on a real server)

```env
# Hostname browsers use. No port if nginx (or another proxy) serves :80/:443
# and forwards /arkon-files/ to MinIO.
MINIO_PUBLIC_ENDPOINT=arkon.example.com
MINIO_SECURE=true          # https
NGINX_PORT=3119            # or 80 if this container is the public listener

# Leave empty so the Next.js app calls /api on the same origin.
NEXT_PUBLIC_API_URL=

# Leave empty for same-origin. Only set this if the browser origin ≠ API origin.
CORS_ORIGINS=
```

`NEXT_PUBLIC_API_URL` is baked in at **image build** time. If you change it, rebuild:

```bash
docker compose --env-file .env.docker up -d --build frontend
```

---

## 4. Start

```bash
docker compose --env-file .env.docker up -d --build
docker compose --env-file .env.docker ps
```

Always pass `--env-file .env.docker`. Compose variable substitution (`${REDIS_PASSWORD}` and friends) does **not** read `env_file:` on a service — that only injects vars into the container process.

The API container runs `alembic upgrade head` on every start (`entrypoint.sh`). You do not run migrations by hand on first boot.

---

## 5. First login

Open `http://<host>:3119` (or your domain) and sign in with `DEFAULT_ADMIN_EMAIL` / `DEFAULT_ADMIN_PASSWORD`.

Then **Settings**:

| Slot | Required | Used for |
|---|---|---|
| Embedding | Yes | Semantic search |
| LLM | Yes | Wiki compilation, plan, verify |
| Vision | No | Image captions on PDF pages |
| Chatbot | No | Falls back to LLM |
| Claude Code gateway | No | `/api/claude-gateway` |

Test each slot before uploading documents.

---

## 6. Put it behind TLS

The bundled nginx listens on HTTP. Terminate TLS on a host proxy and forward to `arkon_nginx:80` on the `arkon_default` network.

Then set:

```env
MINIO_SECURE=true
MINIO_PUBLIC_ENDPOINT=arkon.example.com
```

Do **not** publish `5055`, `5432`, `6379`, or `9000` on `0.0.0.0`. The default Compose file already keeps them internal.

If you need LAN access without another proxy, change the nginx port mapping from `127.0.0.1:${NGINX_PORT}:80` to `${NGINX_PORT}:80` in `docker-compose.yml`.

---

## 7. Useful commands

```bash
# Status
docker compose --env-file .env.docker ps

# Logs
docker compose --env-file .env.docker logs -f api
docker compose --env-file .env.docker logs -f worker

# Shell / migrations (after an upgrade)
docker exec arkon_api alembic current
docker exec arkon_api alembic upgrade head

# Rebuild one service
docker compose --env-file .env.docker up -d --build frontend
```

---

## Volumes

| Volume | Contents |
|---|---|
| `postgres_data` | Database |
| `redis_data` | Queue persistence |
| `minio_data` | Uploaded files, images, skill packages |
| `notebooklm_data` | NotebookLM cookies / master token |
| `temp_uploads` | In-flight uploads |

NotebookLM session files live in `notebooklm_data`. If you wipe that volume you must reconnect NotebookLM.

---

## Upgrade

```bash
git pull
docker compose --env-file .env.docker up -d --build
```

The API entrypoint applies new Alembic revisions. Read [CHANGELOG.md](../CHANGELOG.md) before upgrading.

---

## What not to do

- Do not start Compose without `--env-file .env.docker`.
- Do not set `CORS_ORIGINS=*` on a network-reachable API (the process refuses unless `ARKON_ALLOW_CORS_WILDCARD=1`).
- Do not point `NEXT_PUBLIC_API_URL` at `http://localhost:5055` on a server — browsers on other machines cannot reach it, and the port is not published.
- Do not delete named volumes unless you intend to wipe the knowledge base.
