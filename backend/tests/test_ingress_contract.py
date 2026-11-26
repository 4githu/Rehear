"""Real loopback ingress routing; no real identity provider or credentials."""
import os
import shutil
import socket
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest
import uvicorn

from main import app

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT.parent / 'frontend'
SCRATCH = Path(os.environ['TMPDIR'])


def listener():
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    sock.listen(64)
    return sock


class LegacyAuth(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/auth/google/start?purpose=login&return_to=%2Fodi':
            self.send_response(302)
            self.send_header('Location', '/simulated-provider')
        elif self.path == '/auth/google/callback?error=access_denied':
            self.send_response(303)
            self.send_header('Location', '/odi?x=1&google_error=cancelled')
        elif self.path == '/auth/status':
            self.send_response(204)
        else:
            self.send_response(404)
        self.send_header('X-Test-Original-Host', self.headers['Host'])
        self.end_headers()

    def log_message(self, *_):
        pass


@pytest.fixture
def ingress():
    if not shutil.which('caddy'):
        pytest.skip('caddy not installed')
    auth = ThreadingHTTPServer(('127.0.0.1', 0), LegacyAuth)
    threading.Thread(target=auth.serve_forever, daemon=True).start()
    backend_sock = listener()
    backend_port = backend_sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level='error', lifespan='off'))
    thread = threading.Thread(target=server.run, kwargs={'sockets': [backend_sock]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(.02)
    assert server.started
    frontend_reserved = listener()
    frontend_port = frontend_reserved.getsockname()[1]
    frontend_reserved.close()
    # Never allow the dev server to auto-load local environment files.
    assert not any(FRONTEND.glob('.env*')), 'frontend environment files must not be loaded'
    frontend_env = {key: os.environ[key] for key in ('PATH', 'HOME', 'TMPDIR')}
    frontend_env.update(PORT=str(frontend_port), HOST='127.0.0.1',
                        DATABASE_URL=str(SCRATCH / 'rehear-ingress-frontend.db'),
                        API_UPSTREAM_URL=f'http://127.0.0.1:{backend_port}')
    for name in ('API_KEY', 'APP_ID', 'AUTH_DOMAIN', 'DATABASE_URL',
                 'MESSAGING_SENDER_ID', 'PROJECT_ID', 'STORAGE_BUCKET'):
        frontend_env['PUBLIC_FIREBASE_' + name] = ''
    frontend = subprocess.Popen(['node', 'node_modules/vite/bin/vite.js', '--strictPort'],
                                cwd=FRONTEND, env=frontend_env,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline:
        try:
            result = httpx.get(f'http://127.0.0.1:{frontend_port}/odi?x=1', timeout=1)
            if result.status_code == 308:
                break
        except httpx.TransportError:
            pass
        assert frontend.poll() is None, 'real frontend failed to start'
        time.sleep(.1)
    else:
        frontend.terminate()
        frontend.wait(timeout=10)
        pytest.fail('real frontend did not become ready')
    reserved = listener()
    port = reserved.getsockname()[1]
    reserved.close()
    env = dict(os.environ, LISTEN_PORT=str(port),
               LEGACY_AUTH_UPSTREAM=f'127.0.0.1:{auth.server_port}',
               REHEAR_BACKEND_UPSTREAM=f'127.0.0.1:{backend_port}',
               FRONTEND_UPSTREAM=f'127.0.0.1:{frontend_port}')
    process = subprocess.Popen(['caddy', 'run', '--config', str(ROOT / 'Caddyfile')], env=env,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f'http://127.0.0.1:{port}'
    try:
        while time.monotonic() < deadline:
            try:
                httpx.get(base + '/auth/status', timeout=.2)
                break
            except httpx.TransportError:
                assert process.poll() is None, 'Caddy failed to start'
                time.sleep(.05)
        else:
            pytest.fail('Caddy did not become ready')
        yield base
    finally:
        process.terminate()
        process.wait(timeout=10)
        frontend.terminate()
        frontend.wait(timeout=10)
        server.should_exit = True
        thread.join(timeout=10)
        auth.shutdown()
        auth.server_close()


def test_real_frontend_screen_redirect_preserves_query(ingress):
    response = httpx.get(ingress + '/odi?x=1')
    assert response.status_code == 308
    assert response.headers['location'] == '/?x=1'
    assert httpx.get(ingress + '/odi/login?x=1').headers['location'] == '/login?x=1'


def test_start_and_callback_reach_same_legacy_server_with_host_and_query(ingress):
    headers = {'Host': 'localhost'}
    start = httpx.get(ingress + '/auth/google/start?purpose=login&return_to=%2Fodi', headers=headers)
    assert start.status_code == 302, (start.status_code, start.headers.get('server'), start.headers.get('location'), start.headers.get('x-test-original-host'))
    assert start.headers['location'] == '/simulated-provider'
    assert start.headers['x-test-original-host'] == 'localhost'
    callback = httpx.get(ingress + '/auth/google/callback?error=access_denied', headers=headers)
    assert callback.status_code == 303
    assert callback.headers['location'] == '/odi?x=1&google_error=cancelled'
    assert callback.headers['x-test-original-host'] == 'localhost'
    screen = httpx.get(ingress + callback.headers['location'], headers=headers)
    assert screen.status_code == 308
    assert screen.headers['location'] == '/?x=1&google_error=cancelled'
    final = httpx.get(ingress + screen.headers['location'], headers=headers)
    assert final.status_code == 200
    assert 'text/html' in final.headers.get('content-type', '')
    assert httpx.get(ingress + '/auth/status', headers=headers).status_code == 204


def test_quest_route_reaches_rehear_backend_not_legacy_auth(ingress):
    r = httpx.post(ingress + '/odi/xreal_rehear/evc/smart-start')
    assert r.status_code == 422
    assert 'x-test-original-host' not in r.headers
    read = httpx.get(ingress + '/odi/xreal_rehear/evc/sessions/00000000-0000-0000-0000-000000000000')
    assert read.status_code == 401
    update = httpx.post(ingress + '/odi/xreal_rehear/evc/update')
    assert update.status_code == 422
    assert 'x-test-original-host' not in read.headers
    assert 'x-test-original-host' not in update.headers


@pytest.mark.parametrize('error,status,code', [
    ('configuration', 503, 'evaluation_provider_configuration_error'),
    ('provider', 502, 'evaluation_provider_error'),
])
def test_quest_provider_error_json_survives_loopback_caddy(ingress, monkeypatch, error, status, code):
    import importlib
    from odi.EVC.evaluation import EvaluationProviderError, EvaluationProviderConfigurationError
    router_module = importlib.import_module('odi.EVC.router')
    async def fail(**kwargs):
        if error == 'configuration':
            raise EvaluationProviderConfigurationError('synthetic')
        raise EvaluationProviderError('synthetic')
    monkeypatch.setattr(router_module, 'update_pipeline', fail)
    monkeypatch.setenv('DEEPGRAM_API_KEY', 'synthetic')
    start = httpx.post(ingress + '/odi/xreal_rehear/evc/smart-start',
                       data={'presentation_title': 'synthetic', 'seed': '10'})
    assert start.status_code == 200, start.text
    session = start.json()
    response = httpx.post(ingress + '/odi/xreal_rehear/evc/update',
        headers={'X-EVC-Session-Token': session['session_token']},
        data={'session_id': session['session_id'],
              'request_id': '00000000-0000-0000-0000-000000000001',
              'expected_step': '0', 'client_time_s': '1.0'},
        files={'audio': ('voice.wav', b'RIFF-test', 'audio/wav')})
    assert response.status_code == status
    assert response.headers['content-type'].startswith('application/json')
    assert response.json()['detail']['code'] == code


def test_all_backend_odi_namespaces_are_routed_without_screen_collisions(ingress):
    prefixes = ('/odi/db', '/odi/files', '/odi/coaching', '/odi/xreal_rehear')
    for path in app.openapi()['paths']:
        if path.startswith('/odi/'):
            assert any(path == p or path.startswith(p + '/') for p in prefixes), path
    for prefix in prefixes:
        for path in (prefix, prefix + '/not-a-real-route'):
            response = httpx.get(ingress + path)
            assert response.status_code in (404, 405), path
            assert response.headers.get('content-type', '').startswith('application/json'), path
    screen = httpx.get(ingress + '/odi/register?x=1')
    assert (screen.status_code, screen.headers['location']) == (308, '/rehear-register?x=1')
