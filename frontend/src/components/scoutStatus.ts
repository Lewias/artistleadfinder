import type { ScoutAccountRow, ScoutEvent } from '../services/types';

export function runStatus(row: ScoutAccountRow): string {
  const run = row.run;
  if (row.found >= row.target) return 'Цель достигнута';
  if (!run) return 'Не запускался';
  if (run.stage === 'interrupted') return 'Прерван при закрытии приложения';
  if (run.status === 'running') return run.wait_seconds ? 'Ждёт по темпу' : 'Идёт поиск';
  if (run.status === 'paused') return run.error ? 'Требуется внимание' : 'На паузе';
  if (run.status === 'completed') return 'Завершён';
  if (run.status === 'cancelled') return 'Остановлен';
  return run.status;
}

/** LeadSkipReason → readable text. */
export const skipReasons: Record<string, string> = {
  SOURCE_ACCOUNT: 'это сам источник',
  IGNORED_USERNAME: 'в списке игнора',
  ALREADY_PROCESSED: 'уже обработан',
  DUPLICATE_LEAD: 'уже есть в CRM',
  MISSING_REQUIRED_DATA: 'нет нужных данных',
  FOLLOWERS_TOO_LOW: 'мало подписчиков',
  FOLLOWERS_TOO_HIGH: 'слишком много подписчиков',
  NO_CONTACT: 'нет контактов',
  WRONG_PROFILE_TYPE: 'не подходит тип профиля',
  LOW_CONFIDENCE: 'низкая уверенность',
  PROFILE_UNAVAILABLE: 'профиль недоступен',
  PROFILE_NOT_FOUND: 'профиль не найден',
  PROFILE_PRIVATE: 'закрытый профиль',
  PROFILE_PARSE_FAILED: 'не удалось прочитать профиль',
  CLASSIFICATION_FAILED: 'не удалось классифицировать',
  RATE_LIMITED: 'ограничение Instagram',
  INTERRUPTED: 'приложение закрылось во время поиска',
  OTHER: 'ошибка обработки',
};

export const categoryLabels: Record<string, string> = {
  artist: 'Артист',
  producer: 'Продюсер',
  media: 'Медиа / сервис',
  other: 'Другое',
};

const methodLabels: Record<string, string> = {
  post: 'пост',
  reel: 'reel',
  tagged: 'tagged',
  story: 'story',
  followers: 'подписчики',
  following: 'подписки',
  comment: 'комментарий',
  profile: 'проверка профиля',
};
export const methodLabel = (method: string) => methodLabels[method] || method;

const reasonText = (reason: unknown) => skipReasons[String(reason || '')] || String(reason || '');

/** One readable line for the live event feed. */
export function eventText(event: ScoutEvent): string {
  const p = event.payload;
  const at = (name: unknown) => (name ? `@${String(name)}` : '');
  switch (event.type) {
    case 'scout:run-started':
      return `Запуск: ${((p.sources as string[]) || []).map(at).join(', ')}`;
    case 'scout:source-started':
      return `Источник ${at(p.source)}`;
    case 'scout:source-completed':
      return `Источник ${at(p.source)} просканирован · лидов ${p.leads_found ?? 0}`;
    case 'scout:candidate-found':
      return `Кандидат ${at(p.username)} · ${methodLabel(p.method || '')} · из ${at(p.source)}`;
    case 'scout:profile-resolving':
      return `Чтение профиля ${at(p.username)}`;
    case 'scout:profile-resolved':
      return `Профиль ${at(p.username)} прочитан · подписчиков ${p.followers ?? 'нет данных'}`;
    case 'scout:classification-started':
      return `Классификация ${at(p.username)}`;
    case 'scout:classification-completed':
      return `${at(p.username)}: ${p.category} · ${p.confidence}%`;
    case 'scout:profile-skipped':
      return `Пропущен ${at(p.username)} — ${reasonText(p.reason)}${p.details ? ` (${p.details})` : ''}`;
    case 'scout:lead-created':
      return `Лид ${at(p.username)} · ${p.category} · ${p.confidence}%`;
    case 'scout:lead-updated':
      return `Лид ${at(p.username)} обновлён${p.change === 'new source' ? ` · новый источник ${at(p.source)}` : ''}`;
    case 'scout:paused':
      return 'Пауза';
    case 'scout:resumed':
      return 'Продолжено';
    case 'scout:cancelled':
      return 'Остановлено, прогресс сохранён';
    case 'scout:error':
      return `Ошибка: ${reasonText(p.reason)}${p.profile ? ` · ${at(p.profile)}` : ''}`;
    case 'scout:discovery-page':
      return `Разбор страницы · ${p.method} · ${at(p.source)}`;
    case 'scout:completed':
      return `Готово · найдено ${p.leads ?? 0}, обновлено ${p.leads_updated ?? 0}, пропущено ${p.skipped ?? 0}`;
    default:
      return event.type;
  }
}

export interface ActivityItem {
  id: number;
  username: string;
  type: string;
  outcome: string;
  tone: 'good' | 'muted' | 'bad';
}

/** Recent activity: one row per profile decision (lead saved, updated or skipped). */
export function activityItem(event: ScoutEvent): ActivityItem | null {
  const p = event.payload;
  const type =
    p.category && p.confidence != null ? `${categoryLabels[p.category] || p.category} ${p.confidence}%` : '';
  const base = { id: event.id, username: String(p.username || ''), type };
  switch (event.type) {
    case 'scout:lead-created':
      return { ...base, outcome: 'Лид сохранён', tone: 'good' };
    case 'scout:lead-updated':
      return {
        ...base,
        outcome: p.change === 'new source' ? `Лид обновлён · новый источник @${p.source}` : 'Лид обновлён',
        tone: 'good',
      };
    case 'scout:profile-skipped':
      return { ...base, outcome: `Пропущен: ${reasonText(p.reason)}`, tone: 'muted' };
    case 'scout:error':
      return p.kind === 'profile' && p.profile
        ? { ...base, username: String(p.profile), outcome: 'Ошибка обработки', tone: 'bad' }
        : null;
    default:
      return null;
  }
}

export const sourceStatusLabels: Record<string, string> = {
  new: 'Новый',
  queued: 'В очереди',
  scanning: 'Сканируется',
  done: 'Просканирован',
  stopped: 'Остановлен',
  interrupted: 'Прерван',
  error: 'Ошибка',
  rate_limited: 'Ограничение',
};
