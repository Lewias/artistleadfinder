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

export const skipReasons: Record<string, string> = {
  ALREADY_PROCESSED: 'уже обработан',
  FOLLOWERS_TOO_LOW: 'мало подписчиков',
  FOLLOWERS_TOO_HIGH: 'слишком много подписчиков',
  NO_CONTACT: 'нет контактов',
  WRONG_PROFILE_TYPE: 'не подходит тип профиля',
  PROFILE_UNAVAILABLE: 'профиль недоступен',
  RATE_LIMITED: 'ограничение Instagram',
  CLASSIFICATION_FAILED: 'не удалось классифицировать',
};

const methodLabels: Record<string, string> = {
  post: 'пост',
  reel: 'reel',
  tagged: 'tagged',
  story: 'story',
  followers: 'подписчики',
  following: 'подписки',
  comment: 'комментарий',
};

/** One readable line for the live event feed. */
export function eventText(event: ScoutEvent): string {
  const p = event.payload;
  const at = (name: unknown) => (name ? `@${String(name)}` : '');
  switch (event.type) {
    case 'scout:start':
      return `Запуск: ${((p.sources as string[]) || []).map(at).join(', ')}`;
    case 'source:start':
      return `Источник ${at(p.source)}`;
    case 'candidate:found':
      return `Кандидат ${at(p.username)} · ${methodLabels[p.method || ''] || p.method} · из ${at(p.source)}`;
    case 'profile:analyzing':
      return `Анализ ${at(p.username)}`;
    case 'profile:skipped':
      return `Пропущен ${at(p.username)} — ${skipReasons[p.reason || ''] || p.reason}`;
    case 'lead:found':
      return `Лид ${at(p.username)} · ${p.category} · ${p.confidence}%`;
    case 'source:done':
      return `Источник ${at(p.source)} просканирован · лидов ${p.leads_found ?? 0}`;
    case 'scout:pause':
      return 'Пауза';
    case 'scout:stop':
      return 'Остановлено, прогресс сохранён';
    case 'scout:error':
      return `Ошибка: ${skipReasons[p.reason || ''] || p.reason}${p.profile ? ` · ${at(p.profile)}` : ''}`;
    case 'discovery:page':
      return `Разбор страницы · ${p.method} · ${at(p.source)}`;
    case 'scout:done':
      return `Готово · найдено ${p.leads ?? 0}, пропущено ${p.skipped ?? 0}`;
    default:
      return event.type;
  }
}
