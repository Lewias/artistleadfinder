import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import { expect, test } from 'vitest';

// Real DOM behaviour is covered by backend/tests/test_discovery_fixtures.py (Chromium).
const gridScript = readFileSync('src-tauri/src/grid.js', 'utf8');
const postScript = readFileSync('src-tauri/src/scout.js', 'utf8');

const anchor = (href: string) => ({ href, getAttribute: () => href, innerText: '' });

function grid(path: string, links: string[], args: object = {}, text = 'Posts', password = false) {
  const fn = runInNewContext(`(${gridScript})`, {
    URL,
    Date,
    Promise,
    setTimeout: (fn: () => void) => fn(),
    location: { href: 'https://www.instagram.com' + path, pathname: path },
    document: {
      readyState: 'complete',
      title: 'Music News (@music_news)',
      body: { innerText: text, scrollHeight: 1000 },
      querySelector: (selector: string) => (selector === 'input[type="password"]' && password ? {} : null),
      querySelectorAll: (selector: string) => (selector === 'main a[href]' ? links.map(anchor) : []),
    },
  });
  return fn({ maxScrollRounds: 2, scrollDelayMs: 300, ...args });
}

function post(path: string, description: string, headerLinks: string[] = [], text = 'Publication') {
  const header = { innerText: headerLinks.join(' '), querySelectorAll: () => headerLinks.map(anchor) };
  const fn = runInNewContext(`(${postScript})`, {
    URL,
    Promise,
    setTimeout: (fn: () => void) => fn(),
    location: { href: 'https://www.instagram.com' + path, pathname: path },
    getComputedStyle: () => ({ overflowY: 'visible' }),
    document: {
      readyState: 'complete',
      body: { innerText: text },
      querySelector: (selector: string) => {
        if (selector === 'meta[property="og:description"]') return { content: description };
        if (selector === 'article header') return headerLinks.length ? header : null;
        return null;
      },
      querySelectorAll: () => [],
    },
  });
  return fn({ comments: true });
}

test('grid collects unique publication URLs of Instagram only and stops at login gates', async () => {
  const links = [
    'https://www.instagram.com/p/one/',
    'https://www.instagram.com/p/one/?hl=en',
    'https://www.instagram.com/music_news/reel/two/',
    'https://www.instagram.com/music_news/p/one/c/123/',
    'https://evil.test/p/no/',
  ];
  const result = await grid('/music_news/', links);
  expect(result.ready).toBe(true);
  expect(result.posts).toEqual([
    'https://www.instagram.com/p/one/',
    'https://www.instagram.com/music_news/reel/two/',
  ]);
  expect((await grid('/another/', links)).ready).toBe(false);
  expect(await grid('/music_news/', links, {}, 'Posts', true)).toMatchObject({
    blocked: true,
    block_reason: 'login',
  });
});
test('grid reports typed gates: restricted profile, checkpoint and rate limit', async () => {
  const restricted = await grid(
    '/music_news/',
    [],
    {},
    'Ограниченный профиль. Чтобы продолжить, выполните вход.',
  );
  expect(restricted).toMatchObject({ blocked: true, block_reason: 'login', rate_limited: false });
  const checkpoint = await grid('/music_news/', [], {}, 'Confirm it’s you to continue');
  expect(checkpoint.block_reason).toBe('checkpoint');
  const limited = await grid('/music_news/', [], {}, 'Please wait a few minutes before you try again.');
  expect(limited).toMatchObject({ blocked: true, rate_limited: true, block_reason: 'rate_limited' });
});
test('grid skips processed shortcodes, honours maxPosts and reads the tagged grid', async () => {
  const links = Array.from({ length: 150 }, (_, index) => `https://www.instagram.com/p/p${index}/`);
  const result = await grid('/music_news/', links, { maxPosts: 120, skip: ['p0', 'p1'] });
  expect(result.posts).toHaveLength(120);
  expect(result.posts[0]).toBe('https://www.instagram.com/p/p2/');
  expect(result.already_processed).toBe(2);
  const tagged = await grid('/music_news/tagged/', [
    'https://www.instagram.com/tv/two/',
    'https://www.instagram.com/reels/three/',
  ]);
  expect(tagged.posts).toEqual([
    'https://www.instagram.com/tv/two/',
    'https://www.instagram.com/reel/three/',
  ]);
});
test('grid stops without progress instead of scrolling forever', async () => {
  const result = await grid('/music_news/', ['https://www.instagram.com/p/one/'], {
    maxPosts: 50,
    maxScrollRounds: 40,
    maxNoProgressRounds: 2,
  });
  expect(result.end_reason).toBe('end_of_grid');
  expect(result.rounds).toBe(2);
});
test.each(['/music_news/p/one/', '/music_news/reel/one/', '/reel/one/', '/tv/one/'])(
  'reads the publication author at %s from metadata',
  async path => {
    const result = await post(path, 'music_news on September 25, 2026: "Rapper @caption_only new album"');
    expect(result.ready).toBe(true);
    expect(result.author).toBe('music_news');
    expect(result.author_strategy).toBe('metadata');
  },
);
test('caption mentions are never collaborators; the header is the authorship block', async () => {
  const result = await post(
    '/p/one/',
    '10 likes - music_news September 25, 2026: "Collab with @caption_guest"',
    [
      'https://www.instagram.com/music_news/',
      'https://www.instagram.com/New_Artist/',
      'https://www.instagram.com/explore/tags/rap/',
    ],
  );
  expect(result.collaborators).toEqual(['music_news', 'new_artist']);
  expect(JSON.stringify(result)).not.toContain('caption_guest"]');
});
test('unknown author is reported, not guessed; deleted publications are unavailable', async () => {
  const unknown = await post('/p/one/', '');
  expect(unknown.author).toBeNull();
  expect(unknown.parse_error).toBe('author_not_found');
  const gone = await post('/p/one/', '', [], "Sorry, this page isn't available.");
  expect(gone).toMatchObject({ ready: true, unavailable: true });
});
