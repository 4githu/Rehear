"""Mac home deployment. Only successful push CI on the current main can deploy.

Builds run without runtime secrets. Data stays outside immutable releases. The
legacy Sushi service forwards ODI requests to one selected backend. Rollback
changes code, never restores an old database over newer user data.
"""
from __future__ import annotations
import argparse, contextlib, fcntl, hashlib, json, os, plistlib, re, shutil
import socket, sqlite3, subprocess, tarfile, tempfile, time, urllib.request
from pathlib import Path

SHA = re.compile(r'[0-9a-f]{40}')
PREFIXES = ('/odi/db', '/odi/files', '/odi/coaching', '/odi/xreal_rehear')


class Deferred(RuntimeError):
    pass


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2))
    temp.chmod(0o600)
    os.replace(temp, path)


def clean_env(cfg):
    env = {k: os.environ[k] for k in ('HOME', 'LANG', 'TMPDIR') if k in os.environ}
    env['PATH'] = str(Path(cfg['node']).parent) + ':/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin'
    return env


def run(args, *, cwd=None, env=None, log=None, check=True):
    result = subprocess.run([str(a) for a in args], cwd=cwd, env=env,
                            stdout=log or subprocess.PIPE, stderr=log or subprocess.PIPE)
    if check and result.returncode:
        raise RuntimeError('Command failed: ' + Path(str(args[0])).name + ' (see local build/service log)')
    return result


def ci_passed(runs, sha, repo):
    return any(r.get('head_sha') == sha and r.get('head_branch') == 'main'
               and r.get('event') == 'push' and r.get('conclusion') == 'success'
               and r.get('name') == 'Isolated validation'
               and r.get('repository', {}).get('full_name') == repo for r in runs)


def gh(cfg, endpoint):
    result = run([cfg['gh'], 'api', endpoint], env=clean_env(cfg))
    return json.loads(result.stdout)


def checked_main(cfg):
    sha = gh(cfg, f"repos/{cfg['repo']}/git/ref/heads/main")['object']['sha']
    if not SHA.fullmatch(sha):
        raise RuntimeError('Invalid main revision')
    runs = gh(cfg, f"repos/{cfg['repo']}/actions/runs?branch=main&event=push&per_page=40")['workflow_runs']
    if not ci_passed(runs, sha, cfg['repo']):
        raise Deferred('Current main is waiting for successful CI')
    return sha


def request(url, *, host=None, timeout=10):
    headers = {'Host': host} if host else {}
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout) as response:
        return response.status, response.read()


def wait_http(url, *, host=None, timeout=40):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            return request(url, host=host, timeout=2)
        except (OSError, TimeoutError):
            time.sleep(.2)
    raise RuntimeError('Local service did not become healthy')


def extract_release(archive, destination):
    destination = Path(destination)
    with tarfile.open(archive, 'r:gz') as tar:
        names = tar.getnames()
        roots = {n.split('/')[0] for n in names}
        if len(roots) != 1:
            raise RuntimeError('Unexpected source archive layout')
        with tempfile.TemporaryDirectory(dir=destination.parent) as temp:
            tar.extractall(temp, filter='data')
            source = Path(temp) / roots.pop()
            if not (source / 'backend/main.py').is_file() or not (source / 'frontend/package-lock.json').is_file():
                raise RuntimeError('Archive does not contain Rehear')
            os.replace(source, destination)


def build_environment(cfg, release):
    env = clean_env(cfg)
    frontend = json.loads((Path(cfg['home']) / 'frontend.json').read_text())
    env.update({k: v for k, v in frontend.items() if k.startswith(('PUBLIC_', 'VITE_'))})
    env.update(BUILD_TARGET='node', DATABASE_URL=str(Path(release) / 'build-only.db'))
    return env


def prepare(cfg, sha):
    home = Path(cfg['home'])
    release = home / 'releases' / sha
    marker = release / '.prepared.json'
    if marker.is_file():
        return json.loads(marker.read_text())
    if release.exists():
        # Only an incomplete, explicitly named source revision can be replaced.
        if not SHA.fullmatch(release.name) or release.is_symlink():
            raise RuntimeError('Invalid release directory')
        shutil.rmtree(release)
    release.parent.mkdir(exist_ok=True)
    archive = home / 'source.tar.gz'
    with archive.open('wb') as output:
        run([cfg['gh'], 'api', f"repos/{cfg['repo']}/tarball/{sha}"], env=clean_env(cfg), log=output)
    extract_release(archive, release)
    archive.unlink()
    if any(p.name != '.env.example' for folder in ('frontend', 'backend') for p in (release / folder).glob('.env*')):
        raise RuntimeError('Source must not contain runtime environment files')
    with (release / 'build.log').open('w') as log:
        env = build_environment(cfg, release)
        run([cfg['npm'], 'ci'], cwd=release / 'frontend', env=env, log=log)
        run([cfg['npm'], 'run', 'build'], cwd=release / 'frontend', env=env, log=log)
        digest = hashlib.sha256()
        for file in sorted((release / 'backend').glob('requirements*.txt')):
            digest.update(file.name.encode()); digest.update(file.read_bytes())
        venv = home / 'environments' / digest.hexdigest()[:24]
        if not (venv / '.ready').is_file():
            venv.parent.mkdir(exist_ok=True)
            run([cfg['base_python'], '-m', 'venv', venv], env=clean_env(cfg), log=log)
            run([venv / 'bin/python', '-m', 'pip', 'install', '-r', release / 'backend/requirements.txt'],
                env=clean_env(cfg), log=log)
            (venv / '.ready').touch()
        if not (release / 'frontend/build-node/index.js').is_file():
            raise RuntimeError('Node build missing')
    result = {'sha': sha, 'path': str(release), 'python': str(venv / 'bin/python')}
    atomic_json(marker, result)
    return result


def service_name(role, slot=None):
    return 'app.rehear.' + role + ('.' + slot if slot else '')


def stop(label):
    target = f'gui/{os.getuid()}/{label}'
    if run(['/bin/launchctl', 'print', target], check=False).returncode == 0:
        run(['/bin/launchctl', 'bootout', target])


def wait_closed(port, timeout=90):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(('127.0.0.1', port), timeout=.3):
                pass
        except OSError:
            return
        time.sleep(.2)
    raise RuntimeError('Previous backend has not drained')


def start_plist(path):
    run(['/bin/launchctl', 'bootstrap', f'gui/{os.getuid()}', path])


def install_plist(cfg, label, arguments, *, interval=None):
    home = Path(cfg['home'])
    path = Path.home() / 'Library/LaunchAgents' / (label + '.plist')
    data = {'Label': label, 'ProgramArguments': [str(a) for a in arguments],
            'WorkingDirectory': str(home), 'RunAtLoad': True,
            'StandardOutPath': str(home / 'logs' / (label + '.log')),
            'StandardErrorPath': str(home / 'logs' / (label + '.error.log')),
            'EnvironmentVariables': {'PATH': clean_env(cfg)['PATH']}}
    if interval:
        data['StartInterval'] = interval
    else:
        data.update(KeepAlive=True, ThrottleInterval=5)
    stop(label)
    path.write_bytes(plistlib.dumps(data)); path.chmod(0o600)
    start_plist(path)


def caddy_text(cfg, active, maintenance=False):
    sha = active['sha'] if active else 'legacy'
    backend = active['backend_port'] if active else 8000
    frontend = active['frontend_port'] if active else 5173
    paths = ' '.join(p + ' ' + p + '/*' for p in PREFIXES)
    api = ('respond "Rehear is updating; retry shortly" 503' if maintenance
           else f'reverse_proxy 127.0.0.1:{backend} {{\n            header_up X-Forwarded-Proto https\n            header_up X-Sushi-Proto https\n        }}')
    return f'''{{
    admin 127.0.0.1:2018
    auto_https off
}}
http://:5180 {{
    bind 127.0.0.1
    header X-Rehear-Revision "{sha}"
    @private path /_ops /_ops/*
    handle @private {{
        respond "Not found" 404
    }}
    handle /_rehear/deployment {{
        header Content-Type application/json
        header Cache-Control no-store
        respond `{{"revision":"{sha}"}}` 200
    }}
    @odi path {paths}
    handle @odi {{
        {api}
    }}
    @auth path /auth /auth/* /api /api/* /pronunciation /pronunciation/*
    handle @auth {{
        reverse_proxy 127.0.0.1:8000 {{
            header_up X-Forwarded-Proto https
            header_up X-Sushi-Proto https
        }}
    }}
    @docs path /openapi.json /docs /docs/* /redoc
    handle @docs {{
        reverse_proxy 127.0.0.1:{backend}
    }}
    @rehear host rehear.kr rehear.chobab.app
    handle @rehear {{
        reverse_proxy 127.0.0.1:{frontend} {{
            header_up X-Forwarded-Proto https
            header_up X-Sushi-Proto https
        }}
    }}
    handle {{
        reverse_proxy 127.0.0.1:5173
    }}
}}
'''


def proxy(cfg, active, maintenance=False):
    home = Path(cfg['home'])
    new = home / 'Caddyfile.next'
    new.write_text(caddy_text(cfg, active, maintenance))
    run([cfg['caddy'], 'validate', '--config', new, '--adapter', 'caddyfile'])
    os.replace(new, home / 'Caddyfile')
    if run(['/bin/launchctl', 'print', f'gui/{os.getuid()}/app.rehear.ingress'], check=False).returncode:
        install_plist(cfg, 'app.rehear.ingress', [cfg['caddy'], 'run', '--config', home / 'Caddyfile', '--adapter', 'caddyfile'])
        wait_http('http://127.0.0.1:5180/_rehear/deployment', host='rehear.kr')
    else:
        run([cfg['caddy'], 'reload', '--address', '127.0.0.1:2018', '--config', home / 'Caddyfile', '--adapter', 'caddyfile'])


def data_snapshot(cfg, label, *, source=None):
    home = Path(cfg['home'])
    source = Path(source) if source else home / 'data'
    db_source = Path(cfg['legacy_backend']) / 'odi/db/odi.db' if source == Path(cfg['legacy_backend']) else source / 'db/odi.db'
    target = home / 'backups' / (time.strftime('%Y%m%d-%H%M%S') + '-' + str(time.time_ns())[-6:] + '-' + label)
    target.mkdir(parents=True, mode=0o700)
    with sqlite3.connect(f'file:{db_source}?mode=ro', uri=True) as src, sqlite3.connect(target / 'odi.db') as dst:
        src.backup(dst)
        if dst.execute('PRAGMA integrity_check').fetchone()[0] != 'ok' or dst.execute('PRAGMA foreign_key_check').fetchall():
            raise RuntimeError('Database integrity verification failed')
    media = source / 'storage/odi'
    if any(p.is_symlink() for p in media.rglob('*')):
        raise RuntimeError('Media contains symlinks; inspect before snapshot')
    shutil.copytree(media, target / 'storage/odi')
    def hashes(folder):
        return {str(p.relative_to(folder)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in folder.rglob('*') if p.is_file()}
    before, after = hashes(media), hashes(target / 'storage/odi')
    if before != after:
        raise RuntimeError('Media snapshot did not verify')
    atomic_json(target / 'verification.json', {'media_files': len(before), 'integrity': 'ok', 'foreign_key_violations': 0})
    return target


def read_state(home):
    path = Path(home) / 'state.json'
    return json.loads(path.read_text()) if path.is_file() else {'active': None, 'previous': None, 'rejected': []}


def check_idle(active):
    if active:
        _, body = request(f"http://127.0.0.1:{active['backend_port']}/_ops/health")
        h = json.loads(body)
        if h['active_sessions'] or h['active_jobs']:
            raise Deferred('Active Rehear presentation or analysis; keeping current release')


def launch_release(cfg, release, slot):
    home = Path(cfg['home'])
    active = {**release, 'slot': slot, 'backend_port': 8181 if slot == 'a' else 8182,
              'frontend_port': 5181 if slot == 'a' else 5182}
    atomic_json(home / f'slot-{slot}.json', active)
    for role in ('backend', 'frontend'):
        install_plist(cfg, service_name(role, slot), [cfg['base_python'], home / 'bin/runtime.py', role, slot, home / 'config.json'])
    _, body = wait_http(f"http://127.0.0.1:{active['backend_port']}/_ops/health", timeout=60)
    if json.loads(body)['revision'] != release['sha']:
        raise RuntimeError('Backend revision mismatch')
    wait_http(f"http://127.0.0.1:{active['frontend_port']}/", timeout=60)
    return active


def stop_release(active):
    if active:
        for role in ('backend', 'frontend'):
            stop(service_name(role, active['slot']))
        if active.get('backend_port'):
            wait_closed(active['backend_port'])


def smoke(cfg, active):
    _, body = request('http://127.0.0.1:5180/_rehear/deployment', host='rehear.kr')
    if json.loads(body)['revision'] != active['sha']:
        raise RuntimeError('Ingress revision mismatch')
    status, body = request('http://127.0.0.1:5180/', host='rehear.kr')
    if status != 200 or b'<html' not in body.lower():
        raise RuntimeError('Frontend smoke failed')
    status, body = request('http://127.0.0.1:5180/odi/db/health', host='rehear.kr')
    if status != 200 or json.loads(body).get('status') != 'ok':
        raise RuntimeError('ODI smoke failed')


def prune_generated_artifacts(cfg):
    """Bound generated release/snapshot storage; preserve active, previous and initial data."""
    home = Path(cfg['home']); state = read_state(home)
    protected = {r['sha'] for r in (state.get('active'), state.get('previous')) if r}
    releases = sorted((p for p in (home / 'releases').iterdir()
                       if p.is_dir() and not p.is_symlink() and SHA.fullmatch(p.name)),
                      key=lambda p: p.stat().st_mtime, reverse=True)
    # Preserve the newest candidate as well as both rollback slots.
    keep = protected | {p.name for p in releases[:1]}
    for release in releases:
        if release.name not in keep:
            shutil.rmtree(release)
    backups = sorted((p for p in (home / 'backups').iterdir()
                      if p.is_dir() and not p.is_symlink()
                      and re.fullmatch(r'[0-9]{8}-[0-9]{6}-[0-9]{6}-[0-9a-f]{10}', p.name)),
                     key=lambda p: p.stat().st_mtime, reverse=True)
    for backup in backups[3:]:
        shutil.rmtree(backup)


def activate(cfg, release, *, rollback=False):
    home = Path(cfg['home']); state = read_state(home); old = state['active']
    check_idle(old)
    proxy(cfg, old, maintenance=True)
    try:
        time.sleep(.25)
        check_idle(old)
    except BaseException:
        proxy(cfg, old); raise
    new = None
    slot = 'b' if old and old['slot'] == 'a' else 'a'
    try:
        stop_release(old)
        data_snapshot(cfg, release['sha'][:10])
        new = launch_release(cfg, release, slot)
        proxy(cfg, new)
        smoke(cfg, new)
    except BaseException:
        stop_release(new or {'slot': slot, 'backend_port': 8181 if slot == 'a' else 8182})
        if old:
            restored = launch_release(cfg, old, old['slot'])
            proxy(cfg, restored)
        raise
    rejected = set(state.get('rejected', []))
    if rollback and old:
        rejected.add(old['sha'])
    atomic_json(home / 'state.json', {'active': new, 'previous': old, 'rejected': sorted(rejected)})
    for name in ('deploy.py', 'runtime.py'):
        source = Path(new['path']) / 'ops' / name
        if source.is_file():
            target = home / 'bin' / name
            shutil.copy2(source, target.with_suffix('.next'))
            os.replace(target.with_suffix('.next'), target)
    atomic_json(home / 'last-result.json', {'status': 'deployed', 'sha': new['sha'], 'at': time.time()})
    try:
        prune_generated_artifacts(cfg)
    except OSError:
        # Storage cleanup must not turn a healthy release into a failed deployment.
        print('Generated storage cleanup deferred', flush=True)
    print('DEPLOYED', new['sha'], flush=True)
    return new


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True)
    parser.add_argument('action', choices=['prepare', 'deploy', 'poll', 'rollback', 'status'])
    parser.add_argument('--retry', action='store_true')
    args = parser.parse_args(); cfg = json.loads(Path(args.config).read_text()); home = Path(cfg['home'])
    with (home / 'deploy.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        state = read_state(home)
        try:
            if args.action == 'status':
                print(json.dumps(state)); return
            if args.action == 'rollback':
                if not state['previous']:
                    raise RuntimeError('No previous independent release')
                activate(cfg, state['previous'], rollback=True); return
            if args.action == 'poll' and not (home / 'enabled').is_file():
                return
            sha = checked_main(cfg)
            if args.action == 'prepare':
                print('PREPARED', prepare(cfg, sha)['sha']); return
            if state['active'] and state['active']['sha'] == sha:
                return
            if sha in state.get('rejected', []) and not args.retry:
                raise Deferred('Main revision previously rolled back; waiting for a new main')
            if not state['active']:
                raise Deferred('Initial data cutover must be completed by the operator first')
            release = prepare(cfg, sha)
            if checked_main(cfg) != sha:
                raise Deferred('Main changed while building; next poll will build the new revision')
            activate(cfg, release)
        except Deferred as exc:
            atomic_json(home / 'last-result.json', {'status': 'deferred', 'reason': str(exc), 'at': time.time()})
            print('DEFERRED', str(exc), flush=True)
        except BaseException as exc:
            atomic_json(home / 'last-result.json', {'status': 'failed', 'error_type': type(exc).__name__, 'at': time.time()})
            raise


if __name__ == '__main__':
    main()
