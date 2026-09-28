export const number = (value: number) => new Intl.NumberFormat('ru-RU').format(value);
export const date = (value: string | null) =>
  value ? new Date(value).toLocaleDateString('ru-RU') : 'Нет данных';
export const activity = (value: string | null) => {
  if (!value) return 'Нет данных';
  const days = Math.max(0, Math.floor((Date.now() - new Date(value).getTime()) / 86400000));
  return days === 0 ? 'Сегодня' : `${days} дн. назад`;
};
export const waitLabel = (seconds: number) => {
  const total = Math.ceil(seconds);
  return total < 60 ? `${total} с` : `${Math.ceil(total / 60)} мин`;
};
/** Russian plural: plural(1, ['источник', 'источника', 'источников']) → '1 источник'. */
export const plural = (count: number, forms: [string, string, string]) => {
  const tens = count % 100;
  const units = count % 10;
  const form = tens > 10 && tens < 20 ? 2 : units === 1 ? 0 : units >= 2 && units <= 4 ? 1 : 2;
  return `${count} ${forms[form]}`;
};
export const sourceLabels: Record<string, string> = {
  mock: 'Демо',
  imported: 'Импорт',
  instagram_scout: 'Скаут',
  instagram_browser: 'Браузер',
};
export const statusLabels: Record<string, string> = {
  new: 'Новый',
  reviewed: 'Просмотрен',
  qualified: 'Подходит',
  rejected: 'Отклонён',
  contacted: 'Контакт отмечен',
  queued: 'В очереди',
  running: 'Выполняется',
  paused: 'На паузе',
  completed: 'Завершён',
  failed: 'Ошибка',
  cancelled: 'Отменён',
};
export const genres = ['Hip-Hop', 'Trap', 'R&B', 'Pop', 'Indie', 'Electronic', 'Rock', 'Soul'];
