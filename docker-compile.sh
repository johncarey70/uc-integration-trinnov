#!/bin/bash
set -euo pipefail

# Only run on macOS
if [[ "$(uname)" != "Darwin" ]]; then
  echo "This script is intended to run on macOS."
  exit 0
fi

# Check if Docker is available
if ! docker system info > /dev/null 2>&1; then
  echo "Docker is not running."

  # Check if current user has an active GUI session
  ACTIVE_USER=$(stat -f "%Su" /dev/console)
  if [[ "$ACTIVE_USER" != "$USER" ]]; then
    echo "No active GUI session for user '$USER'."
    echo "To use Docker via SSH, make sure:"
    echo "  1. Automatic login is enabled for your user."
    echo "  2. Docker Desktop is set to launch on login."
    echo "  3. You are logged into the GUI after reboot."
  fi

  echo "Your user has an active console session, but Docker may not have launched."
  echo "Start Docker Desktop manually or reboot with login auto-start enabled."
fi

# Auto-detect integration directory
INTG_DIR=$(find . -maxdepth 1 -type d -name 'intg-*' -exec basename {} \; | head -n 1)
if [[ -z "$INTG_DIR" ]]; then
  echo "Error: No directory matching intg-* found in the current folder."
  exit 1
fi

ARCHIVE_NAME="uc-${INTG_DIR}-aarch64.tar.gz"
BUILD_ROOT="$PWD"
STAGING_DIR="artifacts"

# Create local temp dir
TMPDIR=$(mktemp -d /tmp/intg-build.XXXXXX)
echo "Working in local temp dir: $TMPDIR"

# Copy necessary files only
cp -R "$INTG_DIR" "$TMPDIR/"
cp driver.json requirements.txt assets/trinnov.png "$TMPDIR/"
WHEEL_FILE=$(ls pytrinnov-*.whl 2>/dev/null | head -n 1 || true)
if [[ -z "$WHEEL_FILE" ]]; then
  echo "Error: pytrinnov wheel file not found."
  exit 1
fi
cp "$WHEEL_FILE" "$TMPDIR/"

pushd "$TMPDIR" > /dev/null

# Run build in Docker
docker run --rm --name builder \
  --user=$(id -u):$(id -g) \
  -v "$TMPDIR":/workspace \
  docker.io/unfoldedcircle/r2-pyinstaller:3.11.12 \
  bash -c "cd /workspace && \
    python -m pip install $WHEEL_FILE && \
    python -m pip install -q --disable-pip-version-check -r requirements.txt && \
    pyinstaller --clean --onedir --name $INTG_DIR $INTG_DIR/driver.py"

# Package result
mkdir -p "$STAGING_DIR/bin"
mv dist/"$INTG_DIR"/* "$STAGING_DIR/bin/"
mv "$STAGING_DIR/bin/$INTG_DIR" "$STAGING_DIR/bin/driver"
cp driver.json "$STAGING_DIR/"
#cp trinnov.png "$STAGING_DIR/"
tar czf "$ARCHIVE_NAME" -C "$STAGING_DIR" .

# Copy archive back
cp "$ARCHIVE_NAME" "$BUILD_ROOT/"

popd > /dev/null
rm -rf "$TMPDIR"

# Show archive size
SIZE_MB=$(du -m "$BUILD_ROOT/$ARCHIVE_NAME" | cut -f1)
echo "Archive created: $ARCHIVE_NAME (${SIZE_MB} MB)"
