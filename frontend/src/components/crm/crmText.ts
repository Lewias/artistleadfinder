import type { CrmChannelKind, CrmColor, CrmId, CrmSource, CrmStatus } from '../../services/types';

export const crmColors: CrmColor[] = ['violet', 'blue', 'green', 'amber', 'red', 'pink', 'slate'];
export const colorNames: Record<CrmColor, string> = {
  violet: 'Фиолетовый',
  blue: 'Синий',
  green: 'Зелёный',
  amber: 'Жёлтый',
  red: 'Красный',
  pink: 'Розовый',
  slate: 'Серый',
};
const looseColors: CrmColor[] = ['slate', 'blue', 'green', 'violet', 'pink'];

/** Configured statuses keep their color; other labels (sources, imports) get a stable one. */
export function statusColor(label: string, statuses: CrmStatus[]): CrmColor {
  const known = statuses.find(status => status.label === label);
  if (known) return known.color;
  let hash = 0;
  for (const char of label) hash = (hash * 31 + char.charCodeAt(0)) >>> 0;
  return looseColors[hash % looseColors.length];
}

/** Two letters for the avatar: words of the name, else its first two characters. */
export function initials(name: string) {
  const words = name
    .replace(/[@._\-+]+/g, ' ')
    .split(/\s+/)
    .filter(word => /[\p{L}\p{N}]/u.test(word));
  const letters =
    words.length > 1
      ? words[0][0] + words[1][0]
      : (words[0] ?? name).replace(/[^\p{L}\p{N}]/gu, '').slice(0, 2);
  return letters.toUpperCase() || '?';
}

export const money = (value: number) =>
  `${new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 2 }).format(value)} $`;

const day = (value: string) => {
  const date = new Date(value);
  return new Date(date.getFullYear(), date.getMonth(), date.getDate());
};

/** «Сегодня», «Вчера», «5 дн. назад» or a date; «Никогда» without one. */
export function lastContactLabel(value: string | null, now = new Date()) {
  if (!value) return 'Никогда';
  const days = Math.round((day(now.toISOString()).getTime() - day(value).getTime()) / 86400000);
  if (days <= 0) return 'Сегодня';
  if (days === 1) return 'Вчера';
  if (days < 7) return `${days} дн. назад`;
  return new Date(value).toLocaleDateString('ru-RU', {
    day: 'numeric',
    month: 'short',
    year: days > 300 ? 'numeric' : undefined,
  });
}

/** Overdue, today or later for the next action date. */
export function dueState(value: string | null, now = new Date()): 'overdue' | 'today' | 'later' | null {
  if (!value) return null;
  const diff = day(value).getTime() - day(now.toISOString()).getTime();
  return diff < 0 ? 'overdue' : diff === 0 ? 'today' : 'later';
}

export function dueLabel(value: string, now = new Date()) {
  const state = dueState(value, now);
  if (state === 'today') return 'сегодня';
  const text = new Date(value).toLocaleDateString('ru-RU', { day: 'numeric', month: 'short' });
  return state === 'overdue' ? `просрочено · ${text}` : text;
}

/** yyyy-mm-dd for <input type="date"> from an ISO date-time. */
export const dateInput = (value: string | null) => (value ? value.slice(0, 10) : '');

export const kindLabels: Record<CrmChannelKind, string> = {
  instagram: 'Instagram',
  email: 'Email',
  phone: 'Телефон',
};
export const kindPlaceholders: Record<CrmChannelKind, string> = {
  instagram: 'username или ссылка',
  email: 'name@example.com',
  phone: '+15555550123',
};

export const crmNames: Record<CrmId, string> = { instagram: 'CRM Instagram', imessage: 'CRM iMessage' };

export function sourceText(source: CrmSource['id'], target: CrmId) {
  if (source === 'leads')
    return {
      title: 'База парсинга',
      description:
        target === 'imessage'
          ? 'Все лиды с метками и заметками; телефон или email, если найден, станет основным каналом.'
          : 'Лиды Instagram вместе с метками, заметками, источником и найденными контактами.',
    };
  if (source === 'imessage')
    return {
      title: 'CRM iMessage',
      description: 'Телефоны и email из CRM iMessage вместе с метками и заметками.',
    };
  return {
    title: 'CRM Instagram',
    description: 'Все контакты вместе с метками и заметками; телефон или email станет основным каналом.',
  };
}
