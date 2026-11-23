// Only known screen namespaces are rewritten; backend and auth paths are untouched.
const screens = new Set(['login','join','practice','report','session','templates','favorites','waitvr','GITA']);
export function rootRoute(path) {
  if (path === '/') return '/odi';
  if (path === '/rehear-register' || path.startsWith('/rehear-register/')) return '/odi/register' + path.slice('/rehear-register'.length);
  return screens.has(path.split('/')[1]) ? '/odi' + path : null;
}
export function legacyLocation(path, search = '') {
  if (path === '/odi' || path === '/odi/') return '/' + search;
  if (path === '/odi/register' || path.startsWith('/odi/register/')) return '/rehear-register' + path.slice('/odi/register'.length) + search;
  if (!path.startsWith('/odi/')) return null;
  return screens.has(path.split('/')[2]) ? path.slice(4) + search : null;
}
