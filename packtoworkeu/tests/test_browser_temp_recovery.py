"""Standalone filesystem regressions; no browser, GUI or user data needed."""
import hashlib
import os
import shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import pytest
from packtoworkeu.services import browser_policy as bp


@pytest.fixture
def isolated(monkeypatch,tmp_path):
    if os.name=='nt':pytest.skip('Unix temp ownership regression')
    state=tmp_path/'app-state'
    monkeypatch.setattr(bp,'state_dir',lambda:state)
    monkeypatch.setattr(bp,'_private_temp_paths',{})
    identity=str(state.absolute())
    digest=hashlib.sha256(identity.encode()).hexdigest()[:10]
    base=Path('/tmp').resolve()/f'packtoworkeu-{os.getuid()}-{digest}'
    yield base,identity
    for path in [base,*bp._private_temp_paths.values()]:
        if path.is_symlink():path.unlink()
        elif path.exists():shutil.rmtree(path)


def test_missing_marker_recovers_without_adopting_or_deleting(isolated):
    base,identity=isolated;base.mkdir(mode=0o700)
    old=base/'existing-profile';old.write_text('leave this untouched')
    actual=bp.private_temp_dir()
    assert actual!=base
    assert actual.stat().st_uid==os.getuid()
    assert actual.stat().st_mode & 0o077==0
    assert (actual/'.packtoworkeu-owner').read_text()==identity
    assert bp.private_temp_dir()==actual
    assert bp.private_temp_dir(create=False)==actual
    bp.remove_private_temp()
    assert not actual.exists() and old.read_text()=='leave this untouched'


def test_cleanup_never_adopts_markerless_directory(isolated):
    base,_=isolated;base.mkdir(mode=0o700)
    assert bp.private_temp_dir(create=False) is None
    bp.remove_private_temp()
    assert base.is_dir()


def test_concurrent_setup_is_consistent(isolated):
    with ThreadPoolExecutor(12) as pool:
        paths=list(pool.map(lambda _:bp.private_temp_dir(),range(50)))
    assert len(set(paths))==1


@pytest.mark.parametrize('kind',['marker-link','directory-link','public','marker-directory'])
def test_unsafe_or_conflicting_storage_is_still_rejected(isolated,tmp_path,kind):
    base,identity=isolated
    if kind=='directory-link':
        target=tmp_path/'external';target.mkdir();base.symlink_to(target,target_is_directory=True)
    else:
        base.mkdir(mode=0o700);marker=base/'.packtoworkeu-owner'
        if kind=='marker-link':
            target=tmp_path/'external';target.write_text(identity);marker.symlink_to(target)
        elif kind=='public':base.chmod(0o755)
        else:marker.mkdir()
    with pytest.raises(ValueError):bp.private_temp_dir()


def test_stale_identity_recovers_without_changing_original_marker(isolated):
    base,_=isolated;base.mkdir(mode=0o700)
    marker=base/'.packtoworkeu-owner';marker.write_text('old or incomplete identity')
    actual=bp.private_temp_dir()
    assert actual!=base and marker.read_text()=='old or incomplete identity'
