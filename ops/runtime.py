"""Launch one immutable Rehear release; secrets never enter build processes."""
import json, os, sys
from pathlib import Path


def clean_env():
    return {k: os.environ[k] for k in ('HOME', 'PATH', 'LANG', 'TMPDIR') if k in os.environ}


def main():
    role, slot, config_path = sys.argv[1:]
    cfg = json.loads(Path(config_path).read_text())
    home = Path(cfg['home'])
    release = json.loads((home / f'slot-{slot}.json').read_text())
    root = Path(release['path'])
    env = clean_env()
    if role == 'backend':
        env.update(json.loads((home / 'backend.json').read_text()))
        env.update(REHEAR_ODI_DB_PATH=str(home / 'data/db/odi.db'),
                   REHEAR_DATA_ROOT=str(home / 'data'), REHEAR_REVISION=release['sha'])
        os.chdir(root / 'backend')
        python = release['python']
        os.execve(python, [python, '-m', 'uvicorn', 'main:app', '--host', '127.0.0.1',
                          '--port', str(release['backend_port']), '--workers', '1', '--no-access-log'], env)
    elif role == 'frontend':
        env.update(json.loads((home / 'frontend.json').read_text()))
        env.update(SUSHI_HOST='127.0.0.1', SUSHI_PORT=str(release['frontend_port']),
                   SUSHI_ORIGIN='https://rehear.kr')
        os.chdir(root / 'frontend')
        os.execve(cfg['node'], [cfg['node'], 'build-node/index.js'], env)
    else:
        raise SystemExit('unknown role')


if __name__ == '__main__':
    main()
