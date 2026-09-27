export const number = (value: number) => new Intl.NumberFormat('ru-RU').format(value);
export const date = (value: string | null) => value ? new Date(value).toLocaleDateString('ru-RU') : 'Нет данных';
export const activity = (value: string | null) => {
  if (!value) return 'Нет данных';
  const days = Math.max(0, Math.floor((Date.now() - new Date(value).getTime()) / 86400000));
  return days === 0 ? 'Сегодня' : `${days} дн. назад`;
};
export const statusLabels: Record<string, string> = { new: 'Новый', reviewed: 'Просмотрен', qualified: 'Подходит', rejected: 'Отклонён', contacted: 'Контакт отмечен', queued: 'В очереди', running: 'Выполняется', paused: 'На паузе', completed: 'Завершён', failed: 'Ошибка', cancelled: 'Отменён' };
export const genres = ['Hip-Hop', 'Trap', 'R&B', 'Pop', 'Indie', 'Electronic', 'Rock', 'Soul'];
