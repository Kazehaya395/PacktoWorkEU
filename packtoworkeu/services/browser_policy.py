"""One explicit browser selection for every Chromium launch. No silent download."""
from __future__ import annotations
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

FAMILIES = ('Chrome', 'Edge', 'Brave', 'Opera', 'Vivaldi', 'Chromium')


def state_dir() -> Path:
    # A stable per-account location, even if the job database/data directory moves.
    return (Path(os.environ['APPDATA']) / 'PacktoWorkEU' if os.name == 'nt' and os.environ.get('APPDATA')
            else Path.home() / '.packtoworkeu')


def is_link(path: Path) -> bool:
    """Include Windows directory junctions/reparse points on Python 3.10+."""
    try:return path.is_symlink() or bool(getattr(path.lstat(),'st_file_attributes',0) & 0x400)
    except OSError:return False


def unlink_link(path: Path) -> None:
    if path.is_symlink() or not path.is_dir():path.unlink()
    else:path.rmdir()  # Windows junction: remove link, never descend into target.


def _safe_storage() -> bool:
    path=state_dir()
    return not any(is_link(p) for p in (path,*path.parents))


_driver_start_lock = threading.Lock()


def private_temp_dir(*, create=True):
    """Short Unix socket paths, owned by this UID/account; never arbitrary paths."""
    if os.name=='nt':
        path=state_dir()/'runtime/tmp'
        if not create and not path.exists():return None
        if any(is_link(p) for p in (path,*path.parents)):raise ValueError('Unsafe browser temp folder')
        if create:path.mkdir(parents=True,exist_ok=True)
        return path
    import hashlib
    uid=os.getuid()
    identity=str(state_dir().absolute())
    digest=hashlib.sha256(identity.encode('utf-8')).hexdigest()[:10]
    path=Path('/tmp').resolve()/f'packtoworkeu-{uid}-{digest}'
    marker=path/'.packtoworkeu-owner'
    created=False
    if not path.exists() and not is_link(path):
        if not create:return None
        try:path.mkdir(mode=0o700);created=True
        except FileExistsError:pass
    if is_link(path) or not path.is_dir():raise ValueError('Unsafe browser temp location')
    stat=path.stat()
    if stat.st_uid!=uid or stat.st_mode & 0o077:raise ValueError('Browser temp folder must be private to this account')
    if created:
        marker.write_text(identity,encoding='utf-8')
    if is_link(marker) or not marker.is_file() or marker.read_text(encoding='utf-8')!=identity:
        raise ValueError('Browser temp folder ownership could not be confirmed')
    return path


def remove_private_temp() -> None:
    path=private_temp_dir(create=False)
    if path is not None:shutil.rmtree(path)


def _runtime_environment() -> dict:
    """Keep browser caches/crash reports/downloads and driver temps app-owned."""
    if not _safe_storage():raise ValueError('Unsafe PacktoWorkEU browser storage')
    root=state_dir()/'runtime'
    folders={name:root/name for name in ('home','config','cache','local','roaming','tmp','crashes','downloads')}
    for directory in folders.values():
        if any(is_link(p) for p in (directory,*directory.parents)):raise ValueError('Browser runtime folder must not be a link')
        directory.mkdir(parents=True,exist_ok=True)
    temp=private_temp_dir()
    env=dict(os.environ,HOME=str(folders['home']),USERPROFILE=str(folders['home']),
             APPDATA=str(folders['roaming']),LOCALAPPDATA=str(folders['local']),
             XDG_CONFIG_HOME=str(folders['config']),XDG_CACHE_HOME=str(folders['cache']),
             TMPDIR=str(temp),TEMP=str(temp),TMP=str(temp))
    authority=Path.home()/'.Xauthority'
    if 'XAUTHORITY' not in env and authority.is_file():env['XAUTHORITY']=str(authority)
    return env


class _ManagedPlaywright:
    def __enter__(self):
        from playwright.sync_api import sync_playwright
        self.context=sync_playwright()
        env=_runtime_environment()
        # Node creates ephemeral profiles before spawning Chromium. Capture app
        # TMP values at driver startup, then immediately restore Python's env.
        # Only these three temp variables are changed, under a short shared lock.
        with _driver_start_lock:
            previous={key:os.environ.get(key) for key in ('TMPDIR','TEMP','TMP')}
            try:
                for key in previous:os.environ[key]=env[key]
                return self.context.__enter__()
            finally:
                for key,value in previous.items():
                    if value is None:os.environ.pop(key,None)
                    else:os.environ[key]=value

    def __exit__(self,*args):return self.context.__exit__(*args)
    def start(self):return self.__enter__()


def managed_playwright():
    """Synchronous Playwright lifecycle with app-owned ephemeral storage."""
    return _ManagedPlaywright()


def config_path() -> Path:
    return state_dir() / 'browser.json'


def fingerprint(path: str) -> dict:
    p = Path(path)
    stat = p.stat()
    return dict(path=str(p.resolve()), size=stat.st_size, mtime_ns=stat.st_mtime_ns)


def discover() -> list[dict]:
    """Known Chromium-family installations; never inspect personal profiles."""
    found = []
    if sys.platform == 'win32':
        relative = {
            'Chrome': ['Google/Chrome/Application/chrome.exe'],
            'Edge': ['Microsoft/Edge/Application/msedge.exe'],
            'Brave': ['BraveSoftware/Brave-Browser/Application/brave.exe'],
            'Opera': ['Programs/Opera/launcher.exe', 'Opera/launcher.exe'],
            'Vivaldi': ['Vivaldi/Application/vivaldi.exe'],
            'Chromium': ['Chromium/Application/chrome.exe'],
        }
        for family, suffixes in relative.items():
            for base in ('PROGRAMFILES', 'PROGRAMFILES(X86)', 'LOCALAPPDATA'):
                if os.environ.get(base):
                    for suffix in suffixes:found.append((family, Path(os.environ[base]) / suffix))
        try:
            import winreg
            for family, exe in [('Chrome','chrome.exe'),('Edge','msedge.exe'),('Brave','brave.exe'),('Opera','opera.exe'),('Vivaldi','vivaldi.exe')]:
                for hive in (winreg.HKEY_CURRENT_USER,winreg.HKEY_LOCAL_MACHINE):
                    for view in (winreg.KEY_WOW64_64KEY,winreg.KEY_WOW64_32KEY):
                        try:
                            with winreg.OpenKey(hive, rf'Software\Microsoft\Windows\CurrentVersion\App Paths\{exe}',0,winreg.KEY_READ|view) as key:
                                found.append((family,Path(winreg.QueryValue(key,None).strip('"'))))
                        except OSError:pass
        except ImportError:pass
    elif sys.platform == 'darwin':
        for family, app, binary in [('Chrome','Google Chrome','Google Chrome'),('Edge','Microsoft Edge','Microsoft Edge'),('Brave','Brave Browser','Brave Browser'),('Opera','Opera','Opera'),('Vivaldi','Vivaldi','Vivaldi'),('Chromium','Chromium','Chromium')]:
            for base in (Path('/Applications'),Path.home()/'Applications'):
                found.append((family,base/f'{app}.app/Contents/MacOS/{binary}'))
    else:
        names = {'Chrome':['google-chrome','google-chrome-stable'],'Edge':['microsoft-edge','microsoft-edge-stable'],
                 'Brave':['brave-browser','brave-browser-stable','brave'],'Opera':['opera'],'Vivaldi':['vivaldi','vivaldi-stable'],'Chromium':['chromium','chromium-browser']}
        for family, commands in names.items():
            for command in commands:
                value=shutil.which(command)
                if value:found.append((family,Path(value)))
    result=[];seen=set()
    for family,path in found:
        if path.is_file() and str(path.resolve()) not in seen:
            seen.add(str(path.resolve()));result.append(dict(family=family,path=str(path.absolute()),kind='installed'))
    return result


def saved_candidate() -> dict | None:
    """Offer a previously selected executable for RE-validation, never for use."""
    try:
        if not _safe_storage():return None
        value=json.loads(config_path().read_text(encoding='utf-8'))
        if not isinstance(value,dict) or value.get('family')not in FAMILIES or value.get('kind')not in ('installed','managed'):return None
        path=Path(value.get('path',''))
        if not path.is_absolute() or not path.is_file():return None
        if value['kind']=='managed' and (is_link(state_dir()/'browser') or not path.resolve().is_relative_to((state_dir()/'browser').resolve())):return None
        return dict(family=value['family'],kind=value['kind'],path=str(path))
    except (OSError,ValueError,TypeError):return None


def read_selection() -> dict | None:
    try:
        if not _safe_storage():return None
        value=json.loads(config_path().read_text(encoding='utf-8'))
        if not isinstance(value,dict):return None
        if value.get('family') not in FAMILIES or value.get('kind') not in ('installed','managed'):return None
        if not Path(value.get('path','')).is_absolute():return None
        if value.get('validated') is not True:return None
        if fingerprint(value['path']) != value.get('fingerprint'):return None
        if value['kind']=='managed' and (is_link(state_dir()/'browser') or not Path(value['path']).resolve().is_relative_to((state_dir()/'browser').resolve())):return None
        return value
    except (OSError,ValueError,TypeError,KeyError):return None


def save_selection(candidate: dict) -> None:
    if candidate.get('family') not in FAMILIES or candidate.get('kind') not in ('managed','installed'):
        raise ValueError('Unsupported browser family')
    if candidate.get('validated') is not True or fingerprint(candidate['path']) != candidate.get('fingerprint'):
        raise ValueError('Validate both browser modes before saving')
    if not _safe_storage():raise ValueError('PacktoWorkEU browser storage must not be a symlink')
    if candidate['kind']=='managed' and (is_link(state_dir()/'browser') or not Path(candidate['path']).resolve().is_relative_to((state_dir()/'browser').resolve())):
        raise ValueError('Managed executable must stay in app-private storage')
    path=config_path();path.parent.mkdir(parents=True,exist_ok=True)
    fd,name=tempfile.mkstemp(prefix='browser-',suffix='.json',dir=path.parent)
    try:
        with os.fdopen(fd,'w',encoding='utf-8')as f:json.dump(candidate,f)
        os.replace(name,path)
        if candidate['kind']=='managed':_prune_managed(candidate)
    finally:
        if os.path.exists(name):os.unlink(name)


def launch_chromium(browser_type, **kwargs):
    """Use the same full executable in headless and visible modes, isolated by Playwright."""
    selected=read_selection()
    if selected is None:
        raise RuntimeError('Browser setup required: open Tools > Advanced maintenance and diagnostics > Browser setup. Select and validate a browser. No browser was downloaded.')
    if any(k in kwargs for k in ('executable_path','channel','user_data_dir')):
        raise ValueError('Browser identity is owned by Browser setup')
    if any(k in kwargs for k in ('env','downloads_path')):
        raise ValueError('Browser storage is owned by PacktoWorkEU')
    args=list(kwargs.pop('args',()) or ())
    if any(str(a).lower().startswith(('--user-data-dir','--profile-directory','--remote-debugging-port','--crash-dumps-dir','--disk-cache-dir')) for a in args):
        raise ValueError('Personal profiles and browser attachment are not allowed')
    env=_runtime_environment();runtime=state_dir()/'runtime'
    args.append('--crash-dumps-dir='+str(runtime/'crashes'))
    # Explicit full executable prevents the default separate headless-shell lookup.
    return browser_type.launch(executable_path=selected['path'],channel={'Chrome':'chrome','Edge':'msedge'}.get(selected['family'],'chromium'),
        args=args,env=env,downloads_path=str(runtime/'downloads'),**kwargs)


def validate(candidate: dict) -> dict:
    """User-initiated local probe in a worker: both modes, no website requests."""
    if candidate.get('family') not in FAMILIES or candidate.get('kind') not in ('managed','installed'):
        raise ValueError('Unsupported browser family')
    before=fingerprint(candidate['path'])
    if candidate['kind']=='managed' and (is_link(state_dir()/'browser') or not Path(candidate['path']).resolve().is_relative_to((state_dir()/'browser').resolve())):
        raise ValueError('Managed browser must be inside PacktoWorkEU storage')
    versions=[]
    env=_runtime_environment()
    with managed_playwright() as pw:
        for headless in (True,False):
            browser=None
            try:
                browser=pw.chromium.launch(executable_path=candidate['path'],channel={'Chrome':'chrome','Edge':'msedge'}.get(candidate['family'],'chromium'),headless=headless,timeout=20000,env=env,downloads_path=str(state_dir()/'runtime/downloads'),args=['--enable-automation','--no-sandbox','--disable-dev-shm-usage','--crash-dumps-dir='+str(state_dir()/'runtime/crashes')])
                session=browser.new_browser_cdp_session()
                try:arguments=session.send('Browser.getBrowserCommandLine')['arguments']
                finally:session.detach()
                profiles=[a.split('=',1)[1] for a in arguments if a.startswith('--user-data-dir=')]
                if len(profiles)!=1 or not Path(profiles[0]).name.startswith('playwright_chromiumdev_profile-'):
                    raise RuntimeError('Browser did not confirm an isolated temporary profile')
                page=browser.new_page();page.set_content('<title>PacktoWorkEU browser check</title><p>Only this local test page is opened.</p>')
                if page.title()!='PacktoWorkEU browser check':raise RuntimeError('Browser content check failed')
                versions.append(browser.version)
            finally:
                if browser is not None:browser.close()
    if fingerprint(candidate['path'])!=before:raise RuntimeError('Browser changed during validation; try again')
    return dict(candidate,validated=True,fingerprint=before,versions=versions)


def install_managed(*, consent: bool = False, timeout: int = 600) -> dict:
    if consent is not True:raise PermissionError('Explicit download confirmation required')
    from playwright._impl._driver import compute_driver_executable, get_driver_env
    if not _safe_storage():raise ValueError('PacktoWorkEU browser storage must not be a symlink')
    target=state_dir()/'browser'
    target.mkdir(parents=True,exist_ok=True)
    if is_link(target):raise ValueError('Managed browser directory must not be a symlink')
    node,cli=compute_driver_executable()
    env=dict(get_driver_env(),**_runtime_environment())
    env['PLAYWRIGHT_BROWSERS_PATH']=str(target)
    # Works in frozen packages: never try to run PacktoWorkEU.exe -m playwright.
    result=subprocess.run([str(node),str(cli),'install','chromium','--no-shell'],env=env,
                          capture_output=True,text=True,timeout=timeout,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if result.returncode:raise RuntimeError('Chromium download failed: '+(result.stderr or result.stdout)[-1600:])
    # A fresh driver reads the app-owned path without changing process-global env.
    metadata=Path(cli).parent/'browsers.json'
    data=json.loads(metadata.read_text())
    chromium=next(b for b in data['browsers'] if b['name']=='chromium')
    candidates=list(target.glob('chromium-*/**/chrome.exe')) if os.name=='nt' else list(target.glob('chromium-*/**/chrome'))
    if sys.platform=='darwin':candidates=list(target.glob('chromium-*/**/Chromium.app/Contents/MacOS/Chromium'))
    # Version metadata/revision is checked by the installer; select the matching
    # current revision, never an arbitrary executable from the user's PATH.
    revision=str(chromium.get('revisionOverrides',{}).get(sys.platform,chromium['revision']))
    matching=[p for p in candidates if f'chromium-{revision}' in p.parts]
    if not matching:matching=candidates  # platform-specific revision override
    if len(matching)!=1:raise RuntimeError('Could not identify the installed full Chromium executable')
    return dict(family='Chromium',kind='managed',path=str(matching[0].absolute()))


def _prune_managed(candidate: dict) -> None:
    """After validation/save, retire old PRIVATE browser builds, not shared caches."""
    import logging
    root=state_dir()/'browser'
    if not _safe_storage() or is_link(root):return
    keep=next((p for p in Path(candidate['path']).parents if p.parent==root),None)
    if keep is None:return
    for entry in root.iterdir():
        if entry==keep or not (entry.name.startswith('chromium-') or entry.name.startswith('chromium_headless_shell-')):continue
        try:
            if is_link(entry):unlink_link(entry)
            elif entry.is_dir():shutil.rmtree(entry)
        except OSError as exc:
            logging.getLogger(__name__).warning('Old app-private browser cache retained (close other app instances and retry setup): %s',exc)
