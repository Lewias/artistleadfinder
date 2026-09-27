import { expect, test } from 'vitest';
import { waitLabel } from './lib/format';

test('wait label rounds up to seconds or minutes', () => {
  expect(waitLabel(7.2)).toBe('8 с');
  expect(waitLabel(59)).toBe('59 с');
  expect(waitLabel(61)).toBe('2 мин');
  expect(waitLabel(1800)).toBe('30 мин');
});
