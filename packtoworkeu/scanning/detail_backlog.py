"""Previous per-posting results as scheduling hints, never as proof of a live vacancy."""
import csv
import re
from pathlib import Path
from packtoworkeu.application.scan_scope import target_key


def load_pending(directory, parent, targets):
    if not re.fullmatch(r'\d{8}T\d{6}(?:_[a-f0-9]{8})?',str(parent)):return {}
    allowed={target_key(t) for t in targets};out={}
    file=Path(directory)/f'{parent}_career_jobs.csv'
    try:
        with file.open(encoding='utf-8-sig',newline='') as f:
            for r in csv.DictReader(f):
                key=((r.get('Seed Name') or '').casefold(),(r.get('Target Country') or 'Global').casefold(),r.get('Seed URL'))
                if key in allowed and r.get('Run ID')==parent and r.get('JD Read Status')=='unattempted':
                    out.setdefault(key,set()).add(r['Job URL'])
    except (OSError,csv.Error,UnicodeError,KeyError):return {}
    return out


def cursor_io(output, run, parent, seed):
    import hashlib,json,os
    from urllib.parse import urlsplit
    from packtoworkeu.scanning.career.large_boards import TABLE_HOSTS
    if urlsplit(seed).netloc not in TABLE_HOSTS:return {}
    def path(rid):
        if not re.fullmatch(r'\d{8}T\d{6}(?:_[0-9a-f]{8})?',str(rid)):return None
        return Path(output).parent/(rid+'_'+hashlib.sha256(seed.encode()).hexdigest()[:20]+'_listing_cursor.json')
    dest=path(run);src=path(parent);previous=None
    if src:
        try:
            if not src.is_symlink() and src.stat().st_size<16000:
                previous=json.loads(src.read_text(encoding='utf-8'))
        except (OSError,ValueError):pass
    def save(data):
        if not dest:return
        try:
            if data is None:dest.unlink(missing_ok=True);return
            # May be called from concurrent target workers. Atomic publication.
            import tempfile
            with tempfile.NamedTemporaryFile(dir=dest.parent,delete=False,mode='w',encoding='utf-8') as tmp:
                json.dump(data,tmp)
            os.replace(tmp.name,dest)
        except OSError:pass  # absence of an optional hint never proves completion
    return dict(cursor=previous if isinstance(previous,dict) else None,save=save)
