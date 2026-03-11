#!/bin/bash
# Build Lambda deployment packages
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
DIST_DIR="$PROJECT_DIR/dist"

rm -rf "$DIST_DIR"
mkdir -p "$DIST_DIR"

echo "==> Installing dependencies..."
pip install -r "$PROJECT_DIR/requirements.txt" -t "$DIST_DIR/package" --quiet

echo "==> Building collector.zip..."
cd "$DIST_DIR/package"
cp -r "$PROJECT_DIR/lambdas" .
cp -r "$PROJECT_DIR/config" .
zip -r "$DIST_DIR/collector.zip" . -x "*.pyc" "__pycache__/*" > /dev/null
cd "$PROJECT_DIR"

echo "==> Building analyzer.zip..."
# Analyzer uses the same package (shared code)
cp "$DIST_DIR/collector.zip" "$DIST_DIR/analyzer.zip"

echo "==> Done!"
ls -lh "$DIST_DIR"/*.zip
