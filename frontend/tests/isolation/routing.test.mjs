import { strict as assert } from 'node:assert';
import { rootRoute, legacyLocation } from '../../src/lib/rehear-routing.js';
const screens = ['login', 'join', 'practice', 'report', 'session', 'templates', 'favorites', 'waitvr', 'GITA'];
assert.equal(rootRoute('/'), '/odi');
for (const name of screens) {
  assert.equal(rootRoute(`/${name}/example`), `/odi/${name}/example`);
  assert.equal(legacyLocation(`/odi/${name}/example`, '?x=1&x=2'), `/${name}/example?x=1&x=2`);
}
assert.equal(rootRoute('/rehear-register'), '/odi/register');
assert.equal(legacyLocation('/odi/register', '?next=%2F'), '/rehear-register?next=%2F');
assert.equal(legacyLocation('/odi/', '?x=1'), '/?x=1');
for (const p of ['/odi/db/users', '/odi/coaching/x', '/odi/files/read', '/odi/xreal_rehear/x', '/odi/unknown', '/api/auth/session']) assert.equal(legacyLocation(p, '?x=1'), null);
for (const p of ['/api/auth/session', '/auth/login', '/register', '/reporting']) assert.equal(rootRoute(p), null);
console.log('routing assertions passed: 9 screen prefixes, legacy queries, API and auth exclusions');
