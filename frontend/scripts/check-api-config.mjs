import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { normalizeApiUrl } from './set-api-url.mjs';

const frontendRoot = join(dirname(fileURLToPath(import.meta.url)), '..');
const repoRoot = join(frontendRoot, '..');

assert.equal(normalizeApiUrl(''), '/api');
assert.equal(normalizeApiUrl('/api/'), '/api');
assert.equal(normalizeApiUrl('https://api.ejemplo.com'), 'https://api.ejemplo.com/api');
assert.equal(normalizeApiUrl('https://api.ejemplo.com/api/'), 'https://api.ejemplo.com/api');

const indexHtml = readFileSync(join(frontendRoot, 'src/index.html'), 'utf8');
assert.ok(indexHtml.includes('/app-config.js'), 'index.html debe cargar /app-config.js');
assert.ok(existsSync(join(frontendRoot, 'public/app-config.js')), 'Debe existir public/app-config.js');

const apiTs = readFileSync(join(frontendRoot, 'src/app/core/api/api.service.ts'), 'utf8');
assert.ok(apiTs.includes('resolveApiBase'), 'ApiService debe resolver URL configurable');
assert.ok(apiTs.includes('eventsUrl('), 'ApiService debe exponer eventsUrl');

const facadeTs = readFileSync(join(frontendRoot, 'src/app/core/state/workspace.facade.ts'), 'utf8');
assert.ok(facadeTs.includes('this.api.eventsUrl('), 'WorkspaceFacade SSE debe usar this.api.eventsUrl');

const firebaseJson = JSON.parse(readFileSync(join(repoRoot, 'firebase.json'), 'utf8'));
assert.equal(firebaseJson.hosting.public, 'frontend/dist/legacylift/browser');
assert.ok(firebaseJson.hosting.rewrites.some(r => r.source === '**' && r.destination === '/index.html'));

console.log('PASS runtime API config, SSE URL resolution, and firebase.json checks');
