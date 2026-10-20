#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
CONTAINER_NAME="${CONTAINER_NAME:-discord-activity-bot}"
PREVIOUS_NAME="${CONTAINER_NAME}-previous"
IMAGE_NAME="${CONTAINER_NAME}"
STARTUP_WAIT_SECONDS="${STARTUP_WAIT_SECONDS:-10}"

cd "$ROOT_DIR"

for command in docker; do
    if ! command -v "$command" >/dev/null 2>&1; then
        echo "Required command not found: $command" >&2
        exit 1
    fi
done

for file in .env service-account.json Dockerfile; do
    if [[ ! -f "$ROOT_DIR/$file" ]]; then
        echo "Required file is missing: $ROOT_DIR/$file" >&2
        exit 1
    fi
done

IMAGE_TAG="${IMAGE_NAME}:local"
echo "Building image ${IMAGE_TAG}..."
docker build -t "$IMAGE_TAG" "$ROOT_DIR"

if docker container inspect "$PREVIOUS_NAME" >/dev/null 2>&1; then
    echo "Removing the older rollback container..."
    docker rm -f "$PREVIOUS_NAME" >/dev/null
fi

HAS_PREVIOUS=false
if docker container inspect "$CONTAINER_NAME" >/dev/null 2>&1; then
    echo "Keeping the current container as ${PREVIOUS_NAME} for rollback..."
    docker stop "$CONTAINER_NAME" >/dev/null
    docker rename "$CONTAINER_NAME" "$PREVIOUS_NAME"
    HAS_PREVIOUS=true
fi

rollback() {
    echo "The updated container failed to start; rolling back..." >&2
    docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true
    if [[ "$HAS_PREVIOUS" == true ]]; then
        docker rename "$PREVIOUS_NAME" "$CONTAINER_NAME"
        docker start "$CONTAINER_NAME" >/dev/null
        echo "Previous container restored. Its logs: docker logs -f $CONTAINER_NAME" >&2
    fi
}

if ! docker run -d \
    --name "$CONTAINER_NAME" \
    --restart unless-stopped \
    --env-file "$ROOT_DIR/.env" \
    -v "$ROOT_DIR/service-account.json:/app/service-account.json:ro" \
    "$IMAGE_TAG" >/dev/null; then
    rollback
    exit 1
fi

for ((attempt = 0; attempt < STARTUP_WAIT_SECONDS; attempt++)); do
    sleep 1
    STATUS="$(docker inspect --format '{{.State.Status}}' "$CONTAINER_NAME" 2>/dev/null || true)"
    RESTARTS="$(docker inspect --format '{{.RestartCount}}' "$CONTAINER_NAME" 2>/dev/null || echo 1)"
    if [[ "$STATUS" != "running" || "$RESTARTS" != "0" ]]; then
        rollback
        exit 1
    fi
done

echo "Bot is running with image ${IMAGE_TAG}."
docker ps --filter "name=^/${CONTAINER_NAME}$" --format 'table {{.Names}}\t{{.Status}}\t{{.Image}}'
if [[ "$HAS_PREVIOUS" == true ]]; then
    echo "Previous container retained as ${PREVIOUS_NAME} for rollback."
fi
