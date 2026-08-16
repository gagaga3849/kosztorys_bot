#!/bin/sh
# Runs before the container's main process (see Dockerfile's ENTRYPOINT/CMD).
# Applies any pending Alembic migrations before starting the app - fails the container
# startup loudly if migrations fail, rather than starting an app pointed at a stale schema.
set -e

alembic upgrade head

exec "$@"
