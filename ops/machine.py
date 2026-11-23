"""One-time operator setup/cutover for the existing Mac Sushi installation."""
import argparse, ast, json, os, re, shutil, sqlite3, subprocess, sys, time
from pathlib import Path
import deploy

ROOT = Path(__file__).resolve().parents[1]


def environment_keys():
    names = {'JWT_SECRET_KEY', 'ALGORITHM', 'GOOGLE_APPLICATION_CREDENTIALS'}
    for folder in ('odi', 'rehear_ai'):
        for p in (ROOT / 'backend' / folder).rglob('*.py'):
            try: tree = ast.parse(p.read_text())
            except (SyntaxError, UnicodeError): continue
            for n in ast.walk(tree):
                if isinstance(n, ast.Call) and n.args and isinstance(n.args[0], ast.Constant):
                    v = n.args[0].value
                    if isinstance(v, str) and re.fullmatch('[A-Z][A-Z0-9_]+', v): names.add(v)
    return names


def set_legacy_env(cfg, external):
    from dotenv import set_key
    path = Path(cfg['legacy_backend']) / '.env'
    set_key(path, 'REHEAR_EXTERNAL_BACKEND', 'http://127.0.0.1:5180' if external else '')
    set_key(path, 'REHEAR_DATA_ROOT', str(Path(cfg['home']) / 'data'))


def patch_legacy(cfg):
    home = Path(cfg['home']); root = Path(cfg['legacy_backend'])
    main = root / 'main.py'; source = main.read_text()
    if '# Rehear external writer bridge' not in source:
        begin = source.index('# Ensure additive report tables')
        end = source.index('\napp.include_router(auth_router)', begin)
        old = source[begin:end]
        new = ('# Rehear external writer bridge\n'
               'import os\n'
               'if os.getenv("REHEAR_EXTERNAL_BACKEND"):\n'
               '    from rehear_legacy_bridge import install as install_rehear_bridge\n'
               '    install_rehear_bridge(app, os.environ["REHEAR_EXTERNAL_BACKEND"])\n'
               'else:\n' + ''.join('    ' + line + '\n' for line in old.splitlines()))
        updated = source[:begin] + new + source[end:]
        compile(updated, str(main), 'exec')
        shutil.copy2(main, home / 'legacy-main.before.py')
        main.write_text(updated)
    service = root / 'odi/files/service.py'; source = service.read_text()
    old = 'BASE_DIR = Path(__file__).resolve().parents[2]'
    if old in source:
        updated = source.replace(old, 'BASE_DIR = Path(os.environ.get("REHEAR_DATA_ROOT", str(Path(__file__).resolve().parents[2]))).resolve()')
        if '\nimport os\n' not in updated:
            updated = updated.replace('\nimport json\n', '\nimport json\nimport os\n')
        compile(updated, str(service), 'exec')
        shutil.copy2(service, home / 'legacy-storage.before.py')
        service.write_text(updated)
    elif 'REHEAR_DATA_ROOT' not in source:
        raise RuntimeError('Legacy media path implementation changed; review before patching')
    shutil.copy2(ROOT / 'ops/legacy_bridge.py', root / 'rehear_legacy_bridge.py')


def tunnel_routes(source):
    lines = source.splitlines(keepends=True)
    hostname, odi, changed = None, False, 0
    for i, line in enumerate(lines):
        if line.startswith('  - '):
            match = re.match(r'  - hostname: ([^\s]+)', line)
            hostname, odi = (match.group(1) if match else None), False
        if hostname and line.lstrip().startswith('path:') and '/odi/' in line:
            odi = True
            if 'coaching' not in line:
                lines[i] = line.replace('(db|', '(db|coaching|')
        if hostname and line.lstrip().startswith('service:') and (hostname in {'rehear.kr', 'rehear.chobab.app'} or odi):
            lines[i] = '    service: http://127.0.0.1:5180\n'
            changed += 1
    if changed < 8:
        raise RuntimeError('Unexpected tunnel layout; not changing it')
    return ''.join(lines)


def configure(args):
    from dotenv import dotenv_values
    home = Path(args.home).expanduser().resolve()
    home.mkdir(parents=True, exist_ok=True, mode=0o700); home.chmod(0o700)
    if (home / 'config.json').exists(): raise RuntimeError('Already configured; existing configuration preserved')
    cfg = {'home': str(home), 'repo': '4githu/Rehear', 'base_python': sys._base_executable,
           'legacy_backend': str(Path(args.legacy_backend).resolve()),
           'legacy_frontend': str(Path(args.legacy_frontend).resolve()),
           'node': '/opt/homebrew/opt/node@22/bin/node', 'npm': '/opt/homebrew/opt/node@22/bin/npm',
           'gh': shutil.which('gh'), 'caddy': shutil.which('caddy'), 'cloudflared': shutil.which('cloudflared')}
    if not all(cfg[x] for x in ('gh', 'caddy', 'cloudflared')): raise RuntimeError('Required CLIs unavailable')
    for folder in ('bin', 'logs', 'backups', 'releases', 'environments'):
        (home / folder).mkdir(mode=0o700)
    backend = dotenv_values(Path(cfg['legacy_backend']) / '.env')
    selected = {k: v for k, v in backend.items() if k in environment_keys() and isinstance(v, str)}
    if not selected.get('JWT_SECRET_KEY') or not selected.get('ALGORITHM'):
        raise RuntimeError('Existing signing configuration missing')
    frontend = dotenv_values(Path(cfg['legacy_frontend']) / '.env')
    selected_front = {k: v for k, v in frontend.items() if isinstance(v, str)
                      and (k.startswith(('PUBLIC_', 'VITE_')) or k in {'BETTER_AUTH_SECRET', 'BETTER_AUTH_URL'})}
    selected_front.update(VITE_SUSHIFASTURL='', VITE_API_BASE_URL='')
    database = Path(frontend.get('DATABASE_URL', 'local.db'))
    if not database.is_absolute(): database = Path(cfg['legacy_frontend']) / database
    if not database.is_file(): raise RuntimeError('Shared frontend auth DB unavailable')
    selected_front['DATABASE_URL'] = str(database.resolve())
    deploy.atomic_json(home / 'backend.json', selected)
    deploy.atomic_json(home / 'frontend.json', selected_front)
    deploy.atomic_json(home / 'config.json', cfg)
    for name in ('deploy.py', 'runtime.py'):
        shutil.copy2(ROOT / 'ops' / name, home / 'bin' / name)
    print('Configured private runtime; no production service changed')


def stage(cfg):
    home = Path(cfg['home'])
    sha = deploy.checked_main(cfg)
    release = deploy.prepare(cfg, sha)
    if not (Path(release['path']) / 'backend/ops_health.py').is_file():
        raise RuntimeError('Main does not yet include deployment health')
    deploy.proxy(cfg, None)
    tunnel = Path.home() / '.cloudflared/config.yml'
    old = tunnel.read_text(); new = tunnel_routes(old)
    candidate = home / 'tunnel.candidate.yml'; candidate.write_text(new); candidate.chmod(0o600)
    deploy.run([cfg['cloudflared'], 'tunnel', '--config', candidate, 'ingress', 'validate'])
    if old != new:
        shutil.copy2(tunnel, home / 'tunnel.before.yml')
        if tunnel.read_text() != old: raise RuntimeError('Tunnel changed during setup')
        tunnel.write_text(new)
        deploy.run(['/bin/launchctl', 'kickstart', '-k', f'gui/{os.getuid()}/app.sushisite.tunnel'])
    deploy.wait_http('http://127.0.0.1:5180/', host='rehear.kr')
    patch_legacy(cfg)
    print('Staged ingress still uses legacy services; prepared', sha)


def legacy_idle(cfg):
    path = Path(cfg['legacy_backend']) / 'odi/db/odi.db'
    with sqlite3.connect(f'file:{path}?mode=ro', uri=True) as db:
        jobs = db.execute("SELECT count(*) FROM presentation_report_jobs WHERE status IN ('queued','generating')").fetchone()[0]
        jobs += db.execute("SELECT count(*) FROM practice_attempts WHERE state IN ('queued','analyzing')").fetchone()[0]
        recent = db.execute("SELECT count(*) FROM sessions WHERE state='running' AND julianday(COALESCE(updated_at,created_at)) > julianday('now','-10 minutes')").fetchone()[0]
        recent += db.execute("SELECT count(*) FROM presentation_segments WHERE julianday(updated_at) > julianday('now','-10 minutes')").fetchone()[0]
    if jobs or recent: raise deploy.Deferred('Recent legacy practice or analysis exists; not stopping it')


def restart_legacy(cfg):
    path = Path.home() / 'Library/LaunchAgents/app.sushisite.backend.plist'
    deploy.start_plist(path)
    deploy.wait_http('http://127.0.0.1:8000/api/healthz', timeout=60)


def initialize_data(cfg):
    """Move paths only after a verified copy exists; undo every partial path move."""
    home = Path(cfg['home'])
    if (home / 'data-initialized').exists():
        return
    snapshot = deploy.data_snapshot(cfg, 'initial', source=cfg['legacy_backend'])
    data = home / 'data'
    if data.exists():
        raise RuntimeError('Uninitialized data directory exists; inspect before retry')
    (data / 'db').mkdir(parents=True)
    shutil.copy2(snapshot / 'odi.db', data / 'db/odi.db')
    (data / 'storage').mkdir()
    shutil.copytree(snapshot / 'storage/odi', data / 'storage/odi')
    old_db = Path(cfg['legacy_backend']) / 'odi/db/odi.db'
    old_media = Path(cfg['legacy_backend']) / 'storage/odi'
    raw = snapshot / 'original-db'; raw.mkdir()
    moved = []
    links = []
    try:
        for suffix in ('', '-wal', '-shm'):
            original = Path(str(old_db) + suffix)
            if original.exists():
                backup = raw / original.name
                original.rename(backup)
                moved.append((original, backup))
        old_db.symlink_to(data / 'db/odi.db'); links.append(old_db)
        backup_media = snapshot / 'original-media'
        old_media.rename(backup_media); moved.append((old_media, backup_media))
        old_media.symlink_to(data / 'storage/odi', target_is_directory=True); links.append(old_media)
        (home / 'data-initialized').touch()
    except BaseException:
        for link in reversed(links):
            link.unlink()
        for original, backup in reversed(moved):
            backup.rename(original)
        # The verified snapshot remains; only our unused duplicate is removed.
        shutil.rmtree(data)
        raise


def cutover(cfg):
    home = Path(cfg['home']); state = deploy.read_state(home)
    if state['active']: raise RuntimeError('Already independently deployed; use deploy/poll')
    sha = deploy.checked_main(cfg); release = deploy.prepare(cfg, sha)
    legacy_idle(cfg)
    deploy.proxy(cfg, None, maintenance=True)
    try:
        legacy_idle(cfg)
    except BaseException:
        deploy.proxy(cfg, None)
        raise
    deploy.stop('app.sushisite.backend')
    deploy.wait_closed(8000)
    new = None
    try:
        initialize_data(cfg)
        set_legacy_env(cfg, True)
        new = deploy.launch_release(cfg, release, 'a')
        restart_legacy(cfg)
        deploy.proxy(cfg, new)
        deploy.smoke(cfg, new)
        deploy.atomic_json(home / 'state.json', {'active': new, 'previous': None, 'rejected': []})
        deploy.atomic_json(home / 'last-result.json', {'status': 'deployed', 'sha': sha, 'at': time.time()})
        deploy.install_plist(cfg, 'app.rehear.autodeploy', [cfg['base_python'], home / 'bin/deploy.py',
                             '--config', home / 'config.json', 'poll'], interval=60)
        (home / 'enabled').touch()
        print('CUTOVER', sha)
    except BaseException:
        deploy.stop_release(new or {'slot': 'a', 'backend_port': 8181})
        if (home / 'data-initialized').exists(): set_legacy_env(cfg, False)
        deploy.stop('app.sushisite.backend')
        restart_legacy(cfg)
        deploy.proxy(cfg, None)
        raise


def main():
    p = argparse.ArgumentParser(); p.add_argument('action', choices=['configure', 'stage', 'cutover'])
    p.add_argument('--home', required=True); p.add_argument('--legacy-backend'); p.add_argument('--legacy-frontend')
    args = p.parse_args()
    if args.action == 'configure': configure(args); return
    cfg = json.loads((Path(args.home).expanduser() / 'config.json').read_text())
    home = Path(cfg['home'])
    with (home / 'deploy.lock').open('a') as lock:
        import fcntl
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        {'stage': stage, 'cutover': cutover}[args.action](cfg)


if __name__ == '__main__': main()
