#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

APP_NAME="PacktoWorkEU"
PKG_NAME="packtoworkeu"
# Derive the package architecture from the host dpkg instead of hard-coding
# "amd64". The hard-coded value silently produced an amd64 .deb on an arm64
# machine (Raspberry Pi, Graviton, ...), where dpkg then refuses it with
# "package architecture (amd64) does not match system (arm64)". build_rpm.sh
# has always done this correctly via `rpmbuild --eval '%{_arch}'`.
DEB_ARCH="$(dpkg --print-architecture 2>/dev/null || true)"
if [ -z "$DEB_ARCH" ]; then
  echo "WARNING: 'dpkg --print-architecture' produced nothing; defaulting to amd64." >&2
  DEB_ARCH="amd64"
fi

BUILD_DIR=".build/deb"
WORK_DIR=".build/deb-work"
FROZEN_DIR=".build/deb-frozen"
VENV_DIR=".build/deb-venv"
DIST_DIR="dist"
APP_DIR="$BUILD_DIR/opt/$PKG_NAME"
DEBIAN_DIR="$BUILD_DIR/DEBIAN"

need() {
  command -v "$1" >/dev/null 2>&1 || { echo "Missing required command: $1" >&2; exit 1; }
}

# dpkg-deb only writes a local .deb file, so building must never run as root.
# Running with sudo would also create root-owned build artifacts and could
# trigger Debian's PEP 668 "externally-managed-environment" protection.
if [ "$(id -u)" -eq 0 ]; then
  cat >&2 <<'EOF'
ERROR: do not run build_deb.sh with sudo.
This script only creates dist/packtoworkeu_<version>_<arch>.deb and does not
need root. Run it as your normal user:

  ./build_deb.sh
EOF
  exit 1
fi

need python3
need dpkg-deb

# Debian/Ubuntu may ship Python without the venv/ensurepip modules. Fail with an
# actionable message instead of falling back to a system-wide pip install.
if ! python3 -c "import venv" >/dev/null 2>&1; then
  cat >&2 <<'EOF'
ERROR: Python's venv module is missing.
Install it first, then run ./build_deb.sh again:

  sudo apt install python3-venv
EOF
  exit 1
fi

# All build dependencies live in this disposable, git-ignored environment.
# This avoids PEP 668 and never modifies the system Python installation.
if [ -e "$VENV_DIR" ] && [ ! -w "$VENV_DIR" ]; then
  echo "ERROR: $VENV_DIR is not writable by the current user." >&2
  echo "A previous sudo build may have created it. Remove it once, then retry:" >&2
  echo "  sudo rm -rf $VENV_DIR && ./build_deb.sh" >&2
  exit 1
fi

# A venv can be present and even executable yet have no working pip (for
# example it was created while python3-venv was still missing, or a previous
# run was interrupted midway). Test that pip actually runs inside it instead of
# trusting the mere existence of bin/python, and rebuild the venv when it is
# not usable. This is what caused "bin/python: No module named pip".
venv_python="$VENV_DIR/bin/python"
venv_has_pip() {
  [ -x "$1" ] || return 1
  "$1" -m pip --version >/dev/null 2>&1 || return 1
  # `pip --version` never imports the install command, so a pip whose tree
  # is partially missing (interrupted upgrade, half-synced .build, disk
  # full) passes it yet dies on `pip install` with e.g.
  # "ModuleNotFoundError: No module named 'pip._internal.operations.build'".
  # `pip install --help` imports that machinery without installing anything.
  "$1" -m pip install --help >/dev/null 2>&1
}

if [ -e "$VENV_DIR" ] && ! venv_has_pip "$venv_python"; then
  echo "Existing $VENV_DIR has no working pip - recreating it..."
  rm -rf "$VENV_DIR"
fi

if [ ! -x "$venv_python" ]; then
  echo "Creating build virtual environment at $VENV_DIR..."
  if ! python3 -m venv "$VENV_DIR"; then
    echo "ERROR: could not create $VENV_DIR" >&2
    echo "If it exists with root-owned files, remove it and retry:" >&2
    echo "  sudo rm -rf $VENV_DIR && ./build_deb.sh" >&2
    exit 1
  fi
fi

# Some minimal installs produce a venv whose pip module is present but not
# bootstrapped; ask ensurepip to finish the job before giving up.
if ! venv_has_pip "$venv_python"; then
  echo "Bootstrapping pip inside $VENV_DIR..."
  "$venv_python" -m ensurepip --upgrade >/dev/null 2>&1 || true
fi

if ! venv_has_pip "$venv_python"; then
  cat >&2 <<EOF
ERROR: could not provide pip inside $VENV_DIR.
The venv module is available but ensurepip/pip is missing. Install the full
venv package and try again:

  sudo apt install python3-venv

If it still fails, remove the environment and rebuild it:

  rm -rf $VENV_DIR && ./build_deb.sh
EOF
  exit 1
fi

PYTHON="$venv_python"

VERSION="$("$PYTHON" - <<'PY'
from pathlib import Path
import re
text = Path('pyproject.toml').read_text(encoding='utf-8')
m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.M)
if not m:
    raise SystemExit('Unable to read version from pyproject.toml')
print(m.group(1))
PY
)"

echo "Installing build dependencies into $VENV_DIR..."
PIP_DISABLE_PIP_VERSION_CHECK=1 "$PYTHON" -m pip install -r requirements.txt

# Fail fast when a dependency installed but is not actually usable. A broken
# greenlet (its compiled extension missing) makes `playwright.sync_api` raise
# ModuleNotFoundError, which the scanner swallows and later reports as an opaque
# "Playwright is required for DOM fallback" for every target.
if ! "$PYTHON" -c "from playwright.sync_api import sync_playwright" >/dev/null 2>&1; then
  echo "ERROR: playwright is not usable inside $VENV_DIR (broken/missing dependency)." >&2
  echo "Recreate the build environment and retry:" >&2
  echo "  rm -rf $VENV_DIR && ./build_deb.sh" >&2
  exit 1
fi

# Only wipe our own staging areas: dist/ also holds the .exe/.deb/.rpm
# artifacts, which must survive so all three formats can be built side by side.
rm -rf "$BUILD_DIR" "$WORK_DIR" "$FROZEN_DIR"
mkdir -p "$APP_DIR" "$DEBIAN_DIR" "$DIST_DIR"

echo "Building $APP_NAME from PacktoWorkEU.spec (safe mode — no binary stripping)..."
"$PYTHON" -m PyInstaller \
  --clean \
  --noconfirm \
  --workpath "$WORK_DIR" \
  --distpath ".build/deb-frozen" \
  PacktoWorkEU.spec

mkdir -p "$APP_DIR"
cp -a ".build/deb-frozen/$APP_NAME/." "$APP_DIR/"

mkdir -p "$BUILD_DIR/usr/bin"
cat > "$BUILD_DIR/usr/bin/packtoworkeu" <<'EOL'
#!/bin/sh
# PacktoWorkEU launcher
# The binary lives at /opt/packtoworkeu/PacktoWorkEU after installing the package.

APP_BIN="/opt/packtoworkeu/PacktoWorkEU"

# Verify the binary exists and is executable.
if [ ! -f "$APP_BIN" ]; then
  echo "PacktoWorkEU: ERROR — $APP_BIN not found." >&2
  echo "  The .deb package may not have installed correctly." >&2
  echo "  Try reinstalling:  sudo dpkg --purge packtoworkeu && sudo dpkg -i packtoworkeu_*.deb" >&2
  exit 1
fi
if [ ! -x "$APP_BIN" ]; then
  echo "PacktoWorkEU: ERROR — $APP_BIN is not executable." >&2
  echo "  Try:  sudo chmod +x $APP_BIN" >&2
  exit 1
fi

exec "$APP_BIN" "$@"
EOL
chmod 755 "$BUILD_DIR/usr/bin/packtoworkeu"

mkdir -p "$BUILD_DIR/usr/share/applications"
cat > "$BUILD_DIR/usr/share/applications/packtoworkeu.desktop" <<EOL
[Desktop Entry]
Version=1.0
Type=Application
Name=PacktoWorkEU
GenericName=Job Sponsorship Finder
Comment=Find visa-sponsoring jobs from official ATS boards
Exec=/usr/bin/packtoworkeu
Icon=packtoworkeu
Terminal=false
Categories=Office;Network;
Keywords=jobs;visa;sponsorship;careers;
StartupWMClass=PacktoWorkEU
EOL

# Install hicolor icon theme entries (16px .. 512px) so the desktop entry's
# Icon=packtoworkeu resolves on the panel / app grid.
if [ ! -f "packtoworkeu/data/icons/packtoworkeu_512.png" ]; then
  echo "ERROR: hicolor source icons not found (packtoworkeu_512.png missing) -" >&2
  echo "the app would install with a blank icon. Aborting build." >&2
  exit 1
fi
for size in 16 24 32 48 64 96 128 256 512; do
  src="packtoworkeu/data/icons/packtoworkeu_${size}.png"
  if [ ! -f "$src" ]; then
    echo "WARNING: missing icon source $src - skipping this size." >&2
    continue
  fi
  dst="$BUILD_DIR/usr/share/icons/hicolor/${size}x${size}/apps"
  mkdir -p "$dst"
  cp "$src" "$dst/packtoworkeu.png"
done

cat > "$DEBIAN_DIR/control" <<EOL
Package: $PKG_NAME
Version: $VERSION
Section: utils
Priority: optional
Architecture: $DEB_ARCH
Maintainer: PacktoWorkEU <packtoworkeu@localhost>
Depends: util-linux, libc6, libgcc-s1, libstdc++6, libasound2t64, libatk-bridge2.0-0t64, libatk1.0-0t64, libatspi2.0-0t64, libcairo2, libcups2t64, libdbus-1-3, libdrm2, libgbm1, libglib2.0-0t64, libnspr4, libnss3, libpango-1.0-0, libx11-6, libxcb1, libxcomposite1, libxdamage1, libxext6, libxfixes3, libxkbcommon0, libxrandr2, libxcb-cursor0, libxcb-icccm4, libxcb-image0, libxcb-keysyms1, libxcb-render-util0, libxcb-util1, libxcb-xkb1, libxkbcommon-x11-0
Installed-Size: $(du -sk "$BUILD_DIR" | cut -f1)
Description: Job sponsorship search application
 A local desktop app for finding jobs with visa sponsorship signals.
EOL

cat > "$DEBIAN_DIR/postinst" <<'EOL'
#!/bin/sh
set -e
# Update desktop database and icon cache
if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database /usr/share/applications >/dev/null 2>&1 || true
fi
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
  gtk-update-icon-cache -f -t /usr/share/icons/hicolor >/dev/null 2>&1 || true
fi

# No browser download during installation. Use Browser setup explicitly.

exit 0
EOL
chmod 755 "$DEBIAN_DIR/postinst"

cat > "$DEBIAN_DIR/prerm" <<'EOL'
#!/bin/sh
set -eu

# Require the installed binary to be closed before upgrade/remove; never kill
# unrelated processes by name.
command -v readlink >/dev/null 2>&1 || { echo "readlink is required to check running PacktoWorkEU instances" >&2; exit 1; }
for entry in /proc/[0-9]*/exe; do
  target="$(readlink "$entry" 2>/dev/null || true)"
  case "$target" in
    "/opt/packtoworkeu/PacktoWorkEU"|"/opt/packtoworkeu/PacktoWorkEU (deleted)")
      echo "Close the installed PacktoWorkEU (/opt/packtoworkeu/PacktoWorkEU) before upgrading or removing the package." >&2
      exit 1
      ;;
  esac
done

# Each user's custom paths are handled under that user's own privileges.
if [ "${1:-}" = "remove" ]; then
  command -v runuser >/dev/null 2>&1 || { echo "runuser is required for safe cleanup" >&2; exit 1; }
  [ -x /opt/packtoworkeu/PacktoWorkEU ] || { echo "Restore the installed executable before removing PacktoWorkEU data" >&2; exit 1; }
  accounts=$(getent passwd) || { echo "Unable to enumerate accounts for cleanup" >&2; exit 1; }
  while IFS=: read -r name unused uid gid gecos home shell; do
    case "$uid" in ''|*[!0-9]*) continue ;; esac
    [ "$uid" -eq 0 ] || [ "$uid" -ge 1000 ] || [ -e "$home/.packtoworkeu" ] || [ -L "$home/.packtoworkeu" ] || continue
    [ -d "$home" ] || continue
    case "$home" in /|/nonexistent|/var/empty) continue ;; esac
    if ! runuser -u "$name" -- env -i HOME="$home" USER="$name" LOGNAME="$name" PATH=/usr/sbin:/usr/bin:/sbin:/bin \
      /opt/packtoworkeu/PacktoWorkEU --purge-user-data; then
      echo "PacktoWorkEU: data cleanup failed for $name; removal stopped. Review permissions and retry." >&2
      exit 1
    fi
  done <<PACKTOWORKEU_ACCOUNT_LIST
$accounts
PACKTOWORKEU_ACCOUNT_LIST
fi

exit 0
EOL
chmod 755 "$DEBIAN_DIR/prerm"

cat > "$DEBIAN_DIR/postrm" <<'EOL'
#!/bin/sh
# Data was removed safely in prerm while the app binary still existed.
# Never follow user-controlled paths here as root, including on purge/upgrade.
exit 0
EOL
chmod 755 "$DEBIAN_DIR/postrm"

# ── Filesystem permissions ──────────────────────────────────────────────────
# WSL drvfs mounts (/mnt/...), NTFS and FAT report every file as 0777 and ignore
# chmod. dpkg-deb then refuses the control directory outright ("control
# directory has bad permissions 777"), and even if it did not, every installed
# file would be world-writable. Normalise in place when the filesystem honours
# chmod; otherwise stage the tree on a Linux filesystem and build from there.
BUILD_TREE="$BUILD_DIR"
STAGE_DIR=""
PROBE_ROOT=""
cleanup() {
  if [ -n "$PROBE_ROOT" ]; then rm -rf "$PROBE_ROOT"; fi
  if [ -n "$STAGE_DIR" ]; then rm -rf "$STAGE_DIR"; fi
  return 0
}
trap cleanup EXIT

if chmod 755 "$DEBIAN_DIR" 2>/dev/null \
   && [ "$(stat -c '%a' "$DEBIAN_DIR" 2>/dev/null || echo 777)" = "755" ]; then
  echo "Normalising package permissions in place…"
  find "$BUILD_DIR" -type d -exec chmod 755 {} + 2>/dev/null || true
  find "$BUILD_DIR" -type f -exec chmod go-w {} + 2>/dev/null || true
  chmod 755 "$BUILD_DIR/DEBIAN/"* 2>/dev/null || true
  chmod 755 "$BUILD_DIR/usr/bin/packtoworkeu" 2>/dev/null || true
  chmod 755 "$BUILD_DIR/opt/$PKG_NAME/$APP_NAME" 2>/dev/null || true
else
  echo "This filesystem does not support Unix permissions (WSL /mnt, NTFS, FAT)." >&2
  echo "Staging the package tree on a Linux filesystem instead…" >&2
  STAGE_DIR="$(mktemp -d)"
  cp -a "$BUILD_DIR/." "$STAGE_DIR/"
  chmod -R u=rwX,go=rX "$STAGE_DIR"
  chmod 755 "$STAGE_DIR/DEBIAN"
  chmod 755 "$STAGE_DIR/DEBIAN/"* 2>/dev/null || true
  chmod 755 "$STAGE_DIR/usr/bin/packtoworkeu" 2>/dev/null || true
  chmod 755 "$STAGE_DIR/opt/$PKG_NAME/$APP_NAME" 2>/dev/null || true
  BUILD_TREE="$STAGE_DIR"
fi

APP_DIR="$BUILD_TREE/opt/$PKG_NAME"
if [ ! -x "$APP_DIR/$APP_NAME" ]; then
  echo "ERROR: $APP_NAME binary not found or not executable in $APP_DIR." >&2
  ls -la "$APP_DIR/" >&2 || true
  exit 1
fi

# Lean package: Playwright driver is collected, but no browser binary/cache.
# Validate the final staged payload (after permission staging, before compression).
echo "Smoke-testing finalized staged payload (dependencies, 8-tab GUI, payload audit)..."
"$APP_DIR/$APP_NAME" --self-check || { echo "Packaged dependency self-check failed" >&2; exit 1; }
"$APP_DIR/$APP_NAME" --self-check-gui || { echo "Packaged GUI self-check failed" >&2; exit 1; }
if [ "${PACKTOWORKEU_VALIDATE_BROWSER:-0}" = "1" ]; then
  "$APP_DIR/$APP_NAME" --self-check-browser || exit 1
fi
"$PYTHON" build_hooks/verify_payload.py "$APP_DIR"

# ── Package ─────────────────────────────────────────────────────────────────
DEB_OUT="$DIST_DIR/${PKG_NAME}_${VERSION}_${DEB_ARCH}.deb"
DEB_TMP="$DIST_DIR/.${PKG_NAME}_${VERSION}_${DEB_ARCH}.deb.partial"
rm -f "$DEB_TMP"

PROBE_ROOT="$(mktemp -d)"
PROBE_DIR="$PROBE_ROOT/control"
mkdir -p "$PROBE_DIR/DEBIAN"
cat > "$PROBE_DIR/DEBIAN/control" <<EOL
Package: packtoworkeu-probe
Version: 0
Architecture: $DEB_ARCH
Maintainer: PacktoWorkEU <packtoworkeu@localhost>
Description: capability probe
EOL

DEB_FLAGS=()
if dpkg-deb --build --root-owner-group "$PROBE_DIR" "$PROBE_ROOT/probe.deb" >/dev/null 2>&1; then
  DEB_FLAGS+=(--root-owner-group)
else
  echo "WARNING: this dpkg does not support --root-owner-group (needs dpkg >= 1.19)." >&2
  echo "         The package would install files owned by uid $(id -u)." >&2
fi

probe_compression() {
  dpkg-deb "${DEB_FLAGS[@]+"${DEB_FLAGS[@]}"}" -Z "$1" -z "$2" \
       --build "$PROBE_DIR" "$PROBE_ROOT/probe-$1.deb" >/dev/null 2>&1
}

COMPRESS_ARGS=()
COMPRESS_LABEL="dpkg default"
if probe_compression xz 1; then
  COMPRESS_ARGS=(-Z xz -z 1)
  COMPRESS_LABEL="xz -1"
elif probe_compression gzip 1; then
  COMPRESS_ARGS=(-Z gzip -z 1)
  COMPRESS_LABEL="gzip -1"
fi
echo "Compression: ${COMPRESS_LABEL}"

if [ "${DEB_COMPRESSION:-auto}" != "auto" ]; then
  DEB_COMPRESSION_LEVEL="${DEB_COMPRESSION_LEVEL:-3}"
  if probe_compression "$DEB_COMPRESSION" "$DEB_COMPRESSION_LEVEL"; then
    COMPRESS_ARGS=(-Z "$DEB_COMPRESSION" -z "$DEB_COMPRESSION_LEVEL")
    COMPRESS_LABEL="$DEB_COMPRESSION -$DEB_COMPRESSION_LEVEL"
  else
    echo "WARNING: -Z $DEB_COMPRESSION is not available here; keeping $COMPRESS_LABEL." >&2
  fi
fi

echo "Compressing $(du -sm "$BUILD_TREE" | cut -f1) MB with ${COMPRESS_LABEL} (packaging is the slow step)…"

PACKAGE_START=$(date +%s)
if ! dpkg-deb "${DEB_FLAGS[@]+"${DEB_FLAGS[@]}"}" "${COMPRESS_ARGS[@]+"${COMPRESS_ARGS[@]}"}" \
     --build "$BUILD_TREE" "$DEB_TMP"; then
  rm -f "$DEB_TMP"
  echo "ERROR: dpkg-deb failed to build the package." >&2
  echo "If it complained about directory permissions, this repository lives on a" >&2
  echo "filesystem that cannot store them (WSL /mnt, NTFS, FAT). Copy the project" >&2
  echo "to a Linux filesystem such as ~/$(basename "$PWD") and build again." >&2
  exit 1
fi
[ -s "$DEB_TMP" ] || { rm -f "$DEB_TMP"; echo "ERROR: dpkg-deb did not produce a non-empty archive at $DEB_TMP." >&2; exit 1; }
mv -f "$DEB_TMP" "$DEB_OUT"
echo "Packaging took $(( $(date +%s) - PACKAGE_START ))s"
"$PYTHON" build_hooks/verify_payload.py --artifact "$DEB_OUT" --report "$BUILD_DIR/artifact-report.json"
echo "Built $DEB_OUT"
echo "Install with: sudo dpkg -i $DEB_OUT"
