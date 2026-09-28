import { expect, test } from 'vitest';
import { plural, waitLabel } from './lib/format';

test('wait label rounds up to seconds or minutes', () => {
  expect(waitLabel(7.2)).toBe('8 с');
  expect(waitLabel(59)).toBe('59 с');
  expect(waitLabel(61)).toBe('2 мин');
  expect(waitLabel(1800)).toBe('30 мин');
});

test('russian plural forms', () => {
  const forms: [string, string, string] = ['источник', 'источника', 'источников'];
  expect([1, 2, 5, 11, 21, 22, 112].map(count => plural(count, forms))).toEqual([
    '1 источник',
    '2 источника',
    '5 источников',
    '11 источников',
    '21 источник',
    '22 источника',
    '112 источников',
  ]);
});
