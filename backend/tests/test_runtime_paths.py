import os
import subprocess
import sys
from pathlib import Path


def test_runtime_data_paths_and_legacy_media_paths(tmp_path):
    root = Path(__file__).resolve().parents[1]
    runtime = tmp_path / 'runtime'
    media = runtime / 'storage' / 'odi' / 'users' / 'synthetic' / 'file.txt'
    media.parent.mkdir(parents=True)
    media.write_text('synthetic')
    db = runtime / 'db' / 'odi.db'
    env = dict(os.environ, REHEAR_ODI_DB_PATH=str(db), REHEAR_DATA_ROOT=str(runtime))
    code = '''
from pathlib import Path
from fastapi import HTTPException
from odi.db import odidb
from odi.files import service
import os
assert odidb.DB_PATH == Path(os.environ['REHEAR_ODI_DB_PATH']).resolve()
p = service.path_from_storage_path('storage/odi/users/synthetic/file.txt')
assert p.read_text() == 'synthetic'
assert service.as_storage_path(p) == 'storage/odi/users/synthetic/file.txt'
try:
    service.path_from_storage_path('storage/odi/../../outside')
except HTTPException:
    pass
else:
    raise AssertionError('path traversal accepted')
'''
    subprocess.run([sys.executable, '-c', code], cwd=root, env=env, check=True)
