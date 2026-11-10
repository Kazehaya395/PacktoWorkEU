"""Track app-owned custom data, then remove it without deleting shared parents.

The Linux package invokes cleanup as each account, never by trusting a user's
manifest as root. Windows installs per-user and runs cleanup without elevation.
"""
from __future__ import annotations
import json
import os
import shutil
import re
from pathlib import Path
from packtoworkeu.services.browser_policy import state_dir, is_link, unlink_link, remove_private_temp

MANIFEST = 'owned_paths.jsonl'
MARKER = '.packtoworkeu-owned'


def protected(path: Path) -> bool:
    p=path.absolute()
    if not p.is_absolute() or p==Path(p.anchor) or p.resolve()==Path.home().resolve():return True
    text='/'+p.as_posix().casefold().strip('/')+'/'
    # Never treat a personal browser profile or shared Playwright cache as ours.
    return any(part in text for part in ('/google/chrome/','/google-chrome/','/microsoft/edge/','/bravesoftware/',
        '/brave-browser/','/chromium/','/microsoft-edge/','/opera/','/opera software/','/opera stable/','/vivaldi/','/mozilla/','/.mozilla/',
        '/firefox/','/librewolf/','/ms-playwright/'))


def _no_symlinks(path: Path) -> bool:
    return not any(is_link(p) for p in (path,*path.parents))


def _append(entry: dict) -> None:
    base=state_dir()
    if not _no_symlinks(base):raise ValueError('PacktoWorkEU data storage must not be a symlink')
    base.mkdir(parents=True,exist_ok=True)
    if is_link(base/MANIFEST):raise ValueError('Ownership registry must not be a link')
    fd=os.open(base/MANIFEST,os.O_WRONLY|os.O_APPEND|os.O_CREAT|getattr(os,'O_NOFOLLOW',0),0o600)
    try:os.write(fd,(json.dumps(entry)+'\n').encode('utf-8'))
    finally:os.close(fd)


def register_file(path, role='artifact') -> None:
    p=Path(path).absolute()
    if protected(p) or not _no_symlinks(p):raise ValueError('Refusing protected/symlink data location')
    _append(dict(kind='file',path=str(p),role=role))


def register_runtime(data_dir=None,db_path=None) -> None:
    from packtoworkeu import paths
    data=Path(data_dir or paths.USER_DATA_DIR).absolute()
    database=Path(db_path or paths.DB_PATH).absolute()
    if protected(data):raise ValueError('Choose a dedicated PacktoWorkEU data folder, not a home or browser folder')
    if not _no_symlinks(data):raise ValueError('PacktoWorkEU data folder must not be a symlink')
    if data.resolve()!=state_dir().resolve():
        for name in ('locale.json','provider_cache.json','jd_cache.json'):
            register_file(data/name)
        for name in ('company_ATS_seed.csv','company_Career_seed.csv','.seed_revision','.seed_merged_revision'):
            register_file(data/'seeds'/name)
        output=data/'scan_output';output.mkdir(parents=True,exist_ok=True)
        if not _no_symlinks(output):raise ValueError('Scan output must not be a symlink')
        register_file(output/'ats_scraper_errors.log')
        for artifact in output.iterdir():
            if re.fullmatch(r'\d{8}T\d{6}[A-Za-z0-9_]*_(?:ats|career)_jobs(?:_(?:recruiter|quarantine|scan_log|errors|baseline))?\.(?:csv|json)',artifact.name) and artifact.is_file():
                register_file(artifact)
        _append(dict(kind='empty_directory',path=str(output)))
        # Parent containers are removed only if empty; unrelated files survive.
        _append(dict(kind='empty_directory',path=str(data/'seeds')))
        _append(dict(kind='empty_directory',path=str(data)))
    register_file(database,'database')
    # QSettings is deliberately app-scoped, but lives outside USER_DATA_DIR.
    if os.name!='nt':
        settings=Path(os.environ.get('XDG_CONFIG_HOME',str(Path.home()/'.config')))/'PacktoWorkEU'/'PacktoWorkEU.conf'
        register_file(settings,'settings')
        register_file(str(settings)+'.lock','settings')
        _append(dict(kind='empty_directory',path=str(settings.parent)))
    # Track existing app-produced seed backups without claiming shared parents.
    seed_backups=data/'seeds'/'backup'
    if seed_backups.is_dir() and _no_symlinks(seed_backups):
        for folder in seed_backups.iterdir():
            if folder.name.replace('_','').isdigit() and folder.is_dir() and _no_symlinks(folder):
                for name in ('company_ATS_seed.csv','company_Career_seed.csv'):
                    if (folder/name).is_file():register_file(folder/name)
                _append(dict(kind='empty_directory',path=str(folder)))
        _append(dict(kind='empty_directory',path=str(seed_backups)))
    # Known legacy maintenance pairs can be adopted after an upgrade. Do not
    # claim the whole parent or unrelated .json/.sqlite3 files.
    directory=database.parent/'maintenance_backups'
    if directory.is_dir() and _no_symlinks(directory):
        for prefix,backup_prefix,operation in (
            ('alias_cleanup_','pre_alias_cleanup_','explicit trusted-posting alias merge'),
            ('data_review_','pre_data_review_','explicit observed-country/non-job review')):
            for audit in directory.glob(prefix+'*.json'):
                try:
                    if is_link(audit) or audit.stat().st_size>2_000_000:continue
                    document=json.loads(audit.read_text(encoding='utf-8'))
                    backup=directory/(backup_prefix+audit.stem[len(prefix):]+'.sqlite3')
                    if document.get('operation')==operation and backup.is_file() and not is_link(backup):
                        register_artifacts(backup,audit)
                except (OSError,ValueError):pass


def purge_user_data() -> dict:
    """Only this account's registered data and dedicated default folders."""
    base=state_dir();errors=[];removed=[]
    entries=[]
    try:
        manifest=base/MANIFEST
        if manifest.exists() and _no_symlinks(manifest):
            for line in manifest.read_text(encoding='utf-8').splitlines():
                try:
                    entry=json.loads(line)
                    if isinstance(entry,dict):entries.append(entry)
                except ValueError:errors.append('Invalid ownership record; manual review required')
    except OSError as exc:errors.append(str(exc))
    def remove_file(p):
        if is_link(p):unlink_link(p);removed.append(str(p));return
        if not _no_symlinks(p):raise ValueError('Symlink ancestor in tracked location')
        if p.is_file():p.unlink();removed.append(str(p))
    for entry in entries:
        try:
            raw=entry.get('path','');p=Path(raw)
            if not raw or not p.is_absolute() or protected(p):raise ValueError('Unsafe ownership record')
            kind=entry.get('kind')
            if kind=='file':
                remove_file(p)
                if entry.get('role')=='database':
                    for suffix in ('-wal','-shm','-journal'):remove_file(Path(str(p)+suffix))
            elif kind!='empty_directory':raise ValueError('Unknown ownership record')
        except (OSError,ValueError,TypeError)as exc:errors.append(str(exc))
    # Remove registered empty containers, including external maintenance folders.
    containers={e.get('path') for e in entries if e.get('kind')=='empty_directory' and isinstance(e.get('path'),str)}
    for value in sorted(containers,key=lambda v:len(Path(v).parts),reverse=True):
        p=Path(value)
        try:
            if p.is_absolute() and not protected(p) and _no_symlinks(p) and p.is_dir():p.rmdir()
        except OSError:pass  # contains unrelated files: intentionally retained
    # App-scoped Qt settings from legacy/default configurations.
    try:
        if os.name=='nt':
            import winreg
            def delete_key(key):
                try:
                    with winreg.OpenKey(winreg.HKEY_CURRENT_USER,key,0,winreg.KEY_READ|winreg.KEY_WRITE) as handle:
                        children=[]
                        while True:
                            try:children.append(winreg.EnumKey(handle,len(children)))
                            except OSError:break
                    for child in children:delete_key(key+'\\'+child)
                    winreg.DeleteKey(winreg.HKEY_CURRENT_USER,key)
                except FileNotFoundError:pass
            delete_key(r'Software\PacktoWorkEU\PacktoWorkEU')
        else:
            settings=Path.home()/'.config/PacktoWorkEU/PacktoWorkEU.conf'
            for p in (settings,Path(str(settings)+'.lock')):remove_file(p)
            try:settings.parent.rmdir()
            except OSError:pass
    except (OSError,ValueError) as exc:errors.append(str(exc))
    try:remove_private_temp()
    except (OSError,ValueError)as exc:errors.append(str(exc))
    # Leave failed records available for retry rather than silently losing them.
    if errors:
        try:
            if _no_symlinks(base):
                base.mkdir(parents=True,exist_ok=True)
                (base/'cleanup-errors.json').write_text(json.dumps(dict(removed=removed,errors=errors),indent=2),encoding='utf-8')
        except OSError:pass
        return dict(removed=removed,errors=errors)
    defaults=[base,Path.home()/'.packtoworkeu']
    if os.name=='nt' and os.environ.get('LOCALAPPDATA'):defaults.append(Path(os.environ['LOCALAPPDATA'])/'PacktoWorkEU')
    for p in dict.fromkeys(defaults):
        try:
            if protected(p):raise ValueError('Unsafe default data folder')
            if is_link(p):unlink_link(p);removed.append(str(p))
            elif p.exists():
                if not _no_symlinks(p):raise ValueError('Symlink ancestor in default data folder')
                shutil.rmtree(p);removed.append(str(p))
        except (OSError,ValueError,TypeError)as exc:errors.append(str(exc))
    return dict(removed=removed,errors=errors)


def register_artifacts(*files) -> None:
    for file in files:register_file(file)
    if files:_append(dict(kind='empty_directory',path=str(Path(files[0]).absolute().parent)))


def register_empty_directory(path) -> None:
    p=Path(path).absolute()
    if protected(p) or not _no_symlinks(p):raise ValueError('Unsafe container')
    _append(dict(kind='empty_directory',path=str(p)))


def register_scan_outputs(*bases) -> None:
    for base in bases:
        p=Path(base)
        register_artifacts(p,*(p.with_name(p.stem+suffix) for suffix in
            ('_recruiter.csv','_quarantine.csv','_scan_log.csv','_errors.csv','_baseline.json')))
