import type { FollowUpRun, OutreachEvent } from '../../services/types';
import { plural } from '../../lib/format';

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
  MESSAGES_CLOSED: 'Instagram не даёт написать',
  ALREADY_IN_DIRECT: 'Уже есть переписка в Директе',
  NETWORK_ERROR: 'Сеть',
  SEND_ERROR: 'Ошибка отправки',
  CAMPAIGN_CANCELLED: 'Кампания отменена',
  REPLY_RECEIVED: 'Получен ответ',
  CONVERSATION_STOPPED: 'Общение остановлено',
  USER_STOPPED: 'Остановлено вручную',
  USER_PAUSED: 'Пауза',
  LEGACY: 'Ждёт запуска после обновления',
  THREAD_MISMATCH: 'Диалог не совпал',
  ORIGIN_MISSING: 'Нет подтверждённого сообщения',
  UNCONFIRMED: 'Отправка не подтверждена',
  SENDER_MISMATCH: 'Диалог ведёт другой аккаунт',
};

export const runStatusLabels: Record<string, string> = {
  active: 'Ждёт шага',
  paused: 'На паузе',
  review: 'Проверьте отправку',
  completed: 'Завершена',
  stopped: 'Остановлена',
  failed: 'Ошибка',
};

/** Badge colour of a chain: the shared status-badge classes. */
export const runStatusTone: Record<string, string> = {
  active: 'running',
  paused: 'paused',
  review: 'failed',
  completed: 'completed',
  stopped: 'cancelled',
  failed: 'failed',
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
    case 'followup:sent':
      return { id: event.id, username, outcome: `Follow-up ${String(p.step)} отправлен`, tone: 'good' };
    case 'followup:review':
      return {
        id: event.id,
        username,
        outcome: `Follow-up ${String(p.step)} не подтверждён · проверьте диалог`,
        tone: 'bad',
      };
    case 'followup:stopped':
      return {
        id: event.id,
        username,
        outcome: `Follow-up остановлен — ${reasonLabel(p.reason)}`,
        tone: 'muted',
      };
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

/** Where a chain is («Следующий шаг 2 из 3 · 18.11.2026, 14:00»), or why it ended. */
export function runLine(run: FollowUpRun) {
  const step = `шаг ${Math.min(run.step + 1, run.total)} из ${run.total}`;
  if (run.status === 'active')
    return run.sending
      ? `Отправляется ${step}`
      : `Следующий ${step}${run.next_at ? ` · ${dateTime(run.next_at)}` : ''}`;
  if (run.status === 'review')
    return `Шаг ${(run.review_step ?? run.step) + 1}: Instagram не показал сообщение отправленным`;
  if (run.status === 'paused')
    return `На паузе перед: ${step}${run.reason ? ` · ${reasonLabel(run.reason)}` : ''}`;
  if (run.status === 'completed') return `Отправлены все ${plural(run.total, ['шаг', 'шага', 'шагов'])}`;
  return `${reasonLabel(run.reason) || runStatusLabels[run.status]} · отправлено ${run.step} из ${run.total}`;
}
