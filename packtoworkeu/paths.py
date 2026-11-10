from __future__ import annotations

import os
import sys
from pathlib import Path


APP_DIR_ENV = "PACKTOWORKEU_DATA_DIR"
DB_PATH_ENV = "PACKTOWORKEU_DB_PATH"


# Browser selection is explicit and per-account; do not modify global
# PLAYWRIGHT_BROWSERS_PATH or redirect other applications to an app bundle.


def _get_windows_appdata_dir(app_name: str) -> Path:
    """Return the *per-user* application data directory on Windows.

    Uses %APPDATA% (e.g. C:\\Users\\<user>\\AppData\\Roaming) which is the
    standard location for per-user application data on Windows.  Falls back
    to the home directory if the environment variable is missing.
    """
    env_path = os.environ.get("APPDATA")
    if env_path:
        return Path(env_path) / app_name
    return Path.home() / f".{app_name.lower()}"


def get_user_data_dir() -> Path:
    """Return the single per-user directory for all mutable app data."""
    override = os.environ.get(APP_DIR_ENV, "").strip()
    if override:
        return Path(override).expanduser()
    # On Windows, use the APPDATA standard directory
    if os.name == "nt":
        return _get_windows_appdata_dir("PacktoWorkEU")
    return Path.home() / ".packtoworkeu"


USER_DATA_DIR = get_user_data_dir()

# MERGE FIX (v0.2.0): career_scanner resolves provider_cache.json via
# ``from packtoworkeu.paths import DATA_DIR`` — a name the REG-1-era
# reconstruction invented and the wave-era scanner kept.  The original
# module only ever exported USER_DATA_DIR; alias it so both trees agree.
DATA_DIR = USER_DATA_DIR
DB_PATH = Path(os.environ.get(DB_PATH_ENV, "")).expanduser() if os.environ.get(DB_PATH_ENV) else USER_DATA_DIR / "packtoworkeu.db"

# Per-user, user-editable copy of the seed CSVs.  Bundled seeds in
# packtoworkeu/data are the defaults; the app copies them here on first run
# and all user edits (Data Management tab) target these mutable copies so a
# packaged build never writes into its own application bundle.
SEEDS_DIR = USER_DATA_DIR / "seeds"

# Raw scan-evidence artifacts (the algorithm scripts' CSV outputs and
# per-run scan logs).  Kept outside the DB so users can inspect them.
SCAN_OUTPUT_DIR = USER_DATA_DIR / "scan_output"


def ensure_user_data_dir() -> Path:
    USER_DATA_DIR.mkdir(parents=True, exist_ok=True)
    return USER_DATA_DIR


def ensure_scan_output_dir() -> Path:
    SCAN_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    return SCAN_OUTPUT_DIR