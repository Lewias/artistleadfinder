// Sets one version in every manifest: `node scripts/bump-version.mjs 0.2.0`.
// The release workflow builds the tag `v<version>`; the updater compares this number.
import { readFileSync, writeFileSync } from 'node:fs';

const version = process.argv[2];
if (!/^\d+\.\d+\.\d+$/.test(version ?? '')) {
  console.error('Usage: node scripts/bump-version.mjs <major.minor.patch>');
  process.exit(1);
}

const edit = (path, pattern, replacement) => {
  const text = readFileSync(path, 'utf8');
  if (!pattern.test(text)) throw new Error(`Version not found in ${path}`);
  writeFileSync(path, text.replace(pattern, replacement));
};

edit('package.json', /("version":\s*")[^"]+(")/, `$1${version}$2`);
edit('src-tauri/tauri.conf.json', /("version":\s*")[^"]+(")/, `$1${version}$2`);
edit('src-tauri/Cargo.toml', /^(version\s*=\s*")[^"]+(")/m, `$1${version}$2`);
edit('backend/artist_lead_finder/service.py', /("version":\s*")[^"]+(")/, `$1${version}$2`);
edit('backend/pyproject.toml', /^(version\s*=\s*")[^"]+(")/m, `$1${version}$2`);
console.log(`Version ${version}. Commit, then tag: git tag v${version} && git push origin v${version}`);
