"""Audit finalized frozen payloads without importing application/test modules."""
from pathlib import Path
import argparse
import hashlib
import json


def inventory(directory: Path) -> dict:
    root = directory.resolve()
    if not root.is_dir():
        raise ValueError('Payload directory missing')
    files = {}
    links = {}
    total = 0
    forbidden = {'chrome','chrome.exe','headless_shell','headless_shell.exe',
                 'chrome-headless-shell','chrome-headless-shell.exe','firefox','firefox.exe',
                 'Qt6WebEngineCore.dll'}
    for path in sorted(root.rglob('*')):
        relative = path.relative_to(root)
        if '.local-browsers' in relative.parts or path.name in forbidden:
            raise ValueError(f'Unexpected browser/WebEngine payload: {relative}')
        if path.is_symlink():
            target = path.resolve(strict=True)
            if not target.is_relative_to(root):
                raise ValueError(f'Payload link escapes the package: {relative}')
            links[str(relative)] = str(target.relative_to(root))
        elif path.is_file():
            files[str(relative)] = hashlib.sha256(path.read_bytes()).hexdigest()
            total += path.stat().st_size
    return dict(files=files,internal_links=links,regular_file_bytes=total,
                regular_file_count=len(files),browser_payload_absent=True)


def artifact_info(path: Path) -> dict:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return dict(path=str(path),bytes=path.stat().st_size,sha256=digest.hexdigest())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory',type=Path,nargs='?')
    parser.add_argument('--artifact',type=Path)
    parser.add_argument('--report',type=Path)
    args = parser.parse_args()
    if bool(args.directory) == bool(args.artifact):
        parser.error('Choose a payload directory OR --artifact file')
    if args.artifact:
        result = artifact_info(args.artifact)
        print(f"Compressed artifact: {result['path']} ({result['bytes'] / 1048576:.2f} MiB, {result['bytes']} bytes)")
        print(f"SHA256: {result['sha256']}")
        if args.report:
            args.report.write_text(json.dumps(result,indent=2),encoding='utf-8')
        return 0
    result = inventory(args.directory)
    if args.report:
        args.report.write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(f"Payload checked: {result['regular_file_count']} regular files, "
          f"{result['regular_file_bytes'] / 1048576:.2f} MiB; no bundled browser/WebEngine")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
