import type { WorkspaceUsername } from '../../services/types';
import { plural } from '../../lib/format';
import { reasonLabel } from './outreachText';

const handle = /^[a-z0-9_.]{1,30}$/;
const profileUrl = /^(?:https?:\/\/)?(?:www\.)?instagram\.com\/([a-z0-9_.]{1,30})\/?(?:[?#]\S*)?$/i;

/** Usernames typed or pasted (@name, name, profile link; separated by spaces, commas, lines). */
export function parseUsernames(text: string): { usernames: string[]; invalid: string[] } {
  const usernames: string[] = [];
  const invalid: string[] = [];
  for (const entry of text.split(/[\s,;]+/).filter(Boolean)) {
    const name = (entry.match(profileUrl)?.[1] ?? entry.replace(/^@/, '')).toLowerCase();
    if (handle.test(name)) usernames.push(name);
    else invalid.push(entry);
  }
  return { usernames: [...new Set(usernames)], invalid };
}

/** Bulk messages: one message per block, blocks separated by an empty line. */
export function parseMessages(text: string): string[] {
  return text
    .split(/\n\s*\n/)
    .map(block => block.trim())
    .filter(Boolean);
}

export const chipTone: Record<string, string> = {
  sent: 'sent',
  contacted: 'sent',
  pending: 'queued',
  queued: 'queued',
  sending: 'queued',
  failed: 'bad',
  review: 'bad',
  skipped: 'muted',
  cancelled: '',
  new: '',
};

const statusText: Record<string, string> = {
  sent: 'Отправлено',
  contacted: 'Уже писали',
  pending: 'Ожидает',
  queued: 'В очереди',
  sending: 'Отправляется',
  failed: 'Ошибка',
  review: 'Не подтверждено: проверьте диалог и отметьте результат в карточке лида',
  skipped: 'Пропущен',
  cancelled: 'Не отправлено (остановлено)',
  new: 'Ещё не писали',
};

/** Tooltip of a username chip. */
export function chipTitle(item: WorkspaceUsername): string {
  const reason = item.reason && item.status !== 'cancelled' ? ` — ${reasonLabel(item.reason)}` : '';
  const details = item.details ? ` · ${item.details}` : '';
  return `${statusText[item.status] ?? item.status}${reason}${details}`;
}

export const accountsLabel = (n: number) => plural(n, ['аккаунт', 'аккаунта', 'аккаунтов']);
export const messagesLabel = (n: number) => plural(n, ['сообщение', 'сообщения', 'сообщений']);
