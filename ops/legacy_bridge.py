"""Keep old ODI callers on the selected writer after extraction.

Installed in the existing Sushi backend with an explicit local upstream. It forwards
HTTP bytes and cookies normally; it never exports them to logs or configuration.
"""
import httpx
from starlette.responses import StreamingResponse
from starlette.background import BackgroundTask

PREFIXES = ('/odi/db', '/odi/files', '/odi/coaching', '/odi/xreal_rehear')
HOP = {'host', 'connection', 'keep-alive', 'transfer-encoding', 'upgrade',
       'proxy-authenticate', 'proxy-authorization', 'te', 'trailer'}


def install(app, upstream):
    if upstream != 'http://127.0.0.1:5180':
        raise RuntimeError('Rehear bridge must use the configured loopback ingress')

    @app.middleware('http')
    async def route_odi(request, call_next):
        path = request.url.path
        if not any(path == p or path.startswith(p + '/') for p in PREFIXES):
            return await call_next(request)
        client = httpx.AsyncClient(timeout=httpx.Timeout(30, read=None))
        headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP}
        headers['host'] = request.headers.get('host', 'rehear.kr')
        headers['x-rehear-legacy-bridge'] = '1'
        try:
            req = client.build_request(request.method, upstream + path,
                                       params=request.query_params.multi_items(), headers=headers,
                                       content=request.stream())
            response = await client.send(req, stream=True)
        except BaseException:
            await client.aclose()
            raise

        async def close():
            await response.aclose()
            await client.aclose()

        result = StreamingResponse(response.aiter_raw(), status_code=response.status_code,
                                   background=BackgroundTask(close))
        result.raw_headers = [(k, v) for k, v in response.headers.raw if k.decode().lower() not in HOP]
        return result
