FROM nginx:alpine
# Bake the reverse-proxy config into the image instead of bind-mounting it from
# the host, so nginx has no dependency on the E: drive mount (which Docker
# Desktop's WSL2 file-sharing intermittently drops). Rebuild to apply edits:
#   docker compose -f docker-compose.yml build nginx && docker compose ... up -d nginx
COPY rate-limit.conf /etc/nginx/conf.d/00-rate-limit.conf
COPY nginx.conf /etc/nginx/conf.d/default.conf
