import { expect, test } from 'vitest';
import type { CaptureQueue, ScoutAccountRow } from '../services/types';
import { runStatus } from './scoutStatus';

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
  expect(runStatus(row(3, run({ status: 'paused' })))).toBe('Остановлен');
  expect(runStatus(row(3, run({ status: 'paused', error: 'Нужен вход' })))).toBe('Требуется внимание');
  expect(runStatus(row(3, run({ status: 'paused', stage: 'interrupted' })))).toBe(
    'Прерван при закрытии приложения',
  );
});
