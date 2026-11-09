"""Collect Playwright's complete driver, excluding host hermetic browser caches.

Using --collect-all on the CLI bypasses this filter, so native builders use this
hook instead. A build host may have PLAYWRIGHT_BROWSERS_PATH=0 installations.
"""
from PyInstaller.utils.hooks import collect_all


def without_browser_cache(items):
    return [item for item in items if all('.local-browsers' not in str(value).replace('\\', '/').casefold().split('/') for value in item)]


datas, binaries, hiddenimports = collect_all('playwright')
datas = without_browser_cache(datas)
binaries = without_browser_cache(binaries)
