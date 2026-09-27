import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import { expect, test } from 'vitest';
const script = readFileSync('src-tauri/src/search.js', 'utf8');
function extract(links: string[], body = 'Search results', href = 'https://search.brave.com/search?q=artist') {
  return runInNewContext(script, { URL, location: { href }, document: {
    readyState: 'complete', body: { innerText: body }, querySelector: () => null,
    querySelectorAll: () => links.map(href => ({ href, innerText: 'Artist' })),
  } });
}
test('search accepts canonical profiles only and removes duplicates', () => {
  const result = extract(['https://instagram.com/Artist/?hl=en', 'https://www.instagram.com/artist/',
    'https://instagram.com/p/123/', 'https://instagram.com/popular/music/', 'https://instagram.com/accounts/',
    'https://instagram.com.evil.test/artist/', 'https://user:secret@instagram.com/artist/', 'javascript:alert(1)']);
  expect(result.ready).toBe(true);
  expect(result.results).toEqual([{ url: 'https://www.instagram.com/artist/', username: 'artist', title: 'Artist' }]);
  expect(result.query).toBe('artist');
});
test('search distinguishes restrictions, empty results and unloaded pages', () => {
  expect(extract([], 'Verify you are human').blocked).toBe(true);
  expect(extract([], 'No results found').ready).toBe(true);
  expect(extract([]).ready).toBe(false);
  expect(extract([], '', 'https://example.com/search?q=artist').ready).toBe(false);
  expect(extract(Array.from({ length: 30 }, (_, index) => `https://instagram.com/artist${index}/`)).results).toHaveLength(20);
});
