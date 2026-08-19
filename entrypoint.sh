#!/bin/sh
set -e

# Privilege-drop shim. This script does NOT run migrations any more — see migrate.sh and the
# `migrate` service in docker-compose.yml for why.
#
# Two modes:
#
#   Started as appuser (the image default, `USER appuser`): nothing to fix up. Named volumes
#   inherit appuser ownership from the image, so there is nothing to chown and no reason to
#   have been root in the first place. exec the command directly.
#
#   Started as root (`user: root` in compose, or a plain `docker run` that overrides the
#   user): create and chown the data directories first — a HOST BIND MOUNT arrives owned by
#   whoever owns it on the host, and only root can correct that — then drop to appuser via
#   gosu. This is the documented escape hatch for bind-mounted deployments.

if [ "$(id -u)" = "0" ]; then
    echo "entrypoint: running as root — fixing data directory ownership, then dropping to appuser"
    mkdir -p /data/notebooklm-session /app/temp_uploads
    chown -R appuser:appuser /data/notebooklm-session /app/temp_uploads
    exec gosu appuser "$@"
fi

exec "$@"
