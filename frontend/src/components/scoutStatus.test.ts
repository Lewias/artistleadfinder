import { expect, test } from 'vitest';
import type { CaptureQueue, ScoutAccountRow, ScoutEvent } from '../services/types';
import { eventText, runStatus } from './scoutStatus';

const profile = { id: 'a'.repeat(32), name: 'Main', cookie_count: 5, proxy: null };
const run = (patch: Partial<CaptureQueue>): CaptureQueue => ({
  id: 1,
  status: 'running',
  stage: 'scout_reading',
  cursor: 0,
  total: 1,
  url: null,
  error: null,
  ...patch,
});
const row = (found: number, current: CaptureQueue | null): ScoutAccountRow => ({
  profile,
  target: 100,
  found,
  run: current,
});

test('account status reflects goal, run state and pacing', () => {
  expect(runStatus(row(0, null))).toBe('Не запускался');
  expect(runStatus(row(100, run({})))).toBe('Цель достигнута');
  expect(runStatus(row(3, run({})))).toBe('Идёт поиск');
  expect(runStatus(row(3, run({ wait_seconds: 12 })))).toBe('Ждёт по темпу');
  expect(runStatus(row(3, run({ status: 'paused' })))).toBe('На паузе');
  expect(runStatus(row(3, run({ status: 'paused', error: 'Нужен вход' })))).toBe('Требуется внимание');
  expect(runStatus(row(3, run({ status: 'paused', stage: 'interrupted' })))).toBe(
    'Прерван при закрытии приложения',
  );
});

test('event feed lines are readable', () => {
  const event = (type: string, payload: ScoutEvent['payload']): ScoutEvent => ({
    id: 1,
    job_id: 1,
    type,
    payload,
    created_at: '2026-09-28T10:00:00Z',
  });
  expect(eventText(event('lead:found', { username: 'nova', category: 'artist', confidence: 91 }))).toBe(
    'Лид @nova · artist · 91%',
  );
  expect(eventText(event('profile:skipped', { username: 'big', reason: 'FOLLOWERS_TOO_HIGH' }))).toBe(
    'Пропущен @big — слишком много подписчиков',
  );
  expect(eventText(event('candidate:found', { username: 'a', method: 'tagged', source: 'rapdaily' }))).toBe(
    'Кандидат @a · tagged · из @rapdaily',
  );
});
