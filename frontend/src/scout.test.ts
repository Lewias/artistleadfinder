import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import { expect, test } from 'vitest';
const script = readFileSync('src-tauri/src/scout.js', 'utf8');
function extract(
  path: string,
  description = '',
  blocked = false,
  links = [
    'https://www.instagram.com/p/one/',
    'https://www.instagram.com/p/one/?hl=en',
    'https://evil.test/p/no/',
  ],
) {
  return runInNewContext(script, {
    URL,
    setTimeout: (fn: () => void) => fn(),
    location: { href: 'https://www.instagram.com' + path },
    document: {
      readyState: 'complete',
      title: 'Music News (@music_news)',
      body: { innerText: 'Unrelated comment @commenter new single' },
      querySelector: (selector: string) => {
        if (selector === 'input[type="password"]') return blocked ? {} : null;
        if (selector === 'meta[property="og:description"]') return { content: description };
        return null;
      },
      querySelectorAll: (selector: string) =>
        selector === 'main a[href]' ? links.map(href => ({ href })) : [],
    },
  });
}
test('source collects unique publication URLs only and stops at login gates', async () => {
  expect((await extract('/music_news/')).posts).toEqual(['https://www.instagram.com/p/one/']);
  expect((await extract('/music_news/')).ready).toBe(true);
  expect((await extract('/another/')).ready).toBe(false);
  expect((await extract('/music_news/', '', true)).blocked).toBe(true);
});
test('loaded source accepts publication links containing the source username', async () => {
  const result = await extract('/music_news/', '', false, [
    'https://www.instagram.com/music_news/p/one/',
    'https://www.instagram.com/music_news/p/one/?img_index=1',
    'https://www.instagram.com/music_news/reel/two/',
    'https://www.instagram.com/music_news/p/one/c/123/',
    'https://evil.test/music_news/p/no/',
  ]);
  expect(result.ready).toBe(true);
  expect(result.posts).toEqual([
    'https://www.instagram.com/music_news/p/one/',
    'https://www.instagram.com/music_news/reel/two/',
  ]);
});
test.each(['/music_news/p/one/', '/music_news/reel/one/', '/reel/one/'])(
  'reads a publication at %s when metadata has no likes or comments prefix',
  async path => {
    const result = await extract(path, 'music_news September 25, 2026: "Rapper @artist has a new single". ');
    expect(result.ready).toBe(true);
    expect(result.author).toBe('music_news');
    expect(result.caption).toBe('Rapper @artist has a new single');
  },
);
test('caption mentions never become comment candidates', async () => {
  const result = await extract(
    '/p/one/',
    '100 likes - music_news on September 21, 2026: "New single @artist"',
  );
  expect(result.caption).toBe('New single @artist');
  expect(result.comments).toEqual([]);
  expect(result.author).toBe('music_news');
  expect(result.published_at).toBeNull();
  expect(result.ready).toBe(true);
  expect((await extract('/p/one/')).ready).toBe(false);
});
