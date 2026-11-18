import { existsSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const raw = (process.argv[2] ?? process.env.LEGACYLIFT_API_URL ?? '/api').trim();

export function normalizeApiUrl(value) {
  const cleaned = value.trim().replace(/\/+$/, '');
  if (!cleaned || cleaned === '/api') return '/api';
  if (/^https?:\/\//i.test(cleaned)) {
    return cleaned.endsWith('/api') ? cleaned : `${cleaned}/api`;
  }
  return cleaned;
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const apiBaseUrl = normalizeApiUrl(raw);
  const payload = `window.__LEGACYLIFT_CONFIG__ = { apiBaseUrl: ${JSON.stringify(apiBaseUrl)} };\n`;
  const publicTarget = join(root, 'public', 'app-config.js');
  writeFileSync(publicTarget, payload, 'utf8');
  console.log(`Actualizado ${publicTarget} -> ${apiBaseUrl}`);

  const distTarget = join(root, 'dist', 'legacylift', 'browser', 'app-config.js');
  if (existsSync(dirname(distTarget))) {
    writeFileSync(distTarget, payload, 'utf8');
    console.log(`Actualizado ${distTarget} -> ${apiBaseUrl}`);
  }
}
