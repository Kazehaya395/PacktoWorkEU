"""Packaging-only regressions; native Windows execution is a separate check."""
from pathlib import Path
import ast
import os
import subprocess
import sys
import pytest

ROOT = Path(__file__).resolve().parents[2]
PS = (ROOT/'build_exe.ps1').read_text()
SPEC = (ROOT/'SponsorScout.spec').read_text()
INNO = (ROOT/'installer.iss').read_text()


def test_windows_build_uses_owned_environment_without_pip_deletion():
    assert "'.build\\windows-venv'" in PS
    assert PS.index('if ($env:VIRTUAL_ENV)') < PS.index("Get-Command py.exe")
    assert '& $Bootstrap -I -m venv $Venv' in PS
    assert '& $Python -I @args' in PS
    assert "-Filter 'pip*'" not in PS
    assert 'pip uninstall' not in PS and '--upgrade' not in PS
    assert '-m pip check' in PS and 'build-dependencies.txt' in PS


def test_every_builder_consumes_the_maintained_spec_and_owns_work_paths():
    for filename in ('build_exe.ps1','build_deb.sh','build_rpm.sh'):
        text = (ROOT/filename).read_text()
        assert 'SponsorScout.spec' in text
        assert '--collect-submodules' not in text and '--name ' not in text
        assert '--workpath' in text and '--distpath' in text
    for kind in ('deb','rpm'):
        text = (ROOT/f'build_{kind}.sh').read_text()
        assert f'".build/{kind}-frozen"' in text
        assert f'cp -a ".build/{kind}-frozen/$APP_NAME/." "$APP_DIR/"' in text
        assert '"$BUILD_DIR/frozen"' not in text  # do not package work files/duplicate payloads


def test_spec_filters_tests_before_collection_not_after_packaging():
    tree = ast.parse(SPEC)
    call = next(node for node in ast.walk(tree) if isinstance(node,ast.Call)
                and isinstance(node.func,ast.Name) and node.func.id=='collect_submodules'
                and node.args and isinstance(node.args[0],ast.Constant) and node.args[0].value=='sponsorscout')
    expression = next(kw.value for kw in call.keywords if kw.arg=='filter')
    predicate = eval(compile(ast.Expression(expression),'<spec-test>','eval'))
    assert predicate('sponsorscout.ui.app') and predicate('sponsorscout.services.browser_policy')
    assert not predicate('sponsorscout.tests') and not predicate('sponsorscout.tests.test_scan')
    assert "collect_submodules('PySide6')" not in SPEC
    assert "'PySide6.QtWidgets'" in SPEC and "'PySide6.QtWebEngineCore'" in SPEC
    assert 'upx=True' not in SPEC and 'strip=True' not in SPEC


def test_final_payloads_are_checked_without_later_plugin_metadata_pruning():
    for filename in ('build_exe.ps1','build_deb.sh','build_rpm.sh'):
        text = (ROOT/filename).read_text()
        assert '--self-check-gui' in text
        assert '*.dist-info' not in text and '-name "locale"' not in text
        assert '-name "*.pyc"' not in text
    assert PS.index("Copy-Item -LiteralPath (Join-Path $Root 'sponsorscout") < PS.index("Test-Payload (Join-Path $InstallerSrcDir")
    assert PS.index("Test-Payload (Join-Path $InstallerSrcDir") < PS.index('& $Iscc')


def test_installer_freshness_and_bundle_only_status_are_explicit():
    assert '[switch]$BundleOnly' in PS
    assert 'if (-not $BundleOnly -and -not $Iscc)' in PS
    assert PS.index('New-Item -ItemType Directory -Path $CompilerOutDir') < PS.index('& $Iscc')
    assert '"/O$CompilerOutDir"' in PS
    assert PS.index('$CompilerCode -ne 0') < PS.index('Move-Item -LiteralPath $FreshInstaller')
    assert 'Test-Path -LiteralPath $FreshInstaller -PathType Leaf' in PS
    assert 'did not produce a nonempty fresh output' in PS
    assert 'No installer was built' in PS
    assert 'MiB unpacked' in PS and 'MiB compressed' in PS
    assert 'SHA256:' in PS
    assert 'and Chromium are still' not in PS


def test_shutdown_does_not_force_kill_by_name():
    assert 'Stop-Process' not in PS and 'taskkill' not in INNO
    assert 'CloseApplications=force' not in INNO
    assert 'InstalledAppRunning()' in INNO and 'ExecutablePath' in INNO
    assert "CompareText(ProcessPath, ExpandConstant('{app}" in INNO
    assert 'PrivilegesRequired=lowest' in INNO and 'IsUserAnAdmin' not in INNO
    assert 'CurUninstallStep <> usUninstall' in INNO and '--purge-user-data' in INNO


@pytest.mark.skipif(os.name != 'posix', reason='Generated Linux scriptlet check')
@pytest.mark.parametrize('filename',['build_deb.sh','build_rpm.sh'])
@pytest.mark.parametrize('target,expected',[
    ('/opt/sponsorscout/SponsorScout',1),
    ('/opt/sponsorscout/SponsorScout (deleted)',1),
    ('/some/other/copy/SponsorScout',0),
])
def test_linux_running_check_is_scoped_and_refuses_without_killing(tmp_path,filename,target,expected):
    text = (ROOT/filename).read_text()
    start = text.index('command -v readlink')
    end = text.index('# Each user',start)
    snippet = text[start:end]
    assert 'kill -s' not in snippet
    binpath = tmp_path/'bin';binpath.mkdir()
    readlink = binpath/'readlink'
    readlink.write_text('#!/bin/sh\nprintf "%s\\n" "$FAKE_EXECUTABLE"\n')
    readlink.chmod(0o755)
    env = dict(os.environ,PATH=str(binpath)+':/usr/bin:/bin',FAKE_EXECUTABLE=target)
    result = subprocess.run(['sh','-c',snippet],env=env,capture_output=True,text=True,timeout=10)
    assert result.returncode==expected,result.stderr


def test_gui_packaging_mode_is_explicit_and_offline():
    main = (ROOT/'sponsorscout/main.py').read_text()
    smoke = (ROOT/'sponsorscout/scripts/packaging_smoke.py').read_text()
    assert "if '--self-check-gui' in sys.argv:" in main
    assert 'TemporaryDirectory' in smoke and 'QSettings.setDefaultFormat(QSettings.IniFormat)' in smoke
    assert 'tabs.count() != 8' in smoke and 'def _check_first_run(self)' in smoke
    assert 'launch_chromium(' not in smoke and 'install_managed(' not in smoke


def test_actual_source_gui_smoke_preserves_user_data(tmp_path):
    pytest.importorskip('PySide6.QtWidgets')
    home = tmp_path/'user';home.mkdir()
    data = home/'actual-data';data.mkdir()
    canary = data/'keep.txt';canary.write_text('must survive')
    env = dict(os.environ,HOME=str(home),USERPROFILE=str(home),APPDATA=str(home/'roaming'),
               SPONSORSCOUT_DATA_DIR=str(data),QT_QPA_PLATFORM='offscreen')
    result = subprocess.run([sys.executable,'-m','sponsorscout.main','--self-check-gui'],cwd=ROOT,
                            env=env,capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stdout+result.stderr
    assert 'Eight-tab Qt GUI startup/close: OK' in result.stdout
    assert canary.read_text()=='must survive'
    assert list(data.iterdir())==[canary]


def _verifier():
    import importlib.util
    spec = importlib.util.spec_from_file_location('payload_test_helper',ROOT/'build_hooks/verify_payload.py')
    module = importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def test_payload_verifier_keeps_metadata_and_hashes_real_files(tmp_path):
    tmp_path = tmp_path/'payload';tmp_path.mkdir()
    meta = tmp_path/'example.dist-info';meta.mkdir()
    (meta/'METADATA').write_text('Version: 1')
    (tmp_path/'node').write_bytes(b'fixture driver')
    info = _verifier().inventory(tmp_path)
    assert info['regular_file_count']==2 and info['browser_payload_absent']
    assert info['regular_file_bytes']==len(b'Version: 1')+len(b'fixture driver')
    for filename in ('build_exe.ps1','build_deb.sh','build_rpm.sh'):
        assert 'verify_payload.py' in (ROOT/filename).read_text()


@pytest.mark.parametrize('filename',['chrome.exe','headless_shell','firefox.exe','.local-browsers/canary'])
def test_payload_verifier_rejects_browser_payloads(tmp_path,filename):
    path = tmp_path/filename;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'fixture')
    with pytest.raises(ValueError,match='Unexpected browser'):
        _verifier().inventory(tmp_path)


@pytest.mark.skipif(os.name != 'posix',reason='POSIX symlink fixture')
def test_payload_verifier_accepts_internal_links_but_rejects_escapes(tmp_path):
    payload = tmp_path/'payload';payload.mkdir()
    (payload/'lib').write_bytes(b'fixture')
    (payload/'alias').symlink_to('lib')
    info = _verifier().inventory(payload)
    assert info['internal_links']=={'alias':'lib'} and info['regular_file_count']==1
    (tmp_path/'unrelated').write_text('keep')
    (payload/'escape').symlink_to('../unrelated')
    with pytest.raises(ValueError,match='escapes'):
        _verifier().inventory(payload)


def test_compressed_artifact_size_uses_file_bytes_not_allocated_blocks(tmp_path):
    import hashlib
    artifact = tmp_path/'fixture.rpm';artifact.write_bytes(b'archive fixture')
    info = _verifier().artifact_info(artifact)
    assert info['bytes']==len(b'archive fixture')
    assert info['sha256']==hashlib.sha256(b'archive fixture').hexdigest()
    for kind in ('deb','rpm'):
        text = (ROOT/f'build_{kind}.sh').read_text()
        assert '--artifact' in text and 'artifact-report.json' in text
        assert f'du -sh "${kind.upper()}_OUT"' not in text


def test_powershell_helpers_with_mocked_process_api():
    import shutil
    shell = os.environ.get('SPONSORSCOUT_PWSH') or shutil.which('pwsh') or shutil.which('powershell')
    if not shell:
        pytest.skip('PowerShell not installed; source contracts above still run')
    result = subprocess.run([shell,'-NoProfile','-NonInteractive','-File',
        str(ROOT/'sponsorscout/tests/packaging_windows_helpers.ps1'),'-RepoRoot',str(ROOT),
        '-PythonForTest',sys.executable],capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stdout+result.stderr
    assert 'PowerShell helper checks OK' in result.stdout


def test_linux_checks_follow_permission_and_fallback_staging():
    for kind in ('deb','rpm'):
        text = (ROOT/f'build_{kind}.sh').read_text()
        assert text.index('# ── Filesystem permissions') < text.index('--self-check-gui')
        var = kind.upper()
        assert f'rm -f "${var}_TMP" "${var}_OUT"' not in text
    deb = (ROOT/'build_deb.sh').read_text()
    assert deb.index('APP_DIR="$BUILD_TREE/opt/$PKG_NAME"') < deb.index('--self-check-gui')
    assert '[ -s "$DEB_TMP" ]' in deb


def test_named_settings_in_smoke_explicitly_use_ini_not_default_format(tmp_path):
    from PySide6.QtCore import QSettings
    from sponsorscout.scripts.packaging_smoke import _private_ini_settings
    QSettings.setPath(QSettings.IniFormat,QSettings.UserScope,str(tmp_path/'private-settings'))
    QSettings.setPath(QSettings.IniFormat,QSettings.SystemScope,str(tmp_path/'private-system-settings'))
    settings = _private_ini_settings('SponsorScout','SponsorScout')
    assert settings.format()==QSettings.IniFormat
    assert Path(settings.fileName()).is_relative_to(tmp_path/'private-settings')
    # Keep this isolation override complete if another production module starts
    # using a named QSettings constructor in future.
    for path in (ROOT/'sponsorscout').rglob('*.py'):
        if 'tests' in path.parts or path.name=='packaging_smoke.py':
            continue
        tree = ast.parse(path.read_text())
        named = [n for n in ast.walk(tree) if isinstance(n,ast.Call) and
                 isinstance(n.func,ast.Name) and n.func.id=='QSettings']
        if named:
            assert path.name=='assisted_ingestion.py',str(path)
