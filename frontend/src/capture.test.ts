import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import { expect, test } from 'vitest';

const script = readFileSync('src-tauri/src/capture.js', 'utf8');
function extract(blocked = false, username = 'artist', loginDialog = false) {
  return runInNewContext(script, {
    URL,
    location: { href: `https://www.instagram.com/${username}/`, pathname: `/${username}/` },
    document: {
      readyState: 'complete', title: 'Artist (@artist)', body: { innerText: 'Artist profile' },
      querySelector: (selector: string) => {
        if (selector === '[role="dialog"]') return loginDialog ? { innerText: 'Зарегистрироваться или Войти' } : null;
        if (selector === 'input[type="password"]') return blocked ? {} : null;
        if (selector === 'main header') return { innerText: '1200 followers', querySelectorAll: () => [{ href: 'https://l.instagram.com/?u=https%3A%2F%2Fopen.spotify.com%2Fartist%2Fexample' }] };
        if (selector.includes('description')) return { content: '1200 Followers - Artist on Instagram: "Independent rapper"' };
        return null;
      },
    },
  });
}
test('reads bounded profile metadata and unwraps music URL', () => {
  const result = extract();
  expect(result.ready).toBe(true);
  expect(result.external_url).toBe('https://open.spotify.com/artist/example');
  expect(result).not.toHaveProperty('cookies');
});
test('login wall and stale metadata are not accepted as profiles', () => {
  expect(extract(true).blocked).toBe(true);
  expect(extract(true).ready).toBe(false);
  expect(extract(false, 'other').ready).toBe(false);
  expect(extract(false, 'art').ready).toBe(false);
  expect(extract(false, 'direct').ready).toBe(false);
  expect(extract(false, 'artist', true).blocked).toBe(true);
});
