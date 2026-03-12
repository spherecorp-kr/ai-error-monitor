#!/bin/bash
# Build Lambda deployment packages using Docker (Python 3.12 x86_64 matching Lambda runtime)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
DIST_DIR="$PROJECT_DIR/dist"

rm -rf "$DIST_DIR"
mkdir -p "$DIST_DIR"

echo "==> Installing dependencies (Python 3.12 x86_64 via Docker)..."
docker run --rm \
  --platform linux/amd64 \
  --entrypoint "" \
  -v "$PROJECT_DIR":/var/task \
  -w /var/task \
  public.ecr.aws/lambda/python:3.12 \
  pip install -r requirements.txt -t /var/task/dist/package --quiet

echo "==> Building collector.zip..."
cd "$DIST_DIR/package"
cp -r "$PROJECT_DIR/lambdas" .
cp -r "$PROJECT_DIR/config" .
zip -r "$DIST_DIR/collector.zip" . -x "*.pyc" "__pycache__/*" > /dev/null
cd "$PROJECT_DIR"

echo "==> Building analyzer.zip..."
cp "$DIST_DIR/collector.zip" "$DIST_DIR/analyzer.zip"

echo "==> Done!"
ls -lh "$DIST_DIR"/*.zip
