import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import { expect, test } from 'vitest';

const script = readFileSync('src-tauri/src/follow.js', 'utf8');

function run(path: string, pages: string[][], args: object, body = 'Followers') {
  // Each scroll reveals the next page of the list.
  let shown = 1;
  const scroller = {
    scrollHeight: 2000,
    clientHeight: 400,
    get scrollTop() {
      return 0;
    },
    set scrollTop(_value: number) {
      shown += 1;
    },
  };
  const dialog = {
    querySelectorAll: (selector: string) =>
      selector === 'a[href]'
        ? pages
            .slice(0, shown)
            .flat()
            .map(name => ({ href: `https://www.instagram.com/${name}/` }))
        : [scroller],
  };
  const fn = runInNewContext(`(${script})`, {
    URL,
    Date,
    Promise,
    setTimeout: (callback: () => void) => callback(),
    getComputedStyle: () => ({ overflowY: 'auto' }),
    location: { href: `https://www.instagram.com${path}`, pathname: path },
    document: {
      body: { innerText: body },
      querySelector: (selector: string) => (selector === '[role="dialog"]' ? dialog : null),
    },
  });
  return fn(args);
}

test('pages through a follower list up to the limit without the source itself', async () => {
  const result = await run(
    '/rapdaily/followers/',
    [
      ['rapdaily', 'a1', 'a2'],
      ['a3', 'explore'],
      ['a4', 'a5'],
    ],
    { pageSize: 12, delayMs: 1000, max: 4 },
  );
  expect(result.ready).toBe(true);
  expect(result.users).toEqual(['a1', 'a2', 'a3', 'a4']);
  expect(result.limited).toBe(true);
});
test('stops at the end of the list and on rate limits', async () => {
  const done = await run('/rapdaily/following/', [['b1'], []], { max: 50 });
  expect(done.users).toEqual(['b1']);
  expect(done.limited).toBe(false);
  const limited = await run('/rapdaily/followers/', [['c1']], { max: 50 }, 'Please wait a few minutes');
  expect(limited).toMatchObject({ ready: false, blocked: true, rate_limited: true });
  expect((await run('/rapdaily/', [['x']], {})).ready).toBe(false);
});
