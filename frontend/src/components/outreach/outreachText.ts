import type { OutreachEvent } from '../../services/types';

export const campaignStatusLabels: Record<string, string> = {
  draft: 'Черновик',
  scheduled: 'Запланирована',
  running: 'Идёт отправка',
  paused: 'На паузе',
  completed: 'Завершена',
  cancelled: 'Отменена',
  failed: 'Ошибка',
};

export const recipientStatusLabels: Record<string, string> = {
  pending: 'Ожидает',
  queued: 'В очереди',
  sending: 'Отправляется',
  sent: 'Отправлено',
  skipped: 'Пропущен',
  failed: 'Ошибка',
  cancelled: 'Отменён',
  replied: 'Ответил',
};

export const senderStatusLabels: Record<string, string> = {
  active: 'Активен',
  paused: 'На паузе',
  auth_required: 'Нужен вход',
  checkpoint: 'Checkpoint',
  rate_limited: 'Ограничение Instagram',
  disabled: 'Отключён',
};

export const outreachReasons: Record<string, string> = {
  RECIPIENT_UNAVAILABLE: 'Профиль недоступен',
  ALREADY_CONTACTED: 'Уже писали',
  DO_NOT_CONTACT: 'Не связываться',
  SENDER_UNAVAILABLE: 'Аккаунт недоступен',
  AUTH_REQUIRED: 'Нужен вход в аккаунт',
  CHECKPOINT: 'Checkpoint',
  RATE_LIMITED: 'Ограничение Instagram',
  MESSAGE_REJECTED: 'Сообщение отклонено',
  MESSAGES_CLOSED: 'Нельзя написать — только подписаться',
  NETWORK_ERROR: 'Сеть',
  SEND_ERROR: 'Ошибка отправки',
  CAMPAIGN_CANCELLED: 'Кампания отменена',
};

export const conversationLabels: Record<string, string> = {
  waiting_reply: 'Ждём ответа',
  replied: 'Ответил',
  stopped: 'Общение остановлено',
};

export const reasonLabel = (reason: unknown) => outreachReasons[String(reason || '')] || String(reason || '');

export interface OutreachActivity {
  id: number;
  username: string;
  outcome: string;
  tone: 'good' | 'muted' | 'bad';
}

/** One line of "recent activity" (§29); null for events without a recipient outcome. */
export function outreachActivity(event: OutreachEvent): OutreachActivity | null {
  const p = event.payload;
  const username = p.username ? `@${String(p.username)}` : '';
  const details = p.details ? ` · ${String(p.details)}` : '';
  switch (event.type) {
    case 'recipient:sent':
      return { id: event.id, username, outcome: 'Первое сообщение отправлено', tone: 'good' };
    case 'recipient:replied':
      return { id: event.id, username, outcome: 'Получен ответ', tone: 'good' };
    case 'recipient:skipped':
      return {
        id: event.id,
        username,
        outcome: `Пропущен — ${reasonLabel(p.reason)}${details}`,
        tone: 'muted',
      };
    case 'recipient:failed':
      return {
        id: event.id,
        username,
        outcome: `Ошибка — ${reasonLabel(p.reason)}${p.needs_review ? ' · проверьте диалог вручную' : ''}`,
        tone: 'bad',
      };
    case 'recipient:queued':
      return p.retry
        ? { id: event.id, username, outcome: `Повтор после ошибки: ${reasonLabel(p.reason)}`, tone: 'muted' }
        : null;
    case 'sender:unavailable':
      return {
        id: event.id,
        username: 'Аккаунт',
        outcome: `Недоступен — ${reasonLabel(p.reason)}${details}`,
        tone: 'bad',
      };
    case 'campaign:started':
      return { id: event.id, username: 'Кампания', outcome: 'Запущена', tone: 'muted' };
    case 'campaign:paused':
      return { id: event.id, username: 'Кампания', outcome: 'Пауза', tone: 'muted' };
    case 'campaign:resumed':
      return { id: event.id, username: 'Кампания', outcome: 'Продолжена', tone: 'muted' };
    case 'campaign:cancelled':
      return { id: event.id, username: 'Кампания', outcome: 'Отменена', tone: 'bad' };
    case 'campaign:completed':
      return { id: event.id, username: 'Кампания', outcome: 'Завершена', tone: 'good' };
    default:
      return null;
  }
}

/** Datetime-local input value (local time) → ISO with offset for the core (UTC in the DB). */
export function localToIso(value: string): string | null {
  if (!value) return null;
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? null : parsed.toISOString();
}

export const dateTime = (value: string | null) =>
  value ? new Date(value).toLocaleString('ru-RU', { dateStyle: 'short', timeStyle: 'short' }) : '—';

export const templateVariables = [
  'firstName',
  'fullName',
  'username',
  'artistName',
  'followers',
  'source',
  'profileType',
] as const;
