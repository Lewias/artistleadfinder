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
  text = 'Unrelated comment @commenter new single',
  headerLinks: string[] = [],
) {
  return runInNewContext(script, {
    URL,
    setTimeout: (fn: () => void) => fn(),
    location: { href: 'https://www.instagram.com' + path },
    document: {
      readyState: 'complete',
      title: 'Music News (@music_news)',
      body: { innerText: text },
      querySelector: (selector: string) => {
        if (selector === 'input[type="password"]') return blocked ? {} : null;
        if (selector === 'meta[property="og:description"]') return { content: description };
        return null;
      },
      querySelectorAll: (selector: string) =>
        selector === 'main a[href]'
          ? links.map(href => ({ href }))
          : selector === 'article header a[href]'
            ? headerLinks.map(href => ({ href }))
            : [],
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
test('audience-restricted source is a login gate, not a loading failure', async () => {
  const restricted = await extract(
    '/music_news/',
    '',
    false,
    [],
    'Войти | Зарегистрироваться | Ограниченный профиль | Аккаунт недоступен для определенных аудиторий. Чтобы продолжить, выполните вход.',
  );
  expect(restricted.blocked).toBe(true);
  expect(restricted.ready).toBe(false);
});
test('rate limit is flagged separately from login gates', async () => {
  const limited = await extract('/music_news/', '', false, [], 'Повторите попытку позже');
  expect(limited.blocked).toBe(true);
  expect(limited.rate_limited).toBe(true);
  const login = await extract('/music_news/', '', true);
  expect(login.blocked).toBe(true);
  expect(login.rate_limited).toBe(false);
});
test('source grid collects up to 120 publications', async () => {
  const links = Array.from({ length: 150 }, (_, index) => `https://www.instagram.com/p/p${index}/`);
  const result = await extract('/music_news/', '', false, links);
  expect(result.posts).toHaveLength(120);
  expect(result.ready).toBe(true);
});
test('tagged grid of the source is read like the source grid, including /tv/ and /reels/', async () => {
  const result = await extract('/music_news/tagged/', '', false, [
    'https://www.instagram.com/p/one/',
    'https://www.instagram.com/tv/two/',
    'https://www.instagram.com/reels/three/',
  ]);
  expect(result.ready).toBe(true);
  expect(result.posts).toEqual([
    'https://www.instagram.com/p/one/',
    'https://www.instagram.com/tv/two/',
    'https://www.instagram.com/reels/three/',
  ]);
});
test('publication reports its collaborators from the post header', async () => {
  const result = await extract(
    '/p/one/',
    '10 likes - music_news September 25, 2026: "Collab drop"',
    false,
    [],
    '',
    [
      'https://www.instagram.com/music_news/',
      'https://www.instagram.com/New_Artist/',
      'https://www.instagram.com/explore/tags/rap/',
      'https://www.instagram.com/new_artist/',
    ],
  );
  expect(result.author).toBe('music_news');
  expect(result.collaborators).toEqual(['music_news', 'new_artist']);
});
