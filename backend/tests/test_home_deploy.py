"""Deployment boundary and rollback contracts; no real services or credentials."""
import copy, io, json, subprocess, sys, tarfile
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'ops'))
import deploy
import machine
import legacy_bridge

SHA = 'a' * 40


def test_only_successful_main_push_from_this_repository_is_deployable():
    good = dict(head_sha=SHA, head_branch='main', event='push', conclusion='success',
                name='Isolated validation', repository={'full_name': 'owner/repo'})
    assert deploy.ci_passed([good], SHA, 'owner/repo')
    for change in [dict(event='pull_request'), dict(head_branch='feature'), dict(head_sha='b' * 40),
                   dict(conclusion='failure'), dict(conclusion=None), dict(name='other'),
                   dict(repository={'full_name': 'fork/repo'})]:
        assert not deploy.ci_passed([{**good, **change}], SHA, 'owner/repo')


def test_build_does_not_receive_runtime_credentials_or_auth_database(tmp_path, monkeypatch):
    monkeypatch.setenv('JWT_SECRET_KEY', 'synthetic-signing-secret')
    monkeypatch.setenv('GH_TOKEN', 'synthetic-github-token')
    deploy.atomic_json(tmp_path / 'frontend.json', {'PUBLIC_FIREBASE_APP_ID': 'synthetic-public',
                      'BETTER_AUTH_SECRET': 'synthetic-private', 'DATABASE_URL': '/private/auth.db'})
    env = deploy.build_environment({'home': str(tmp_path), 'node': '/node/bin/node'}, tmp_path / 'release')
    assert env['PUBLIC_FIREBASE_APP_ID'] == 'synthetic-public'
    assert env['DATABASE_URL'] == str(tmp_path / 'release/build-only.db')
    assert not {'JWT_SECRET_KEY', 'GH_TOKEN', 'BETTER_AUTH_SECRET'} & env.keys()


@pytest.mark.parametrize('maintenance', [False, True])
def test_generated_ingress_is_valid_caddy_and_keeps_auth_separate(tmp_path, maintenance):
    active = {'sha': SHA, 'backend_port': 8181, 'frontend_port': 5181}
    config = tmp_path / 'Caddyfile'; config.write_text(deploy.caddy_text({}, active, maintenance))
    result = subprocess.run(['caddy', 'validate', '--config', str(config), '--adapter', 'caddyfile'], capture_output=True)
    assert result.returncode == 0, result.stderr.decode()


def test_tunnel_switch_keeps_unrelated_hosts_and_auth_routes():
    source = '''tunnel: synthetic
credentials-file: /synthetic.json
ingress:
'''
    for host in ('rehear.kr', 'rehear.chobab.app', 'chobab.app', 'aura.chobab.app'):
        source += f'''  - hostname: {host}
    path: ^/odi/(db|files|xreal_rehear)(/.*)?$
    service: http://127.0.0.1:8000
  - hostname: {host}
    path: ^/(auth|api)(/.*)?$
    service: http://127.0.0.1:8000
  - hostname: {host}
    service: http://127.0.0.1:5173
'''
    source += '  - service: http_status:404\n'
    updated = machine.tunnel_routes(source)
    assert 'hostname: chobab.app\n    path: ^/(auth|api)(/.*)?$\n    service: http://127.0.0.1:8000' in updated
    assert 'hostname: aura.chobab.app\n    service: http://127.0.0.1:5173' in updated
    assert updated.count('db|coaching|files|xreal_rehear') == 4
    assert machine.tunnel_routes(updated) == updated


def test_source_archive_cannot_write_outside_release(tmp_path):
    archive = tmp_path / 'source.tar.gz'
    with tarfile.open(archive, 'w:gz') as tar:
        for name in ('root/backend/main.py', 'root/frontend/package-lock.json', 'root/../../escaped'):
            item = tarfile.TarInfo(name); item.size = 1; tar.addfile(item, io.BytesIO(b'x'))
    with pytest.raises(tarfile.FilterError):
        deploy.extract_release(archive, tmp_path / 'release')
    assert not (tmp_path.parent / 'escaped').exists()


def test_failed_candidate_is_stopped_before_previous_code_is_restored(tmp_path, monkeypatch):
    old = {'sha': SHA, 'slot': 'a', 'backend_port': 8181, 'frontend_port': 5181}
    deploy.atomic_json(tmp_path / 'state.json', {'active': old, 'previous': None, 'rejected': []})
    data = tmp_path / 'latest-user-data'; data.write_text('newest data must survive rollback')
    calls = []
    monkeypatch.setattr(deploy, 'check_idle', lambda _: None)
    monkeypatch.setattr(deploy, 'proxy', lambda *a, **kw: calls.append(('proxy', kw.get('maintenance', False))))
    monkeypatch.setattr(deploy, 'stop_release', lambda active: calls.append(('stop', active['slot'])))
    monkeypatch.setattr(deploy, 'data_snapshot', lambda *a, **kw: None)
    def launch(cfg, release, slot):
        calls.append(('launch', slot))
        if slot == 'b': raise RuntimeError('synthetic candidate startup failure')
        return old
    monkeypatch.setattr(deploy, 'launch_release', launch)
    with pytest.raises(RuntimeError, match='synthetic candidate'):
        deploy.activate({'home': str(tmp_path)}, {'sha': 'b' * 40})
    assert calls.index(('stop', 'b')) < calls.index(('launch', 'a'))
    assert deploy.read_state(tmp_path)['active'] == old
    assert data.read_text() == 'newest data must survive rollback'


def test_legacy_bridge_preserves_cookie_headers_and_unrelated_service(monkeypatch):
    import httpx
    from fastapi import FastAPI, Request
    from fastapi.responses import JSONResponse
    from fastapi.testclient import TestClient
    target = FastAPI()
    @target.post('/odi/db/probe')
    async def probe(request: Request):
        response = JSONResponse({'body': (await request.body()).decode(), 'cookie': request.cookies.get('synthetic')})
        response.set_cookie('one', '1'); response.set_cookie('two', '2')
        return response
    real = httpx.AsyncClient
    monkeypatch.setattr(legacy_bridge.httpx, 'AsyncClient', lambda **kw: real(transport=httpx.ASGITransport(app=target), **kw))
    app = FastAPI()
    @app.get('/auth/check')
    def auth(): return {'legacy': True}
    legacy_bridge.install(app, 'http://127.0.0.1:5180')
    with TestClient(app) as client:
        response = client.post('/odi/db/probe', content='synthetic body', cookies={'synthetic': 'value'})
        assert response.json() == {'body': 'synthetic body', 'cookie': 'value'}
        assert len(response.headers.get_list('set-cookie')) == 2
        assert client.get('/auth/check').json() == {'legacy': True}


def test_runtime_health_reports_busy_work_without_session_data(tmp_path, monkeypatch):
    import sqlite3
    from types import SimpleNamespace
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import ops_health
    from odi.EVC.session_store import session_store
    db = tmp_path / 'odi.db'
    with sqlite3.connect(db) as c:
        c.executescript("CREATE TABLE presentation_report_jobs(status TEXT); CREATE TABLE practice_attempts(state TEXT); INSERT INTO practice_attempts VALUES('analyzing');")
    monkeypatch.setattr(ops_health.odidb, 'DB_PATH', db)
    monkeypatch.setattr(session_store, '_sessions', {'synthetic': SimpleNamespace(presentation_status='running', last_access_monotonic=session_store._clock())})
    monkeypatch.setenv('REHEAR_REVISION', SHA)
    app = FastAPI(); ops_health.install(app)
    with TestClient(app, client=('127.0.0.1', 1234)) as client:
        assert client.get('/_ops/health').json() == {'status': 'ok', 'revision': SHA, 'active_sessions': 1, 'active_jobs': 1}
    with TestClient(app, client=('192.0.2.1', 1234)) as client:
        assert client.get('/_ops/health').status_code == 404


@pytest.mark.parametrize('ttl,age,status,expected', [
    (7200, 599, 'running', 1),
    (7200, 600, 'running', 1),
    (7200, 601, 'finishing', 1),
    (7200, 7199, 'running', 1),
    (7200, 7200, 'running', 0),
    (7200, 601, 'finished', 0),
    (300, 299, 'running', 1),
    (300, 300, 'running', 0),
    (10000, 7200, 'running', 1),
])
def test_deploy_health_uses_session_store_expiry(tmp_path, monkeypatch, ttl, age, status, expected):
    import asyncio
    import importlib
    import sqlite3
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import ops_health
    from odi.EVC.schema import SmartStartOptions
    from odi.EVC.session_store import SessionStore

    now = [0.0]
    store = SessionStore(ttl_s=ttl, monotonic_clock=lambda: now[0])
    options = SmartStartOptions(presentation_title='Synthetic', topic_interest=.5, prior_knowledge=.5, seed=1)
    record, token = asyncio.run(store.create_session(options))
    record.presentation_status = status
    now[0] = age
    if age < ttl:
        assert not store._is_expired(record)
    else:
        assert store._is_expired(record)
    monkeypatch.setattr(importlib.import_module('odi.EVC.session_store'), 'session_store', store)
    db = tmp_path / 'odi.db'
    with sqlite3.connect(db) as c:
        c.executescript('CREATE TABLE presentation_report_jobs(status TEXT); CREATE TABLE practice_attempts(state TEXT);')
    monkeypatch.setattr(ops_health.odidb, 'DB_PATH', db)
    app = FastAPI(); ops_health.install(app)
    with TestClient(app, client=('127.0.0.1', 1234)) as client:
        response = client.get('/_ops/health')
    assert response.json()['active_sessions'] == expected
    monkeypatch.setattr(deploy, 'request', lambda *args, **kwargs: (200, response.content))
    if expected:
        with pytest.raises(deploy.Deferred):
            deploy.check_idle({'backend_port': 1234})
    else:
        deploy.check_idle({'backend_port': 1234})
    if age < ttl:
        assert asyncio.run(store.get_authorized_session(record.session_id, token)) is record


def test_initial_migration_restores_original_paths_on_partial_failure(tmp_path, monkeypatch):
    import sqlite3
    home = tmp_path / 'runtime'; home.mkdir()
    legacy = tmp_path / 'legacy'; (legacy / 'odi/db').mkdir(parents=True)
    media = legacy / 'storage/odi'; media.mkdir(parents=True)
    (media / 'synthetic.txt').write_text('preserved media')
    db = legacy / 'odi/db/odi.db'
    with sqlite3.connect(db) as conn:
        conn.execute('CREATE TABLE synthetic(value TEXT)')
        conn.execute("INSERT INTO synthetic VALUES ('preserved database')")
    original = Path.symlink_to
    def fail_media(self, target, **kwargs):
        if self == media:
            raise OSError('synthetic path installation failure')
        return original(self, target, **kwargs)
    monkeypatch.setattr(Path, 'symlink_to', fail_media)
    cfg = {'home': str(home), 'legacy_backend': str(legacy)}
    with pytest.raises(OSError, match='synthetic path'):
        machine.initialize_data(cfg)
    assert not db.is_symlink() and not media.is_symlink()
    with sqlite3.connect(db) as conn:
        assert conn.execute('SELECT value FROM synthetic').fetchone()[0] == 'preserved database'
    assert (media / 'synthetic.txt').read_text() == 'preserved media'
    assert not (home / 'data-initialized').exists()
    monkeypatch.setattr(Path, 'symlink_to', original)
    machine.initialize_data(cfg)
    assert db.is_symlink() and media.is_symlink()
    assert (home / 'data-initialized').is_file()
    assert (media / 'synthetic.txt').read_text() == 'preserved media'


def test_legacy_patch_is_compilable_and_idempotent(tmp_path):
    home = tmp_path / 'runtime'; home.mkdir()
    legacy = tmp_path / 'legacy'; (legacy / 'odi/files').mkdir(parents=True)
    main = legacy / 'main.py'
    main.write_text('app = None\n# Ensure additive report tables\nrecover()\n\napp.include_router(auth_router)\n')
    service = legacy / 'odi/files/service.py'
    service.write_text('from pathlib import Path\nimport json\nBASE_DIR = Path(__file__).resolve().parents[2]\n')
    cfg = {'home': str(home), 'legacy_backend': str(legacy)}
    machine.patch_legacy(cfg)
    once = (main.read_text(), service.read_text())
    machine.patch_legacy(cfg)
    assert once == (main.read_text(), service.read_text())
    assert 'else:\n    # Ensure additive report tables\n    recover()' in once[0]
    assert 'app.include_router(auth_router)' in once[0]
    compile(once[0], str(main), 'exec'); compile(once[1], str(service), 'exec')


def test_retention_preserves_active_previous_initial_and_unmanaged_files(tmp_path):
    import os
    releases = tmp_path / 'releases'; releases.mkdir()
    backups = tmp_path / 'backups'; backups.mkdir()
    for i, ch in enumerate('abcde'):
        p = releases / (ch * 40); p.mkdir(); os.utime(p, (i + 1, i + 1))
    external = tmp_path / 'external'; external.mkdir(); (external / 'keep').touch()
    (releases / ('f' * 40)).symlink_to(external, target_is_directory=True)
    (backups / '20261123-000000-000000-initial').mkdir()
    (backups / 'operator-notes').mkdir()
    for i in range(5):
        p = backups / f'20261123-00000{i}-000000-aaaaaaaaaa'; p.mkdir(); os.utime(p, (i + 1, i + 1))
    deploy.atomic_json(tmp_path / 'state.json', {'active': {'sha': 'a'*40}, 'previous': {'sha': 'b'*40}})
    deploy.prune_generated_artifacts({'home': str(tmp_path)})
    assert {p.name for p in releases.iterdir() if not p.is_symlink()} == {c * 40 for c in 'abe'}
    assert (external / 'keep').exists()
    assert len(list(backups.iterdir())) == 5
    assert (backups / '20261123-000000-000000-initial').exists()
    assert (backups / 'operator-notes').exists()
