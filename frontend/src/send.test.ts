import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import { describe, expect, test } from 'vitest';

const script = readFileSync('src-tauri/src/send.js', 'utf8');

type Reply = { status?: number; body?: unknown; url?: string } | 'throw';
type Call = { path: string; init: { method?: string; headers?: Record<string, string>; body?: string } };

async function run(args: object, replies: Reply[], options: { host?: string; cookie?: string } = {}) {
  const calls: Call[] = [];
  const queue = [...replies];
  const fetch = async (path: string, init: Call['init']) => {
    calls.push({ path, init });
    const reply = queue.shift();
    if (!reply || reply === 'throw') throw new TypeError('Failed to fetch');
    const status = reply.status ?? 200;
    return {
      status,
      ok: status >= 200 && status < 300,
      url: reply.url ?? `https://www.instagram.com${path}`,
      headers: { get: () => 'application/json' },
      json: async () => reply.body,
    };
  };
  const fn = runInNewContext(`(${script})`, {
    URL,
    URLSearchParams,
    JSON,
    String,
    AbortController,
    setTimeout,
    clearTimeout,
    fetch,
    navigator: { onLine: true },
    location: { hostname: options.host ?? 'www.instagram.com', href: 'https://www.instagram.com/' },
    document: {
      cookie: options.cookie ?? 'mid=1; csrftoken=SECRET123; ds_user_id=5',
      querySelectorAll: () => [],
    },
  });
  const result = await fn(args);
  return { result, calls };
}

const base = { username: 'jaycarter', user_id: '42', text: 'Hey Jay', client_context: '123456789' };
const ok = { body: { status: 'ok', payload: { item_id: 'item1', thread_id: 'thread1' } } };

describe('outreach send script', () => {
  test('sends one message and returns ids but no secrets', async () => {
    const { result, calls } = await run(base, [ok]);
    expect(result).toEqual({
      outcome: 'sent',
      status: 200,
      user_id: '42',
      message_id: 'item1',
      thread_id: 'thread1',
    });
    expect(calls).toHaveLength(1);
    expect(calls[0].init.method).toBe('POST');
    const body = new URLSearchParams(calls[0].init.body);
    expect(body.get('recipient_users')).toBe('[["42"]]');
    expect(body.get('client_context')).toBe('123456789');
    expect(body.get('text')).toBe('Hey Jay');
    expect(JSON.stringify(result)).not.toContain('SECRET123');
  });

  test('looks up the recipient id first when the lead has none', async () => {
    const { result, calls } = await run({ ...base, user_id: '' }, [
      { body: { data: { user: { id: '777' } } } },
      ok,
    ]);
    expect(calls[0].path).toContain('web_profile_info/?username=jaycarter');
    expect(result.outcome).toBe('sent');
    expect(result.user_id).toBe('777');
  });

  test('maps refusals to typed reasons', async () => {
    expect((await run(base, [{ status: 429 }])).result.error).toBe('rate_limited');
    expect((await run(base, [{ status: 400, body: { message: 'feedback_required' } }])).result.error).toBe(
      'rate_limited',
    );
    expect(
      (await run(base, [{ status: 200, url: 'https://www.instagram.com/accounts/login/' }])).result.error,
    ).toBe('login');
    expect((await run(base, [{ status: 400, body: { message: 'checkpoint_required' } }])).result.error).toBe(
      'checkpoint',
    );
    expect((await run({ ...base, user_id: '' }, [{ status: 404 }])).result.error).toBe('not_found');
    expect((await run(base, [{ status: 400, body: { status: 'fail', message: 'nope' } }])).result.error).toBe(
      'rejected',
    );
  });

  test('only failures before the message request are retryable', async () => {
    expect((await run({ ...base, user_id: '' }, ['throw'])).result.error).toBe('network');
    expect((await run(base, ['throw'])).result.error).toBe('unconfirmed');
  });

  test('refuses to run outside Instagram, without a session or with bad input', async () => {
    expect((await run(base, [], { host: 'example.com' })).result.error).toBe('no_instagram_tab');
    expect((await run(base, [], { cookie: 'mid=1' })).result.error).toBe('login');
    expect((await run({ ...base, text: '' }, [])).result.error).toBe('bad_request');
    expect((await run({ ...base, username: 'bad name' }, [])).result.error).toBe('bad_request');
  });
});
