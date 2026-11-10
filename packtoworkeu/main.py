"""PacktoWorkEU — verified visa-sponsorship job discovery.

Entry point for the desktop GUI. Bootstraps localization, applies database
schema migrations, then launches the PySide6 main window and its event loop.

Runtime data lives in the per-user application data directory
(%APPDATA%\\PacktoWorkEU on Windows, ~/.packtoworkeu elsewhere).  Override
with the ``PACKTOWORKEU_DATA_DIR`` or ``PACKTOWORKEU_DB_PATH`` environment
variables if needed.
"""

import logging
import os
import sys
from pathlib import Path

# Ensure the project root is importable when this file is executed directly
# (e.g. ``python3 packtoworkeu/main.py``), since Python only adds the script's
# own directory to sys.path in that case — not the parent that contains the
# ``packtoworkeu`` package.
_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from packtoworkeu.services.browser_policy import launch_chromium, managed_playwright  # noqa: E402


logger = logging.getLogger(__name__)

SELF_CHECK_FLAG = "--self-check"
SELF_CHECK_BROWSER_FLAG = "--self-check-browser"


def _self_check_write(msg: str) -> None:
    """Print a self-check line, safely, even in a console-less windowed build."""
    log_target = os.environ.get("PACKTOWORKEU_SELF_CHECK_LOG", "").strip()
    if log_target:
        try:
            log_path = Path(log_target)
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with log_path.open("a", encoding="utf-8") as handle:
                handle.write(msg + "\n")
        except Exception:
            pass
    for stream in (
        getattr(sys, "stdout", None),
        getattr(sys, "__stdout__", None),
        getattr(sys, "stderr", None),
        getattr(sys, "__stderr__", None),
    ):
        if stream is None:
            continue
        try:
            print(msg, file=stream, flush=True)
            return
        except Exception:
            continue


def self_check(try_browser: bool) -> int:
    """Dependency/optional browser smoke test for packaged builds (used by build_deb.sh/build_rpm.sh/build_exe.ps1).

    Verifies that Playwright imports in the *frozen* app and, when
    ``try_browser`` is set, that the selected full Chromium launches in both modes — all
    without starting Qt. Returns a process exit code so the build scripts can
    abort instead of shipping a bundle that fails every DOM scan target with
    "Playwright is required for DOM fallback".
    """
    import packtoworkeu.paths  # noqa: F401

    _self_check_write(
        f"PacktoWorkEU self-check (frozen={getattr(sys, 'frozen', False)})"
    )
    _self_check_write(f"python={sys.version.split()[0]} executable={sys.executable}")

    browsers = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "")
    _self_check_write(f"PLAYWRIGHT_BROWSERS_PATH={browsers or '<unset>'}")
    if browsers:
        _self_check_write(f"browsers_dir_exists={Path(browsers).is_dir()}")

    failures = 0
    try:
        import PySide6.QtWidgets  # noqa: F401
        _self_check_write("Qt imports: OK")
    except Exception as exc:
        _self_check_write(f"Qt imports: FAILED ({type(exc).__name__}: {exc})")
        failures += 1
    sync_playwright = None
    try:
        from playwright.sync_api import sync_playwright as _sp

        from playwright._impl._driver import compute_driver_executable
        node, cli = compute_driver_executable()
        if not Path(node).is_file() or not Path(cli).is_file():
            raise RuntimeError("Playwright driver files missing")
        sync_playwright = _sp
        with _sp() as driver:
            if driver.chromium.name != "chromium":
                raise RuntimeError("Unexpected Playwright driver")
        _self_check_write("playwright imports + driver startup: OK")
    except Exception as exc:
        _self_check_write(f"playwright import: FAILED ({type(exc).__name__}: {exc})")
        failures += 1

    if try_browser and sync_playwright is not None:
        try:
            with managed_playwright() as pw:
                for headless in (True, False):
                    browser = launch_chromium(pw.chromium,
                        headless=headless, args=["--no-sandbox", "--disable-dev-shm-usage"])
                    browser.close()
                    _self_check_write("Selected Chromium launch: OK (headless=%s)" % headless)
        except Exception as exc:
            _self_check_write(f"chromium launch: FAILED ({type(exc).__name__}: {exc})")
            failures += 1

    _self_check_write("self-check: " + ("FAILED" if failures else "OK"))
    return 1 if failures else 0


def main() -> None:
    """Bootstrap and launch PacktoWorkEU."""
    if '--purge-user-data' in sys.argv:
        import json
        from packtoworkeu.services.data_ownership import purge_user_data
        result = purge_user_data()
        _self_check_write(json.dumps(result))
        raise SystemExit(1 if result['errors'] else 0)
    if '--self-check-gui' in sys.argv:
        from packtoworkeu.scripts.packaging_smoke import gui_smoke
        raise SystemExit(gui_smoke())
    if SELF_CHECK_FLAG in sys.argv or SELF_CHECK_BROWSER_FLAG in sys.argv:
        raise SystemExit(self_check(SELF_CHECK_BROWSER_FLAG in sys.argv))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    logger.info("PacktoWorkEU starting up")

    # Load the persisted language preference before building the UI so every
    # widget labels itself in the correct locale from the first frame.
    from packtoworkeu.i18n import load_saved_locale
    from packtoworkeu.ui.style import build_light_palette, build_qss
    load_saved_locale()

    from PySide6.QtWidgets import QApplication
    from packtoworkeu.services.data_ownership import register_runtime
    register_runtime()
    app = QApplication(sys.argv or ["packtoworkeu"])
    # Fusion renders the QSS paddings/borders predictably across Windows
    # styles (and avoids the combo/spinbox text-overlap artifacts), while
    # the explicit light palette keeps every label legible even when the
    # OS is in dark mode — Qt >= 6.5 would otherwise inject the system
    # dark palette into this light-only stylesheet.
    app.setStyle("Fusion")
    app.setPalette(build_light_palette())
    app.setStyleSheet(build_qss())
    app.setApplicationName("PacktoWorkEU")

    from packtoworkeu.ui.app import PacktoWorkEUApp  # deferred: needs QApplication

    window = PacktoWorkEUApp()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()