import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const page = fileURLToPath(new URL('../../src/routes/odi/session/interview/AIsetup/+page.svelte', import.meta.url));
const icons = fileURLToPath(new URL('../../src/lib/odi/icons/puplecheck.svg', import.meta.url));

test('interview tips use the bundled purple check icon instead of a relative URL', () => {
  const source = readFileSync(page, 'utf8');
  assert.ok(existsSync(icons), 'bundled icon must exist');
  assert.match(source, /import\s*\{[^}]*\bpuplecheck\b[^}]*\}\s*from\s*['"]\$lib\/odi\/icons['"]/);
  assert.match(source, /icon:\s*puplecheck\b/);
  assert.doesNotMatch(source, /['"]\.\/puplecheck\.svg['"]/);
});
