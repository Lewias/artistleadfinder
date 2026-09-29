import { describe, expect, it } from 'vitest';
import type { OutreachEvent } from '../../services/types';
import { localToIso, outreachActivity } from './outreachText';

const event = (type: OutreachEvent['type'], payload: Record<string, unknown>): OutreachEvent => ({
  id: 1,
  campaign_id: 1,
  type,
  payload,
  created_at: '2026-09-29T10:00:00+00:00',
});

describe('outreach activity lines', () => {
  it('describes recipient outcomes', () => {
    expect(outreachActivity(event('recipient:sent', { username: 'artistone' }))).toMatchObject({
      username: '@artistone',
      outcome: 'Первое сообщение отправлено',
      tone: 'good',
    });
    expect(
      outreachActivity(event('recipient:skipped', { username: 'artisttwo', reason: 'ALREADY_CONTACTED' }))
        ?.outcome,
    ).toBe('Пропущен — Уже писали');
    expect(
      outreachActivity(event('recipient:failed', { username: 'x', reason: 'SENDER_UNAVAILABLE' }))?.outcome,
    ).toBe('Ошибка — Аккаунт недоступен');
    expect(outreachActivity(event('recipient:replied', { username: 'four' }))?.outcome).toBe('Получен ответ');
    expect(
      outreachActivity(event('recipient:failed', { username: 'x', reason: 'SEND_ERROR', needs_review: true }))
        ?.outcome,
    ).toContain('проверьте диалог');
  });

  it('hides plain queueing and shows retries', () => {
    expect(outreachActivity(event('recipient:queued', { username: 'a' }))).toBeNull();
    expect(
      outreachActivity(event('recipient:queued', { username: 'a', retry: 1, reason: 'NETWORK_ERROR' }))
        ?.outcome,
    ).toBe('Повтор после ошибки: Сеть');
  });

  it('converts local schedule time to UTC ISO', () => {
    const iso = localToIso('2026-10-01T09:30');
    expect(iso).toMatch(/Z$/);
    expect(new Date(iso!).getTime()).toBe(new Date('2026-10-01T09:30').getTime());
    expect(localToIso('')).toBeNull();
  });
});
