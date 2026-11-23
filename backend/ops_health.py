"""Loopback deployment health; public ingress explicitly denies this namespace."""
import os
from fastapi import HTTPException, Request
from odi.db import odidb


def install(app):
    @app.get('/_ops/health', include_in_schema=False)
    async def health(request: Request):
        if not request.client or request.client.host not in {'127.0.0.1', '::1'}:
            raise HTTPException(404)
        from odi.EVC.session_store import session_store
        async with session_store._store_lock:
            active = sum(1 for r in session_store._sessions.values()
                         if r.presentation_status != 'finished'
                         and session_store._clock() - r.last_access_monotonic < 600)
        with odidb.get_conn(odidb.DB_PATH) as db:
            jobs = db.execute("SELECT count(*) FROM presentation_report_jobs WHERE status IN ('queued','generating')").fetchone()[0]
            jobs += db.execute("SELECT count(*) FROM practice_attempts WHERE state IN ('queued','analyzing')").fetchone()[0]
        return {'status': 'ok', 'revision': os.getenv('REHEAR_REVISION', 'development'),
                'active_sessions': active, 'active_jobs': jobs}
