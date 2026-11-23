import http from 'node:http';
import https from 'node:https';

/** @typedef {import('node:http').IncomingMessage} IncomingMessage */
/** @typedef {import('node:http').IncomingHttpHeaders} IncomingHttpHeaders */
/** @typedef {import('node:http').OutgoingHttpHeaders} OutgoingHttpHeaders */
/** @typedef {import('node:http').ClientRequest} ClientRequest */
/** @typedef {import('node:http').RequestListener} RequestListener */
/** @typedef {import('node:http').ServerResponse} ServerResponse */

/** @type {string[]} */
export const API_PATHS = [
	'/auth',
	'/api',
	'/odi/coaching',
	'/odi/db',
	'/odi/files',
	'/odi/xreal_rehear',
	'/pronunciation'
];
const HOP_BY_HOP_HEADERS = new Set([
	'connection',
	'keep-alive',
	'proxy-authenticate',
	'proxy-authorization',
	'te',
	'trailer',
	'transfer-encoding',
	'upgrade'
]);

/** @param {string} name */
function isUntrustedForwardedHeader(name) {
	return (
		name === 'forwarded' ||
		name === 'x-real-ip' ||
		name.startsWith('x-forwarded-') ||
		name === 'x-sushi-proto'
	);
}

export function isApiRequestPath(/** @type {string} */ pathname) {
	return API_PATHS.some((prefix) => pathname === prefix || pathname.startsWith(`${prefix}/`));
}

export function createViteProxyPattern(/** @type {string} */ prefix) {
	const escapedPrefix = prefix.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
	return `^${escapedPrefix}(?:/|$)`;
}

/** @param {string | undefined} value @returns {string} */
export function requirePublicOrigin(value) {
	const origin = parseOrigin(value, 'ORIGIN');
	if (!origin) throw new TypeError('ORIGIN is required for API proxy forwarding');
	return origin.origin;
}

/** @param {string | undefined} value @param {string} label @returns {URL | null} */
function parseOrigin(value, label) {
	if (!value) return null;
	let origin;
	try {
		origin = new URL(value);
	} catch {
		throw new TypeError(`${label} must be an absolute HTTP(S) origin`);
	}
	if (
		!['http:', 'https:'].includes(origin.protocol) ||
		origin.username ||
		origin.password ||
		origin.pathname !== '/' ||
		origin.search ||
		origin.hash
	) {
		throw new TypeError(`${label} must be an absolute HTTP(S) origin without credentials or path`);
	}
	return origin;
}

/** @param {ClientRequest} proxyReq @param {string} publicOrigin */
export function sanitizeProxyRequestHeaders(proxyReq, publicOrigin) {
	const origin = parseOrigin(publicOrigin, 'ORIGIN');
	if (!origin) throw new TypeError('ORIGIN is required for API proxy forwarding');

	for (const name of Object.keys(proxyReq.getHeaders())) {
		if (isUntrustedForwardedHeader(name)) proxyReq.removeHeader(name);
	}
	const protocol = origin.protocol.slice(0, -1);
	proxyReq.setHeader('x-forwarded-host', origin.host);
	proxyReq.setHeader('x-forwarded-proto', protocol);
	proxyReq.setHeader('x-sushi-proto', protocol);
}

/** @param {string} value @returns {URL} */
function parseUpstream(value) {
	const upstream = parseOrigin(value, 'API_UPSTREAM_URL');
	if (!upstream) throw new TypeError('API_UPSTREAM_URL is required');
	return upstream;
}

/**
 * @param {IncomingHttpHeaders} incoming
 * @param {URL} upstream
 * @param {URL} publicOrigin
 * @returns {OutgoingHttpHeaders}
 */
function safeHeaders(incoming, upstream, publicOrigin) {
	const headers = { ...incoming };
	for (const name of Object.keys(headers)) {
		if (HOP_BY_HOP_HEADERS.has(name) || isUntrustedForwardedHeader(name)) {
			delete headers[name];
		}
	}
	const protocol = publicOrigin.protocol === 'https:' ? 'https' : 'http';
	headers.host = upstream.host;
	headers['accept-encoding'] = 'identity';
	headers['x-forwarded-host'] = publicOrigin.host;
	headers['x-forwarded-proto'] = protocol;
	headers['x-sushi-proto'] = protocol;
	return headers;
}

/** @param {ServerResponse} res @param {number} status @param {unknown} body */
function sendJson(res, status, body) {
	res.writeHead(status, {
		'content-type': 'application/json; charset=utf-8',
		'cache-control': 'no-store'
	});
	res.end(JSON.stringify(body));
}

/** @param {URL} upstream @param {number} timeoutMs @returns {Promise<boolean>} */
function requestReady(upstream, timeoutMs) {
	const healthUrl = new URL('/health/ready', upstream);
	const transport = healthUrl.protocol === 'https:' ? https : http;
	return new Promise((resolve) => {
		let settled = false;
		const finish = (/** @type {boolean} */ ready) => {
			if (settled) return;
			settled = true;
			resolve(ready);
		};
		const request = transport.get(
			healthUrl,
			{ headers: { accept: 'application/json' } },
			(response) => {
				response.resume();
				const statusCode = response.statusCode ?? 0;
				response.on('end', () => finish(statusCode >= 200 && statusCode < 300));
			}
		);
		request.setTimeout(timeoutMs, () => request.destroy(new Error('health timeout')));
		request.on('error', () => finish(false));
	});
}

/**
 * @param {{ handler?: RequestListener, apiUpstreamUrl?: string, publicOrigin?: string, healthTimeoutMs?: number }} options
 */
export function createProxyServer({
	handler,
	apiUpstreamUrl = process.env.API_UPSTREAM_URL ||
		process.env.PERSONAL_API_PROXY ||
		'http://127.0.0.1:8000',
	publicOrigin = process.env.ORIGIN || '',
	healthTimeoutMs = 2000
} = {}) {
	if (typeof handler !== 'function') throw new TypeError('SvelteKit handler is required');
	const upstream = parseUpstream(apiUpstreamUrl);
	const origin = parseOrigin(publicOrigin, 'ORIGIN');
	if (!origin) throw new TypeError('ORIGIN is required for API proxy forwarding');
	const transport = upstream.protocol === 'https:' ? https : http;
	const server = http.createServer((req, res) => {
		const pathname = new URL(req.url || '/', 'http://localhost').pathname;
		if ((req.method === 'GET' || req.method === 'HEAD') && pathname === '/health/live') {
			sendJson(res, 200, { status: 'live' });
			return;
		}
		if ((req.method === 'GET' || req.method === 'HEAD') && pathname === '/health/ready') {
			requestReady(upstream, healthTimeoutMs).then((ready) =>
				sendJson(res, ready ? 200 : 503, { status: ready ? 'ready' : 'not_ready' })
			);
			return;
		}
		if (!isApiRequestPath(pathname)) {
			handler(req, res);
			return;
		}

		const headers = safeHeaders(req.headers, upstream, origin);
		const proxyRequest = transport.request(
			{
				protocol: upstream.protocol,
				hostname: upstream.hostname,
				port: upstream.port || undefined,
				path: req.url,
				method: req.method,
				headers
			},
			(proxyResponse) => {
				const responseHeaders = { ...proxyResponse.headers };
				for (const name of Object.keys(responseHeaders)) {
					if (HOP_BY_HOP_HEADERS.has(name)) delete responseHeaders[name];
				}
				res.writeHead(proxyResponse.statusCode || 502, responseHeaders);
				proxyResponse.pipe(res);
			}
		);

		proxyRequest.on('error', () => {
			if (!res.headersSent) sendJson(res, 502, { detail: '서버 연결을 다시 시도해주세요.' });
			else res.destroy();
		});
		proxyRequest.setTimeout(0);
		req.on('aborted', () => proxyRequest.destroy());
		res.on('close', () => {
			if (!res.writableFinished) proxyRequest.destroy();
		});
		req.pipe(proxyRequest);
	});
	server.requestTimeout = 0;
	return server;
}
