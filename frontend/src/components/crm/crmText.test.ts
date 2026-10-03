import { describe, expect, it } from 'vitest';
import { dueState, initials, lastContactLabel, money, statusColor } from './crmText';

describe('CRM text', () => {
  it('makes two-letter avatars from names and handles', () => {
    expect(initials('deals_loudnclear')).toBe('DL');
    expect(initials('pimpindown')).toBe('PI');
    expect(initials('Иван Петров')).toBe('ИП');
    expect(initials('+15555550123')).toBe('15');
    expect(initials('')).toBe('?');
  });
  it('keeps configured colors and gives other labels a stable one', () => {
    const statuses = [{ label: 'Артист', color: 'violet' as const }];
    expect(statusColor('Артист', statuses)).toBe('violet');
    expect(statusColor('streetgossipmedia', statuses)).toBe(statusColor('streetgossipmedia', []));
  });
  it('labels dates relative to today', () => {
    const now = new Date(2026, 9, 5, 15);
    expect(lastContactLabel(null, now)).toBe('Никогда');
    expect(lastContactLabel(new Date(2026, 9, 5, 9).toISOString(), now)).toBe('Сегодня');
    expect(lastContactLabel(new Date(2026, 9, 4, 9).toISOString(), now)).toBe('Вчера');
    expect(lastContactLabel(new Date(2026, 9, 1, 9).toISOString(), now)).toBe('4 дн. назад');
    expect(dueState('2026-10-04T00:00:00', now)).toBe('overdue');
    expect(dueState('2026-10-05T00:00:00', now)).toBe('today');
    expect(dueState('2026-10-06T00:00:00', now)).toBe('later');
    expect(dueState(null, now)).toBeNull();
  });
  it('formats money in dollars', () => {
    expect(money(0)).toBe('0 $');
    expect(money(1200)).toMatch(/^1\s200 \$$/);
  });
});
