import { describe, expect, it } from 'vitest';
import type { FollowUpRun, OutreachEvent } from '../../services/types';
import { localToIso, outreachActivity, runLine } from './outreachText';

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

const run = (patch: Partial<FollowUpRun>): FollowUpRun => ({
  id: 1,
  lead_id: 1,
  username: 'jaycarter',
  display_name: 'Jay',
  sender_id: 'a',
  sender_name: 'Main',
  sequence_name: 'Bumps',
  status: 'active',
  reason: null,
  details: null,
  step: 1,
  total: 3,
  last_sent_at: null,
  next_at: null,
  sending: false,
  review_job_id: null,
  review_step: null,
  conversation_status: 'waiting_reply',
  steps: [],
  ...patch,
});

describe('follow-up chain lines', () => {
  it('says what comes next and why a chain ended', () => {
    expect(runLine(run({}))).toBe('Следующий шаг 2 из 3');
    expect(runLine(run({ sending: true }))).toBe('Отправляется шаг 2 из 3');
    expect(runLine(run({ status: 'review', review_step: 1 }))).toBe(
      'Шаг 2: Instagram не показал сообщение отправленным',
    );
    expect(runLine(run({ status: 'paused', reason: 'USER_PAUSED' }))).toBe(
      'На паузе перед: шаг 2 из 3 · Пауза',
    );
    expect(runLine(run({ status: 'completed', step: 3 }))).toBe('Отправлены все 3 шага');
    expect(runLine(run({ status: 'stopped', reason: 'REPLY_RECEIVED' }))).toBe(
      'Получен ответ · отправлено 1 из 3',
    );
  });

  it('describes follow-up events', () => {
    expect(outreachActivity(event('followup:sent', { username: 'jay', step: 2 }))).toMatchObject({
      outcome: 'Follow-up 2 отправлен',
      tone: 'good',
    });
    expect(
      outreachActivity(event('followup:stopped', { username: 'jay', reason: 'REPLY_RECEIVED' })),
    ).toMatchObject({
      outcome: 'Follow-up остановлен — Получен ответ',
    });
  });
});
