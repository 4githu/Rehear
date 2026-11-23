"""Startup uses only a synthetic SQLite database, never operational data."""
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from main import app
from odi.db import odidb


def test_startup_refuses_to_create_missing_database(tmp_path, monkeypatch):
    path = tmp_path / 'missing.db'
    monkeypatch.setattr(odidb, 'DB_PATH', path)
    with pytest.raises(RuntimeError, match='ODI database is missing'):
        with TestClient(app):
            pass
    assert not path.exists()


def test_real_uvicorn_startup_on_isolated_candidate_copy(tmp_path):
    root = Path(__file__).resolve().parents[1]
    candidate = tmp_path / 'candidate'
    candidate.mkdir()
    for module in ('main.py', 'ops_health.py'):
        shutil.copy2(root / module, candidate / module)
    def ignore_nonsource(directory, names):
        return {name for name in names if name in {'__pycache__', 'tests', 'evc_logs', 'media', 'uploads'} or (not (Path(directory) / name).is_dir() and not name.endswith(('.py', '.sql')))}

    for name in ('odi', 'auth', 'rehear_ai'):
        shutil.copytree(root / name, candidate / name, ignore=ignore_nonsource)
    db = candidate / 'odi' / 'db' / 'odi.db'
    odidb.init_db(schema_path=candidate / 'odi' / 'db' / 'schema.sql', db_path=db)
    with odidb.get_conn(db) as conn:
        conn.execute("INSERT INTO users (user_id, config) VALUES ('synthetic', '{}')")
        conn.execute("INSERT INTO practice_attempts (attempt_id,user_id,metric_id,state,duration_seconds,created_at) VALUES ('interrupted','synthetic','synthetic','analyzing',1,datetime('now'))")
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    env = {'PATH': os.environ['PATH'], 'HOME': str(tmp_path), 'TMPDIR': str(tmp_path), 'PYTHONDONTWRITEBYTECODE': '1'}
    proc = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'main:app', '--host', '127.0.0.1', '--port', str(port)], cwd=candidate, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                pytest.fail(f'isolated Uvicorn exited with code {proc.returncode}: {proc.stdout.read()}')
            try:
                if httpx.get(f'http://127.0.0.1:{port}/openapi.json', timeout=1).status_code == 200:
                    break
            except httpx.TransportError:
                time.sleep(0.1)
        else:
            pytest.fail('isolated Uvicorn did not become ready')
        with sqlite3.connect(db) as conn:
            assert conn.execute("SELECT state FROM practice_attempts WHERE attempt_id='interrupted'").fetchone()[0] == 'failed'
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)


def test_startup_initializes_and_recovers_interrupted_work(tmp_path, monkeypatch):
    path = tmp_path / 'odi.db'
    odidb.init_db(schema_path=odidb.BASE_DIR / 'schema.sql', db_path=path)
    with odidb.get_conn(path) as conn:
        conn.execute("INSERT INTO users (user_id, config) VALUES ('synthetic', '{}')")
        for state in ('queued', 'analyzing', 'completed', 'failed'):
            conn.execute("INSERT INTO practice_attempts (attempt_id,user_id,metric_id,state,duration_seconds,created_at) VALUES (?,?,?, ?,1,datetime('now'))", (state, 'synthetic', 'synthetic', state))
        for job, started in (('stale', '2000-01-01 00:00:00'), ('fresh', '2999-01-01 00:00:00')):
            conn.execute("INSERT INTO presentation_report_jobs (job_id,evc_session_id,request_id,status,started_at) VALUES (?,?,?,'generating',?)", (job, 'synthetic', job, started))
    monkeypatch.setattr(odidb, 'DB_PATH', path)
    with TestClient(app):
        with sqlite3.connect(path) as conn:
            attempts = dict(conn.execute('SELECT attempt_id,state FROM practice_attempts'))
            jobs = dict(conn.execute('SELECT job_id,status FROM presentation_report_jobs'))
            assert attempts == {'queued': 'failed', 'analyzing': 'failed', 'completed': 'completed', 'failed': 'failed'}
            assert jobs == {'stale': 'queued', 'fresh': 'generating'}
    with TestClient(app):
        with sqlite3.connect(path) as conn:
            assert dict(conn.execute('SELECT job_id,status FROM presentation_report_jobs')) == jobs
