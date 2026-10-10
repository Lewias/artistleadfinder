import type { CloudCategory, CloudJob, CloudStage, CloudView } from '../../services/types';

export const categoryLabels: Record<CloudCategory, string> = {
  ARTIST: 'Артист',
  PRODUCER: 'Продюсер',
  MEDIA: 'Медиа',
  OTHER: 'Другое',
  UNKNOWN: 'Не определено',
};

export const viaLabels: Record<string, string> = {
  posts: 'посты',
  tagged: 'отметки',
  followers: 'подписчики',
  following: 'подписки',
  profiles: 'профили',
};

export const stageLabels: Record<CloudStage, string> = {
  queued: 'В очереди',
  collecting: 'Парсинг',
  completed: 'Готово',
  failed: 'Ошибка',
  cancelled: 'Отменено',
};

export const stageTone: Record<CloudStage, string> = {
  queued: 'queued',
  collecting: 'running',
  completed: 'completed',
  failed: 'failed',
  cancelled: 'cancelled',
};

export const activeStage = (job: CloudJob) => job.stage === 'queued' || job.stage === 'collecting';

/** «Парсинг» or «Рассылка» while the job works; the rest is the same for both kinds. */
export const stageLabel = (job: CloudJob) =>
  job.stage === 'collecting' && job.kind === 'outreach' ? 'Рассылка' : stageLabels[job.stage];

export const stepLabels: Record<string, string> = {
  source: 'сетка источника',
  tagged_grid: 'отметки источника',
  post: 'публикация',
  tagged_post: 'отмеченная публикация',
  followers: 'подписчики',
  following: 'подписки',
  profile: 'проверка профиля',
};

export const outcomeLabels: Record<string, string> = {
  added: 'Добавлен в CRM',
  updated: 'Обновлён в CRM',
  filtered: 'Не прошёл фильтр',
  error: 'Ошибка',
  sent: 'Отправлено',
  failed: 'Не ушло',
  skipped: 'Пропущен',
};

export const viewLabels: Partial<Record<CloudView, string>> = {
  all: 'Все',
  saved: 'В CRM',
  filtered: 'Не прошли фильтр',
};

export const outreachViewLabels: Partial<Record<CloudView, string>> = {
  all: 'Все',
  sent: 'Отправлено',
  problems: 'Пропущены и не ушли',
};

export const kindLabels: Record<CloudJob['kind'], string> = {
  scout: 'Поиск',
  outreach: 'Рассылка',
};
