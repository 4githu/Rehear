import sqlite3
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from main import app
from odi.db import odidb as db
from odi.db import router as db_router
from odi.db import template_service
from odi.files import router as files_router
from odi.files import service


def test_external_symlink_upload_template_db_and_read(tmp_path, monkeypatch):
    app_root = tmp_path / 'app'
    external = tmp_path / 'runtime' / 'odi'
    external.mkdir(parents=True)
    (app_root / 'storage').mkdir(parents=True)
    (app_root / 'storage' / 'odi').symlink_to(external, target_is_directory=True)
    monkeypatch.setattr(service, 'BASE_DIR', app_root)
    monkeypatch.setattr(service, 'STORAGE_ROOT', external)
    monkeypatch.setattr(template_service, 'STORAGE_ROOT', external)
    monkeypatch.setattr(files_router, 'get_user_id_from_jwt', lambda request: 'synthetic-user')
    monkeypatch.setattr(db_router, 'get_user_id_from_jwt', lambda request: 'synthetic-user')
    dbpath = tmp_path / 'synthetic.db'
    db.init_db(db.BASE_DIR / 'schema.sql', dbpath)
    monkeypatch.setattr(db, 'DB_PATH', dbpath)
    original_get_conn = db.get_conn
    monkeypatch.setattr(db, 'get_conn', lambda *args, **kwargs: original_get_conn(dbpath))
    monkeypatch.setattr(db, 'ensure_report_schema', lambda *args, **kwargs: None)
    db.create_user('synthetic-user', {})
    existing = external / 'users' / 'synthetic-user' / 'old.txt'
    existing.parent.mkdir(parents=True)
    existing.write_bytes(b'existing')
    with TestClient(app) as client:
        response = client.get('/odi/files/read', params={'path': 'storage/odi/users/synthetic-user/old.txt'})
        assert response.status_code == 200 and response.content == b'existing'
        upload = client.post('/odi/files/upload-temp', data={'role': 'script'}, files={'file': ('draft.txt', b'synthetic upload', 'text/plain')})
        assert upload.status_code == 200
        ref = upload.json()['file']
        assert ref['storage_path'].startswith('storage/odi/')
        response = client.post('/odi/db/templates', json={'owner_id': 'synthetic-user', 'template_id': 'synthetic-template', 'template': {'type': 'presentation', 'environment': {'title': 'Synthetic'}, 'files': {'script': ref}, 'audience': {}}})
        assert response.status_code == 200, response.text
        with sqlite3.connect(dbpath) as conn:
            import json
            stored = json.loads(conn.execute('SELECT template FROM templates WHERE template_id=?', ('synthetic-template',)).fetchone()[0])
        committed = stored['files']['script']['storage_path']
        assert committed.startswith('storage/odi/')
        result = client.get('/odi/files/read', params={'path': committed})
        assert result.status_code == 200 and result.content == b'synthetic upload'
        assert service.path_from_storage_path(committed).is_relative_to(external)


def test_external_storage_rejects_traversal_and_symlink_escape(tmp_path, monkeypatch):
    root = tmp_path / 'runtime'
    root.mkdir()
    outside = tmp_path / 'outside.txt'
    outside.write_text('no')
    (root / 'escape').symlink_to(outside)
    monkeypatch.setattr(service, 'STORAGE_ROOT', root)
    monkeypatch.setattr(service, 'BASE_DIR', tmp_path / 'app')
    for value in ['storage/odi/../../outside.txt', 'storage/odi/escape', '/etc/passwd', 'other/path']:
        with pytest.raises(HTTPException):
            service.path_from_storage_path(value)
    with pytest.raises(ValueError):
        service.as_storage_path(outside)
