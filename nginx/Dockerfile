# Pinned by version AND digest. This was `FROM nginx:alpine` — no version whatsoever — in
# the single ingress for the entire stack, so a routine `docker compose build nginx` could
# bump nginx across minor lines without anyone choosing to. It had already drifted: the tag
# resolves to 1.31.3 today, several minor releases past what the config was written against.
# The digest below is exactly what `nginx:alpine` resolves to now, so this pin changes
# nothing that is running — it only stops the next silent move. Bumps come as reviewable
# Dependabot PRs (.github/dependabot.yml).
FROM nginx:1.31-alpine@sha256:db35bfc6b2951e7f8a72db5db120288c127ffaeeb4a6d4b95a26fead017d5913

# Bake the reverse-proxy config into the image instead of bind-mounting it from
# the host, so nginx has no dependency on the E: drive mount (which Docker
# Desktop's WSL2 file-sharing intermittently drops). Rebuild to apply edits:
#   docker compose -f docker-compose.yml build nginx && docker compose ... up -d nginx
#
# 00- prefix: conf.d is included alphabetically, and the limit_req_zone / map directives in
# http-common.conf must be defined before the server block in nginx.conf references them.
COPY http-common.conf /etc/nginx/conf.d/00-http-common.conf
COPY nginx.conf /etc/nginx/conf.d/default.conf

# Fail the build, not the deploy, on a config typo. Without this a bad directive only
# surfaces when the container starts and the whole stack's ingress is already down.
RUN nginx -t
