import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import { expect, test } from 'vitest';

const script = readFileSync('src-tauri/src/capture.js', 'utf8');
function extract(
  blocked = false,
  username = 'artist',
  loginDialog = false,
  text = 'Artist profile',
  description = '1200 Followers - Artist on Instagram: "Independent rapper"',
) {
  return runInNewContext(script, {
    URL,
    location: { href: `https://www.instagram.com/${username}/`, pathname: `/${username}/` },
    document: {
      readyState: 'complete',
      title: 'Artist (@artist)',
      body: { innerText: text },
      querySelector: (selector: string) => {
        if (selector === '[role="dialog"]')
          return loginDialog ? { innerText: 'Зарегистрироваться или Войти' } : null;
        if (selector === 'input[type="password"]') return blocked ? {} : null;
        if (selector === 'main header')
          return {
            innerText: '1200 followers',
            querySelectorAll: () => [
              { href: 'https://l.instagram.com/?u=https%3A%2F%2Fopen.spotify.com%2Fartist%2Fexample' },
              { href: 'https://www.instagram.com/artist/tagged/' },
              { href: 'https://linktr.ee/artist' },
            ],
          };
        if (selector === 'meta[property="instapp:owner_user_id"]') return { content: '4242' };
        if (selector.includes('description')) return { content: description };
        return null;
      },
      querySelectorAll: (selector: string) =>
        selector.includes('img[alt]')
          ? [{ getAttribute: () => 'Photo by Artist. New single out now' }, { getAttribute: () => '' }]
          : [],
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
test('audience-restricted profile is reported as a login gate', () => {
  const restricted = extract(
    false,
    'artist',
    false,
    'Restricted profile | This account is not available for certain audiences. Log in to continue.',
  );
  expect(restricted.blocked).toBe(true);
  expect(restricted.ready).toBe(false);
});
test('rate limit on a profile is flagged for a break', () => {
  const limited = extract(false, 'artist', false, 'Please wait a few minutes. Try again later.');
  expect(limited.blocked).toBe(true);
  expect(limited.rate_limited).toBe(true);
  expect(extract(true).rate_limited).toBe(false);
});
test('collects every external link, grid captions and the Instagram id for Lead Scout', () => {
  const result = extract();
  expect(result.links).toEqual(['https://open.spotify.com/artist/example', 'https://linktr.ee/artist']);
  expect(result.captions).toEqual(['Photo by Artist. New single out now']);
  expect(result.user_id).toBe('4242');
});
test('counts of a Russian meta description stay with their own labels', () => {
  const counts = (description: string) =>
    Object.fromEntries(
      Object.entries(extract(false, 'artist', false, 'Artist profile', description).profile.stats).map(
        ([kind, item]) => [kind, (item as { raw: string }).raw],
      ),
    );
  expect(counts('Подписчики: 96, подписки: 210, публикации: 100 — посмотрите фото и видео')).toEqual({
    followers: '96',
    following: '210',
    posts: '100',
  });
  expect(counts('96 подписчиков, 210 подписок, 100 публикаций — посмотрите фото')).toEqual({
    followers: '96',
    following: '210',
    posts: '100',
  });
});
