#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

APP_NAME="PacktoWorkEU"
PKG_NAME="packtoworkeu"
RPM_RELEASE="1"

BUILD_DIR=".build/rpm"
WORK_DIR=".build/rpm-work"
FROZEN_DIR=".build/rpm-frozen"
VENV_DIR=".build/rpm-venv"
DIST_DIR="dist"
PKG_ROOT="$BUILD_DIR/pkgroot"
APP_DIR="$PKG_ROOT/opt/$PKG_NAME"
TOPDIR="$BUILD_DIR/rpmbuild"

need() {
  command -v "$1" >/dev/null 2>&1 || { echo "Missing required command: $1" >&2; exit 1; }
}

# rpmbuild only writes a local .rpm file, so building must never run as root.
# Running with sudo would create root-owned artifacts under .build/ and dist/
# and could trigger PEP 668 "externally-managed-environment" protection.
if [ "$(id -u)" -eq 0 ]; then
  cat >&2 <<'EOF'
ERROR: do not run build_rpm.sh with sudo.
This script only creates dist/packtoworkeu-<version>-1.<arch>.rpm and does not
need root. Run it as your normal user:

  ./build_rpm.sh
EOF
  exit 1
fi

if ! command -v rpmbuild >/dev/null 2>&1; then
  cat >&2 <<'EOF'
ERROR: rpmbuild not found. Install the RPM build tools first:

  Fedora / RHEL / CentOS / Alma / Rocky:  sudo dnf install rpm-build
  openSUSE:                               sudo zypper install rpm-build
  Debian / Ubuntu (cross-packaging):      sudo apt install rpm
EOF
  exit 1
fi

need python3

# Debian/Ubuntu and some minimal RPM distros ship Python without venv/ensurepip.
# Fail with an actionable message instead of touching the system Python.
if ! python3 -c "import venv" >/dev/null 2>&1; then
  cat >&2 <<'EOF'
ERROR: Python's venv module is missing. Install it first, then retry:

  Debian / Ubuntu:  sudo apt install python3-venv
  Fedora / RHEL:    sudo dnf install python3
  openSUSE:         sudo zypper install python3-venv
EOF
  exit 1
fi

# All build dependencies live in this disposable, git-ignored environment.
# Separate from .build/deb-venv so building .deb and .rpm side by side never
# races on the same virtualenv.
if [ -e "$VENV_DIR" ] && [ ! -w "$VENV_DIR" ]; then
  echo "ERROR: $VENV_DIR is not writable by the current user." >&2
  echo "A previous sudo build may have created it. Remove it once, then retry:" >&2
  echo "  sudo rm -rf $VENV_DIR && ./build_rpm.sh" >&2
  exit 1
fi

# Test that pip actually runs inside the venv instead of trusting bin/python's
# mere existence (an interrupted previous run leaves a broken venv behind).
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
    echo "  sudo rm -rf $VENV_DIR && ./build_rpm.sh" >&2
    exit 1
  fi
fi

if ! venv_has_pip "$venv_python"; then
  echo "Bootstrapping pip inside $VENV_DIR..."
  "$venv_python" -m ensurepip --upgrade >/dev/null 2>&1 || true
fi

if ! venv_has_pip "$venv_python"; then
  cat >&2 <<EOF
ERROR: could not provide pip inside $VENV_DIR.
The venv module is available but ensurepip/pip is missing. Install the full
venv package and try again:

  Debian / Ubuntu:  sudo apt install python3-venv
  Fedora / RHEL:    sudo dnf install python3-pip

If it still fails, remove the environment and rebuild it:

  rm -rf $VENV_DIR && ./build_rpm.sh
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
# RPM Version tags forbid '-'; translate pre-release dashes (e.g. 0.2.0-rc1) to '~'.
print(m.group(1).replace('-', '~'))
PY
)"

echo "Installing build dependencies into $VENV_DIR..."
PIP_DISABLE_PIP_VERSION_CHECK=1 "$PYTHON" -m pip install -r requirements.txt

# Fail fast when a dependency installed but is not actually usable (e.g. a
# broken greenlet wheel causes `playwright.sync_api` to fail at runtime with
# an opaque "Playwright is required for DOM fallback" error).
if ! "$PYTHON" -c "from playwright.sync_api import sync_playwright" >/dev/null 2>&1; then
  echo "ERROR: playwright is not usable inside $VENV_DIR (broken/missing dependency)." >&2
  echo "Recreate the build environment and retry:" >&2
  echo "  rm -rf $VENV_DIR && ./build_rpm.sh" >&2
  exit 1
fi

# Only wipe our own staging area: dist/ also holds .exe and .deb artifacts so
# all three formats can coexist in the same checkout.
rm -rf "$BUILD_DIR" "$WORK_DIR" "$FROZEN_DIR"
mkdir -p "$APP_DIR" "$DIST_DIR"

echo "Building $APP_NAME from PacktoWorkEU.spec (safe mode — no binary stripping)..."
"$PYTHON" -m PyInstaller \
  --clean \
  --noconfirm \
  --workpath "$WORK_DIR" \
  --distpath ".build/rpm-frozen" \
  PacktoWorkEU.spec

mkdir -p "$APP_DIR"
cp -a ".build/rpm-frozen/$APP_NAME/." "$APP_DIR/"

mkdir -p "$PKG_ROOT/usr/bin"
cat > "$PKG_ROOT/usr/bin/packtoworkeu" <<'EOL'
#!/bin/sh
# PacktoWorkEU launcher
# The binary lives at /opt/packtoworkeu/PacktoWorkEU after installing the package.

APP_BIN="/opt/packtoworkeu/PacktoWorkEU"

if [ ! -f "$APP_BIN" ]; then
  echo "PacktoWorkEU: ERROR — $APP_BIN not found." >&2
  echo "  The .rpm package may not have installed correctly." >&2
  echo "  Try reinstalling:  sudo dnf reinstall ./packtoworkeu-*.rpm" >&2
  exit 1
fi
if [ ! -x "$APP_BIN" ]; then
  echo "PacktoWorkEU: ERROR — $APP_BIN is not executable." >&2
  echo "  Try:  sudo chmod +x $APP_BIN" >&2
  exit 1
fi

exec "$APP_BIN" "$@"
EOL
chmod 755 "$PKG_ROOT/usr/bin/packtoworkeu"

mkdir -p "$PKG_ROOT/usr/share/applications"
cat > "$PKG_ROOT/usr/share/applications/packtoworkeu.desktop" <<'EOL'
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

# Install hicolor icon theme entries (16px .. 512px) so Icon=packtoworkeu
# resolves on KDE Plasma / GNOME / XFCE app launchers.
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
  dst="$PKG_ROOT/usr/share/icons/hicolor/${size}x${size}/apps"
  mkdir -p "$dst"
  cp "$src" "$dst/packtoworkeu.png"
done

# ── Filesystem permissions ──────────────────────────────────────────────────
# WSL drvfs mounts (/mnt/...), NTFS and FAT report every file as 0777 and ignore
# chmod. Without fixing modes first, %defattr(-,root,root) would record 0777 on
# every packaged file and directory. Normalise in place when chmod works;
# otherwise stage pkgroot and the rpmbuild tree on a Linux filesystem.
STAGE_DIR=""
PROBE_ROOT=""
cleanup() {
  if [ -n "$PROBE_ROOT" ]; then rm -rf "$PROBE_ROOT"; fi
  if [ -n "$STAGE_DIR" ]; then rm -rf "$STAGE_DIR"; fi
  return 0
}
trap cleanup EXIT

if chmod 755 "$PKG_ROOT/usr/bin/packtoworkeu" 2>/dev/null \
   && [ "$(stat -c '%a' "$PKG_ROOT/usr/bin/packtoworkeu" 2>/dev/null || echo 777)" = "755" ]; then
  echo "Normalising package permissions in place…"
  find "$PKG_ROOT" -type d -exec chmod 755 {} + 2>/dev/null || true
  find "$PKG_ROOT" -type f -exec chmod go-w {} + 2>/dev/null || true
  chmod 755 "$PKG_ROOT/usr/bin/packtoworkeu" 2>/dev/null || true
  chmod 755 "$PKG_ROOT/opt/$PKG_NAME/$APP_NAME" 2>/dev/null || true
else
  echo "This filesystem does not support Unix permissions (WSL /mnt, NTFS, FAT)." >&2
  echo "Staging the package tree on a Linux filesystem instead…" >&2
  STAGE_DIR="$(mktemp -d)"
  mkdir -p "$STAGE_DIR/pkgroot"
  cp -a "$PKG_ROOT/." "$STAGE_DIR/pkgroot/"
  PKG_ROOT="$STAGE_DIR/pkgroot"
  TOPDIR="$STAGE_DIR/rpmbuild"
  chmod -R u=rwX,go=rX "$PKG_ROOT"
  chmod 755 "$PKG_ROOT/usr/bin/packtoworkeu" 2>/dev/null || true
  chmod 755 "$PKG_ROOT/opt/$PKG_NAME/$APP_NAME" 2>/dev/null || true
fi

APP_DIR="$PKG_ROOT/opt/$PKG_NAME"
if [ ! -x "$APP_DIR/$APP_NAME" ]; then
  echo "ERROR: $APP_NAME binary not found or not executable in $APP_DIR." >&2
  ls -la "$APP_DIR/" >&2 || true
  exit 1
fi

# Lean package: Playwright driver is collected, but no browser binary/cache.
# Validate the finalized staged payload (after permission staging, before rpmbuild).
echo "Smoke-testing finalized staged payload (dependencies, 8-tab GUI, payload audit)..."
"$APP_DIR/$APP_NAME" --self-check || { echo "Packaged dependency self-check failed" >&2; exit 1; }
"$APP_DIR/$APP_NAME" --self-check-gui || { echo "Packaged GUI self-check failed" >&2; exit 1; }
if [ "${PACKTOWORKEU_VALIDATE_BROWSER:-0}" = "1" ]; then
  "$APP_DIR/$APP_NAME" --self-check-browser || exit 1
fi
"$PYTHON" build_hooks/verify_payload.py "$APP_DIR"

mkdir -p "$TOPDIR"/{BUILD,BUILDROOT,RPMS,SOURCES,SPECS,SRPMS}
PKG_ROOT_ABS="$(cd "$PKG_ROOT" && pwd)"
TOPDIR_ABS="$(cd "$TOPDIR" && pwd)"

RPM_ARCH="$(rpmbuild --eval '%{_arch}' 2>/dev/null || uname -m)"

# ── Compression probe ───────────────────────────────────────────────────────
# PyInstaller bundles are large; RPM's default xz level 6 can look hung for
# minutes while brp-strip / payload compression run. Disable all post-install
# binary mangling (stripping breaks PyInstaller's embedded CPython/Qt ELF data
# and debuginfo extraction fails on prebuilt wheels) and pick fast compression
# by building a tiny 1-file probe RPM with the candidate _binary_payload macro.
PROBE_ROOT="$(mktemp -d)"
mkdir -p "$PROBE_ROOT"/{BUILD,BUILDROOT,RPMS,SOURCES,SPECS,SRPMS,src}
echo "probe" > "$PROBE_ROOT/src/probe.txt"
cat > "$PROBE_ROOT/SPECS/probe.spec" <<'PROBESPEC'
%global debug_package %{nil}
%global __os_install_post %{nil}
Name: packtoworkeu-probe
Version: 0
Release: 1
Summary: capability probe
License: MIT
%description
capability probe
%install
mkdir -p "%{buildroot}/opt/packtoworkeu-probe"
cp "%{_ProbeSrc}/probe.txt" "%{buildroot}/opt/packtoworkeu-probe/probe.txt"
%files
/opt/packtoworkeu-probe/probe.txt
PROBESPEC

probe_payload() {
  rm -rf "$PROBE_ROOT/RPMS"/* "$PROBE_ROOT/BUILDROOT"/*
  rpmbuild -bb \
    --define "_topdir $PROBE_ROOT" \
    --define "_ProbeSrc $PROBE_ROOT/src" \
    --define "_binary_payload $1" \
    "$PROBE_ROOT/SPECS/probe.spec" >/dev/null 2>&1
}

# Build a trivial probe package with no compression override first: when even
# that fails, rpmbuild itself is broken or missing sub-packages (for example
# rpm-build without redhat-rpm-config on minimal containers) and we want to
# show rpmbuild's real stderr instead of silently falling back and failing 10
# minutes later on the 300 MB payload.
if ! rpmbuild -bb \
      --define "_topdir $PROBE_ROOT" \
      --define "_ProbeSrc $PROBE_ROOT/src" \
      "$PROBE_ROOT/SPECS/probe.spec" >/dev/null 2>"$PROBE_ROOT/probe.err"; then
  echo "ERROR: rpmbuild cannot build a trivial probe package on this host:" >&2
  cat "$PROBE_ROOT/probe.err" >&2 || true
  exit 1
fi

PAYLOAD_MACRO=""
COMPRESS_LABEL="rpmbuild default"
if [ "${RPM_COMPRESSION:-auto}" != "auto" ]; then
  if probe_payload "$RPM_COMPRESSION"; then
    PAYLOAD_MACRO="$RPM_COMPRESSION"
    COMPRESS_LABEL="$RPM_COMPRESSION"
  else
    echo "WARNING: RPM_COMPRESSION='$RPM_COMPRESSION' is not supported by this rpmbuild; probing defaults." >&2
  fi
fi
if [ -z "$PAYLOAD_MACRO" ]; then
  for candidate in "w3.zstdio" "w1.xzdio" "w2.gzdio"; do
    if probe_payload "$candidate"; then
      PAYLOAD_MACRO="$candidate"
      COMPRESS_LABEL="$candidate"
      break
    fi
  done
fi
echo "Compression: ${COMPRESS_LABEL}"

# ── Spec file ───────────────────────────────────────────────────────────────
# Crucial RPM safety flags for PyInstaller + Qt6 + Playwright bundles:
#   - debug_package / __os_install_post / __strip disabled: brp-strip corrupts
#     PyInstaller's appended CArchive and bundled Qt/Node shared objects.
#   - AutoReqProv: no — otherwise rpm's ELF scanner emits bogus `Requires:` for
#     private bundled Qt6/ICU/FFmpeg .so versions that no distro repo provides,
#     making `dnf install` refuse the package with unsolvable dependencies.
#   - Bundled Playwright driver / Node binary stays untouched without any
#     browser cache inside /opt/packtoworkeu.
SPEC_FILE="$TOPDIR_ABS/SPECS/$PKG_NAME.spec"
cat > "$SPEC_FILE" <<SPECEOF
%global debug_package %{nil}
%global __os_install_post %{nil}
%global __strip /bin/true
%global _build_id_links none

Name:           $PKG_NAME
Version:        $VERSION
Release:        $RPM_RELEASE
Summary:        Job sponsorship search application
License:        Proprietary
URL:            https://github.com/Kazehaya395/PacktoWorkEU
AutoReqProv:    no
Requires:       util-linux, alsa-lib, atk, at-spi2-atk, at-spi2-core, cairo, cups-libs, dbus-libs, glib2, gtk3, libX11, libXcomposite, libXdamage, libXext, libXfixes, libXrandr, libxcb, libxkbcommon, libxkbcommon-x11, mesa-libgbm, nspr, nss, pango

%description
PacktoWorkEU is a local desktop application for finding visa-sponsoring jobs
from official ATS boards and company career pages.

%prep
# Pre-built PyInstaller tree staged outside rpmbuild.

%build
# Nothing to compile here.

%install
rm -rf "%{buildroot}"
mkdir -p "%{buildroot}"
cp -a "$PKG_ROOT_ABS/." "%{buildroot}/"
SPECEOF

cat >> "$SPEC_FILE" <<'EOL'

%post
if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database /usr/share/applications >/dev/null 2>&1 || true
fi
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
  gtk-update-icon-cache -f -t /usr/share/icons/hicolor >/dev/null 2>&1 || true
fi

# No browser download during installation. Use Browser setup explicitly.
exit 0
EOL

cat >> "$SPEC_FILE" <<'EOL'

%preun
# Require the installed binary to be closed before upgrade/erase; never kill
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
# In RPM, $1 == 0 means complete erase; $1 >= 1 means upgrade (keep user data!).
if [ "${1:-1}" = "0" ]; then
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

cat >> "$SPEC_FILE" <<'EOL'

%postun
if [ "${1:-1}" = "0" ]; then
  if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database /usr/share/applications >/dev/null 2>&1 || true
  fi
  if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -f -t /usr/share/icons/hicolor >/dev/null 2>&1 || true
  fi
fi
exit 0
EOL

cat >> "$SPEC_FILE" <<'EOL'

%files
%defattr(-,root,root,-)
/opt/packtoworkeu
/usr/bin/packtoworkeu
/usr/share/applications/packtoworkeu.desktop
/usr/share/icons/hicolor/*/apps/packtoworkeu.png
EOL

# ── Build RPM ───────────────────────────────────────────────────────────────
RPM_BASENAME="${PKG_NAME}-${VERSION}-${RPM_RELEASE}.${RPM_ARCH}"
RPM_OUT="$DIST_DIR/${RPM_BASENAME}.rpm"
RPM_TMP="$DIST_DIR/.${RPM_BASENAME}.rpm.partial"
rm -f "$RPM_TMP"

RPM_EXTRA_DEFINES=(--define "_topdir $TOPDIR_ABS")
if [ -n "$PAYLOAD_MACRO" ]; then
  RPM_EXTRA_DEFINES+=(--define "_binary_payload $PAYLOAD_MACRO")
fi

echo "Compressing $(du -sm "$PKG_ROOT" | cut -f1) MB into RPM with ${COMPRESS_LABEL} (packaging is the slow step)…"
PACKAGE_START=$(date +%s)
if ! rpmbuild -bb "${RPM_EXTRA_DEFINES[@]}" "$SPEC_FILE"; then
  echo "ERROR: rpmbuild failed." >&2
  exit 1
fi

BUILT_RPM="$(find "$TOPDIR_ABS/RPMS" -type f -name "${PKG_NAME}-${VERSION}-${RPM_RELEASE}.*.rpm" | head -n 1)"
if [ -z "$BUILT_RPM" ] || [ ! -f "$BUILT_RPM" ]; then
  echo "ERROR: rpmbuild reported success, but no .rpm was found under $TOPDIR_ABS/RPMS." >&2
  find "$TOPDIR_ABS/RPMS" -maxdepth 3 >&2 || true
  exit 1
fi

# Resolve final filename from the actual arch rpmbuild emitted (e.g. x86_64/aarch64)
ACTUAL_NAME="$(basename "$BUILT_RPM")"
RPM_OUT="$DIST_DIR/$ACTUAL_NAME"
RPM_TMP="$DIST_DIR/.${ACTUAL_NAME}.partial"
rm -f "$RPM_TMP"
cp -f "$BUILT_RPM" "$RPM_TMP"
[ -s "$RPM_TMP" ] || { rm -f "$RPM_TMP"; echo "ERROR: staged RPM at $RPM_TMP is empty." >&2; exit 1; }
mv -f "$RPM_TMP" "$RPM_OUT"

echo "Packaging took $(( $(date +%s) - PACKAGE_START ))s"
"$PYTHON" build_hooks/verify_payload.py --artifact "$RPM_OUT" --report "$BUILD_DIR/artifact-report.json"
echo "Built $RPM_OUT"
echo "Install with: sudo dnf install ./$RPM_OUT   (or: sudo rpm -Uvh $RPM_OUT)"
