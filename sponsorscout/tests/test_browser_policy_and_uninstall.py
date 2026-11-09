"""Installed-browser consent, shared identity, isolated probes and safe deletion."""
import contextlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from sponsorscout.services import browser_policy as bp
from sponsorscout.services import data_ownership as own


def candidate(tmp_path,family='Brave',kind='installed'):
    path=tmp_path/'browser-executable';path.write_text('fixture executable')
    return dict(family=family,kind=kind,path=str(path))


@pytest.mark.parametrize('family,channel',[('Chrome','chrome'),('Edge','msedge'),('Brave','chromium'),('Opera','chromium'),('Vivaldi','chromium'),('Chromium','chromium')])
@pytest.mark.parametrize('headless',[True,False])
def test_shared_identity_preserves_mode_and_args(tmp_path,family,channel,headless):
    c=candidate(tmp_path,family);c.update(validated=True,fingerprint=bp.fingerprint(c['path']))
    bp.save_selection(c);browser_type=SimpleNamespace(launch=Mock(return_value='browser'))
    assert bp.launch_chromium(browser_type,headless=headless,args=['--disable-gpu'])=='browser'
    call=browser_type.launch.call_args.kwargs
    assert call['executable_path']==c['path'] and call['channel']==channel and call['headless']==headless
    assert call['args'][0]=='--disable-gpu' and call['args'][-1].startswith('--crash-dumps-dir=')
    assert Path(call['env']['HOME']).is_relative_to(bp.state_dir())
    assert Path(call['downloads_path']).is_relative_to(bp.state_dir())


@pytest.mark.parametrize('extra',[{'channel':'chrome'},{'executable_path':'/tmp/other'},{'user_data_dir':'/personal'},{'args':['--profile-directory=Default']},{'args':['--user-data-dir=/personal']},{'args':['--remote-debugging-port=9222']}])
def test_no_personal_profile_or_attach_override(extra):
    with pytest.raises(ValueError):bp.launch_chromium(Mock(),**extra)


def test_missing_config_never_downloads(monkeypatch):
    bp.config_path().unlink();download=Mock(side_effect=AssertionError('must not download'))
    monkeypatch.setattr(bp.subprocess,'run',download)
    with pytest.raises(RuntimeError,match='Browser setup required'):bp.launch_chromium(Mock(),headless=True)
    download.assert_not_called()


@pytest.mark.parametrize('bad',[None,{}, {'kind':'installed','family':'Firefox','path':'/usr/bin/firefox','validated':True}, {'path':'relative','validated':True,'family':'Chrome','kind':'installed'}, [], 'bad'])
def test_invalid_config_is_not_usable(bad):
    bp.config_path().write_text(json.dumps(bad))
    assert bp.read_selection() is None


def test_changed_browser_needs_validation_again(tmp_path):
    c=candidate(tmp_path);c.update(validated=True,fingerprint=bp.fingerprint(c['path']))
    bp.save_selection(c);assert bp.read_selection()
    Path(c['path']).write_text('changed executable/version')
    assert bp.read_selection() is None


def test_unvalidated_choice_cannot_be_saved(tmp_path):
    with pytest.raises(ValueError):bp.save_selection(candidate(tmp_path))


def fake_probe(monkeypatch,fail_mode=None,profile='/tmp/playwright_chromiumdev_profile-fixture'):
    import playwright.sync_api
    calls=[];browsers=[]
    def launch(**kwargs):
        calls.append(kwargs)
        if kwargs['headless']==fail_mode:raise RuntimeError('probe launch failed')
        session=SimpleNamespace(send=lambda _:dict(arguments=['--user-data-dir='+profile]),detach=Mock())
        page=SimpleNamespace(set_content=Mock(),title=lambda:'SponsorScout browser check')
        browser=SimpleNamespace(new_page=lambda:page,close=Mock(),version='fixture',new_browser_cdp_session=lambda:session)
        browsers.append(browser);return browser
    factory=lambda:contextlib.nullcontext(SimpleNamespace(chromium=SimpleNamespace(launch=launch)))
    monkeypatch.setattr(playwright.sync_api,'sync_playwright',factory)
    return calls,browsers


@pytest.mark.parametrize('family',bp.FAMILIES)
def test_both_modes_local_page_isolated_and_closed(tmp_path,monkeypatch,family):
    calls,browsers=fake_probe(monkeypatch)
    result=bp.validate(candidate(tmp_path,family))
    assert result['validated'] and result['versions']==['fixture','fixture']
    assert [c['headless'] for c in calls]==[True,False]
    assert all(b.close.call_count==1 for b in browsers)
    for b in browsers:b.new_page().set_content.assert_called_once()


@pytest.mark.parametrize('mode',[True,False])
def test_probe_failure_cannot_be_saved(tmp_path,monkeypatch,mode):
    calls,browsers=fake_probe(monkeypatch,fail_mode=mode)
    with pytest.raises(RuntimeError):bp.validate(candidate(tmp_path))
    assert all(b.close.call_count==1 for b in browsers)


def test_browser_ignoring_isolated_profile_is_rejected(tmp_path,monkeypatch):
    calls,browsers=fake_probe(monkeypatch,profile='/home/person/.config/google-chrome')
    with pytest.raises(RuntimeError,match='isolated temporary profile'):bp.validate(candidate(tmp_path))
    assert browsers[0].close.called


@pytest.mark.parametrize('family',['Firefox','LibreWolf','Unknown'])
def test_firefox_is_manual_only(tmp_path,family):
    with pytest.raises(ValueError):bp.validate(candidate(tmp_path,family))


@pytest.mark.parametrize('consent',[False,None,0,'yes',1])
def test_download_requires_actual_explicit_boolean(consent,monkeypatch):
    run=Mock();monkeypatch.setattr(bp.subprocess,'run',run)
    with pytest.raises(PermissionError):bp.install_managed(consent=consent)
    run.assert_not_called()


def test_frozen_safe_download_uses_bundled_node_and_no_shell(tmp_path,monkeypatch):
    import playwright._impl._driver as driver
    node=tmp_path/'node';cli=tmp_path/'driver'/'cli.js';cli.parent.mkdir();cli.write_text('')
    (cli.parent/'browsers.json').write_text(json.dumps(dict(browsers=[dict(name='chromium',revision='123')])) )
    monkeypatch.setattr(driver,'compute_driver_executable',lambda:(str(node),str(cli)))
    executable='chrome.exe' if os.name=='nt' else 'chrome'
    target=bp.state_dir()/'browser'/'chromium-123'/('chrome-win64'if os.name=='nt'else'chrome-linux64');target.mkdir(parents=True);(target/executable).write_text('')
    run=Mock(return_value=SimpleNamespace(returncode=0,stderr='',stdout=''))
    monkeypatch.setattr(bp.subprocess,'run',run)
    result=bp.install_managed(consent=True)
    assert result['kind']=='managed' and Path(result['path']).name==executable
    argv=run.call_args.args[0]
    assert argv==[str(node),str(cli),'install','chromium','--no-shell']
    assert run.call_args.kwargs['env']['PLAYWRIGHT_BROWSERS_PATH']==str(bp.state_dir()/'browser')
    assert '--with-deps' not in argv and '--only-shell' not in argv


def test_download_failure_is_reported(monkeypatch):
    monkeypatch.setattr(bp.subprocess,'run',Mock(return_value=SimpleNamespace(returncode=1,stderr='offline',stdout='')))
    with pytest.raises(RuntimeError,match='offline'):bp.install_managed(consent=True)


def test_linux_detection_all_families_not_firefox(tmp_path,monkeypatch):
    binaries={}
    for name in ['google-chrome','microsoft-edge','brave-browser','opera','vivaldi','chromium','firefox']:
        p=tmp_path/name;p.write_text('');binaries[name]=str(p)
    monkeypatch.setattr(bp.sys,'platform','linux');monkeypatch.setattr(bp.shutil,'which',binaries.get)
    assert {c['family']for c in bp.discover()}==set(bp.FAMILIES)


def test_windows_known_install_locations_all_families(tmp_path,monkeypatch):
    monkeypatch.setattr(bp.sys,'platform','win32')
    for name in ('PROGRAMFILES','PROGRAMFILES(X86)','LOCALAPPDATA'):monkeypatch.setenv(name,str(tmp_path))
    for suffix in ['Google/Chrome/Application/chrome.exe','Microsoft/Edge/Application/msedge.exe','BraveSoftware/Brave-Browser/Application/brave.exe','Programs/Opera/launcher.exe','Vivaldi/Application/vivaldi.exe','Chromium/Application/chrome.exe']:
        p=tmp_path/suffix;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('')
    assert {c['family']for c in bp.discover()}==set(bp.FAMILIES)


def test_custom_data_database_sidecars_backups_cache_and_settings_removed(tmp_path):
    data=tmp_path/'shared';data.mkdir();db=tmp_path/'separate'/'jobs.sqlite3';db.parent.mkdir();db.write_text('db')
    for suffix in ('-wal','-shm','-journal'):Path(str(db)+suffix).write_text('sqlite sidecar')
    own.register_runtime(data,db)
    for name in ('locale.json','provider_cache.json','jd_cache.json'):(data/name).write_text('app data')
    seeds=data/'seeds';seeds.mkdir(exist_ok=True);(seeds/'company_ATS_seed.csv').write_text('app seed')
    keep=data/'my-unrelated-file.txt';keep.write_text('keep')
    output=data/'scan_output';unrelated=output/'personal.txt';unrelated.write_text('keep nested file')
    base=output/'20261105T180000_career_jobs.csv';own.register_scan_outputs(base);base.write_text('app output')
    backup=db.parent/'maintenance_backups'/'pre_data_review_20261105.sqlite3';backup.parent.mkdir();backup.write_text('backup')
    audit=backup.with_suffix('.json');audit.write_text('audit');own.register_artifacts(backup,audit)
    managed=bp.state_dir()/'browser'/'chromium-123';managed.mkdir(parents=True);(managed/'chrome').write_text('managed browser')
    settings=Path.home()/'.config/SponsorScout/SponsorScout.conf';settings.parent.mkdir(parents=True);settings.write_text('Qt setting')
    personal=Path.home()/'.config/google-chrome/Default/Cookies';personal.parent.mkdir(parents=True);personal.write_text('personal')
    cache=Path.home()/'.cache/ms-playwright/chromium-existing';cache.mkdir(parents=True);(cache/'chrome').write_text('shared cache')
    result=own.purge_user_data();assert not result['errors']
    assert not db.exists() and not backup.exists() and not audit.exists() and not base.exists() and not managed.exists() and not settings.exists()
    assert not any(Path(str(db)+suffix).exists()for suffix in ('-wal','-shm','-journal'))
    assert keep.read_text()=='keep' and unrelated.exists() and personal.exists() and cache.exists()
    assert not bp.state_dir().exists()


def test_old_custom_locations_remain_tracked_after_environment_changes(tmp_path,monkeypatch):
    one=tmp_path/'first';two=tmp_path/'second'
    own.register_runtime(one,one/'db.sqlite3');(one/'db.sqlite3').write_text('first')
    own.register_runtime(two,two/'db.sqlite3');(two/'db.sqlite3').write_text('second')
    monkeypatch.delenv('SPONSORSCOUT_DATA_DIR',raising=False);monkeypatch.delenv('SPONSORSCOUT_DB_PATH',raising=False)
    assert not own.purge_user_data()['errors']
    assert not one.exists() and not two.exists()


@pytest.mark.parametrize('relative',['.config/google-chrome/Default','.config/microsoft-edge/Default','.config/BraveSoftware/Brave-Browser','.config/opera','.config/vivaldi','.mozilla/firefox','.config/chromium/Default','.cache/ms-playwright'])
def test_protected_personal_and_shared_locations_never_registered(relative):
    with pytest.raises(ValueError):own.register_runtime(Path.home()/relative,Path.home()/relative/'db')


def test_uninstall_does_not_follow_directory_or_file_symlinks(tmp_path):
    data=tmp_path/'data';own.register_runtime(data,data/'db')
    outside=tmp_path/'outside';outside.mkdir();(outside/'personal').write_text('keep')
    (data/'scan_output'/'external').symlink_to(outside,target_is_directory=True)
    nested=bp.state_dir()/'nested';nested.symlink_to(outside,target_is_directory=True)
    file=data/'locale.json';file.symlink_to(outside/'personal')
    assert not own.purge_user_data()['errors']
    assert (outside/'personal').read_text()=='keep'
    assert not file.exists()
    # Unrelated links inside a shared custom container are not app-owned.
    assert (data/'scan_output'/'external').is_symlink()


def test_manifest_cannot_claim_arbitrary_recursive_directory(tmp_path):
    victim=tmp_path/'not-app-data';victim.mkdir();(victim/own.MARKER).write_text('fake marker');(victim/'keep').write_text('keep')
    own._append(dict(kind='directory',path=str(victim)))
    assert own.purge_user_data()['errors'] and (victim/'keep').exists()


def test_malformed_records_report_incomplete_and_preserve_retry_manifest():
    path=bp.state_dir()/own.MANIFEST;path.write_text('not json\n'+json.dumps(dict(kind='file',path='/'))+'\n')
    result=own.purge_user_data()
    assert result['errors'] and path.exists()


def test_launch_sites_share_policy_and_no_persistent_profiles():
    root=Path(__file__).parents[1]
    paths=['services/browser_fetcher.py','scanning/ats/ats_scanner.py','scanning/career/career_scanner.py','core/blocked_portals.py','main.py']
    source='\n'.join((root/p).read_text()for p in paths)
    assert source.count('launch_chromium(')==9
    assert '.chromium.launch(' not in source and 'launch_persistent_context'not in source


def test_native_packaging_is_lean_and_cleanup_is_pre_removal():
    root=Path(__file__).parents[2]
    for name in ('build_deb.sh','build_rpm.sh','build_exe.ps1'):
        text=(root/name).read_text()
        assert '--only-shell'not in text and 'playwright install chromium'not in text
        assert 'SPONSORSCOUT_VALIDATE_BROWSER'in text and '--self-check-browser'in text
    deb=(root/'build_deb.sh').read_text();rpm=(root/'build_rpm.sh').read_text();win=(root/'installer.iss').read_text()
    assert 'runuser -u "$name" -- env -i'in deb and 'runuser -u "$name" -- env -i'in rpm
    assert '[ "${1:-}" = "remove" ]'in deb and '[ "${1:-1}" = "0" ]'in rpm
    assert 'PrivilegesRequired=lowest'in win and '--purge-user-data'in win
    assert '[UninstallDelete]'not in win and 'DelTree('not in win
    assert 'InitializeUninstall'in win and 'InitializeSetup'in win


@pytest.mark.skipif(os.environ.get('SPONSORSCOUT_TEST_BROWSER')!='1',reason='explicit local browser test opt-in')
def test_real_full_chromium_both_modes_and_isolated_contexts():
    c=bp.read_selection();assert c
    result=bp.validate(c);bp.save_selection(result)
    from playwright.sync_api import sync_playwright
    with bp.managed_playwright()as pw:
        browser=bp.launch_chromium(pw.chromium,headless=True,args=['--no-sandbox','--enable-automation'])
        try:
            session=browser.new_browser_cdp_session()
            arguments=session.send('Browser.getBrowserCommandLine')['arguments'];session.detach()
            profile=Path(next(a.split('=',1)[1]for a in arguments if a.startswith('--user-data-dir=')))
            assert profile.is_relative_to(bp.private_temp_dir())
            assert not (Path.home()/'.config/google-chrome-for-testing').exists()
            a=browser.new_context();b=browser.new_context()
            a.add_cookies([dict(name='private',value='only-context-a',url='https://local.invalid')])
            assert a.cookies('https://local.invalid') and not b.cookies('https://local.invalid')
            a.close();b.close()
        finally:browser.close()


def test_adopts_only_recognized_legacy_maintenance_pairs(tmp_path):
    data=tmp_path/'custom';db=data/'jobs.sqlite3';folder=data/'maintenance_backups';folder.mkdir(parents=True)
    backup=folder/'pre_alias_cleanup_20260101.sqlite3';backup.write_text('legacy app backup')
    audit=folder/'alias_cleanup_20260101.json';audit.write_text(json.dumps(dict(operation='explicit trusted-posting alias merge',backup=str(backup))))
    unrelated=folder/'my-backup.sqlite3';unrelated.write_text('not ours')
    own.register_runtime(data,db)
    assert not own.purge_user_data()['errors']
    assert not backup.exists() and not audit.exists() and unrelated.exists()


def test_only_unused_private_browser_builds_pruned(tmp_path):
    root=bp.state_dir()/'browser';keep=root/'chromium-1248';keep.mkdir(parents=True)
    old=root/'chromium-1111';old.mkdir();(old/'chrome').write_text('old')
    shell=root/'chromium_headless_shell-1111';shell.mkdir()
    other=root/'unrelated';other.mkdir();(other/'keep').write_text('not ours')
    exe=keep/'chrome';exe.write_text('new')
    c=dict(kind='managed',family='Chromium',path=str(exe),validated=True,fingerprint=bp.fingerprint(str(exe)))
    bp.save_selection(c)
    assert keep.exists() and not old.exists() and not shell.exists() and other.exists()


def test_reparse_point_is_treated_as_a_link(monkeypatch,tmp_path):
    p=tmp_path/'junction'
    with monkeypatch.context() as m:
        m.setattr(Path,'is_symlink',lambda self:False)
        m.setattr(Path,'lstat',lambda self:SimpleNamespace(st_file_attributes=0x400))
        assert bp.is_link(p) and not own._no_symlinks(p)


def test_selection_write_refuses_linked_state_folder(tmp_path,monkeypatch):
    outside=tmp_path/'outside';outside.mkdir();link=tmp_path/'state';link.symlink_to(outside,target_is_directory=True)
    monkeypatch.setattr(bp,'state_dir',lambda:link)
    c=candidate(tmp_path);c.update(validated=True,fingerprint=bp.fingerprint(c['path']))
    with pytest.raises(ValueError,match='symlink'):bp.save_selection(c)
    assert bp.read_selection() is None and not (outside/'browser.json').exists()


def test_windows_cancelled_uninstaller_cannot_purge_data():
    source=(Path(__file__).parents[2]/'installer.iss').read_text()
    startup=source.split('function InitializeUninstall(): Boolean;',1)[1].split('procedure CurUninstallStepChanged',1)[0]
    assert '--purge-user-data'not in startup and 'taskkill'not in startup
    action=source.split('procedure CurUninstallStepChanged',1)[1]
    assert 'if CurUninstallStep <> usUninstall then'in action and 'RaiseException('in action


@pytest.mark.skipif(os.name=='nt',reason='POSIX package script harness')
@pytest.mark.parametrize('package,remove,upgrade',[('build_deb.sh','remove','upgrade'),('build_rpm.sh','0','1')])
def test_linux_pre_removal_hook_delegates_each_account_and_preserves_upgrade(tmp_path,package,remove,upgrade):
    import subprocess
    root=Path(__file__).parents[2];text=(root/package).read_text()
    if package=='build_deb.sh':hook=text.split('cat > "$DEBIAN_DIR/prerm" <<\'EOL\'\n',1)[1].split('\nEOL',1)[0]
    else:hook='#!/bin/sh\n'+text.split('%preun\n',1)[1].split('\nEOL',1)[0]
    commands=tmp_path/'bin';commands.mkdir();log=tmp_path/'calls'
    home=tmp_path/'account-home';home.mkdir();legacy=tmp_path/'legacy';(legacy/'.sponsorscout').mkdir(parents=True)
    accounts='\n'.join([f'alice:x:1000:1000::{home}:/bin/sh',f'root:x:0:0::{home}:/bin/sh',f'legacy:x:501:501::{legacy}:/bin/sh',f'service:x:99:99::{home}:/usr/sbin/nologin'])
    for name,body in {
        'getent':'#!/bin/sh\ncat <<\'ACCOUNTS\'\n'+accounts+'\nACCOUNTS\n',
        'pgrep':'#!/bin/sh\nexit 1\n',
        'runuser':'#!/bin/sh\nprintf \'%s\\n\' "$*" >> "'+str(log)+'"\nexit '+os.environ.get('NEVER_SET_TEST_VALUE','0')+'\n',
    }.items():
        path=commands/name;path.write_text(body);path.chmod(0o755)
    executable=tmp_path/'SponsorScout';executable.write_text('#!/bin/sh\nexit 0\n');executable.chmod(0o755)
    script=tmp_path/'pre-remove';script.write_text(hook.replace('/opt/sponsorscout/SponsorScout',str(executable)))
    env=dict(os.environ,PATH=str(commands)+os.pathsep+os.environ['PATH'])
    assert subprocess.run(['/bin/sh','-n',str(script)],env=env,capture_output=True).returncode==0
    assert subprocess.run(['/bin/sh',str(script),upgrade],env=env,capture_output=True).returncode==0 and not log.exists()
    assert subprocess.run(['/bin/sh',str(script),remove],env=env,capture_output=True).returncode==0
    lines=log.read_text().splitlines();assert len(lines)==3
    assert all('-- env -i HOME='in line and '--purge-user-data'in line for line in lines)
    assert ['-u '+name in line for name,line in zip(('alice','root','legacy'),lines)]==[True,True,True]
    (commands/'runuser').write_text('#!/bin/sh\nexit 1\n')
    assert subprocess.run(['/bin/sh',str(script),remove],env=env,capture_output=True).returncode!=0


@pytest.mark.skipif(os.name=='nt',reason='Native HKCU subprocess requires a disposable Windows account')
def test_source_purge_cli_works_headlessly_without_custom_env(tmp_path):
    import subprocess,sys
    home=tmp_path/'cli-profile';home.mkdir();data=tmp_path/'custom-data';db=data/'jobs.sqlite3'
    env=dict(os.environ,HOME=str(home),APPDATA=str(home),XDG_CONFIG_HOME=str(home/'.config'),SPONSORSCOUT_DATA_DIR=str(data),SPONSORSCOUT_DB_PATH=str(db))
    source='from sponsorscout.services.data_ownership import register_runtime;register_runtime();from pathlib import Path;Path('+repr(str(db))+').write_text("owned SQLite test file")'
    subprocess.run([sys.executable,'-c',source],env=env,check=True,capture_output=True)
    env.pop('SPONSORSCOUT_DATA_DIR');env.pop('SPONSORSCOUT_DB_PATH');env.pop('DISPLAY',None)
    result=subprocess.run([sys.executable,'-m','sponsorscout.main','--purge-user-data'],env=env,capture_output=True,text=True)
    assert result.returncode==0 and not json.loads(result.stdout)['errors'] and not db.exists()


def test_freezer_hook_excludes_hermetic_browser_data_and_binaries():
    import runpy
    hook=Path(__file__).parents[2]/'build_hooks/hook-playwright.py'
    namespace=runpy.run_path(str(hook));filter_items=namespace['without_browser_cache']
    entries=[('sdk/driver/node','playwright/driver'),('sdk/driver/package/cli.js','playwright/driver/package'),('sdk/driver/package/.local-browsers/chromium-123/chrome','playwright/driver/package/.local-browsers/chromium-123'),('sdk\\driver\\package\\.local-browsers\\chrome.dll','playwright/driver/package/.local-browsers')]
    assert filter_items(entries)==entries[:2]
    assert any('cli.js'in src for src,dst in namespace['datas'])
    assert all('.local-browsers'not in (str(src)+str(dst))for src,dst in namespace['datas']+namespace['binaries'])


def test_driver_temp_environment_restored_on_start_failure(monkeypatch):
    import playwright.sync_api
    before={key:os.environ.get(key)for key in ('TMPDIR','TEMP','TMP')}
    class Broken:
        def __enter__(self):
            assert 'sponsorscout-'in os.environ['TMPDIR'] or os.name=='nt'
            raise RuntimeError('driver startup failed')
    monkeypatch.setattr(playwright.sync_api,'sync_playwright',lambda:Broken())
    with pytest.raises(RuntimeError):bp.managed_playwright().start()
    assert {key:os.environ.get(key)for key in before}==before


def test_private_temps_are_short_owned_and_removed_with_data():
    temp=bp.private_temp_dir();(temp/'app-temp').write_text('owned temp')
    if os.name!='nt':
        assert len(str(temp).encode())<60 and temp.stat().st_mode & 0o077==0
        assert temp.stat().st_uid==os.getuid()
    assert not own.purge_user_data()['errors'] and not temp.exists()


def test_loose_career_script_bootstraps_before_shared_browser_import(tmp_path):
    import subprocess,sys
    script=Path(__file__).parents[1]/'scanning/career/career_scanner.py'
    env=dict(os.environ,HOME=str(tmp_path/'home'),SPONSORSCOUT_DATA_DIR=str(tmp_path/'data'))
    env.pop('PYTHONPATH',None)
    result=subprocess.run([sys.executable,'-I',str(script),'--help'],cwd=tmp_path,env=env,capture_output=True,text=True,timeout=20)
    assert result.returncode==0 and 'usage:'in result.stdout.lower(),result.stderr
