const reserved = new Set([
  'accounts',
  'explore',
  'reels',
  'reel',
  'p',
  'direct',
  'stories',
  'challenge',
  'web',
  'about',
  'developer',
  'legal',
  'privacy',
  'terms',
]);

export function parseScoutSources(text: string): { values: string[]; error: string } {
  const values = text
    .split(/[\n,]/)
    .map(value => value.trim())
    .filter(Boolean);
  if (values.length > 20) return { values, error: 'Добавьте не больше 20 источников.' };
  for (const [index, value] of values.entries()) {
    const username =
      value.match(/^@([a-zA-Z0-9_.]{1,30})$/)?.[1] ??
      value.match(
        /^https:\/\/(?:www\.)?instagram\.com(?::443)?\/([a-zA-Z0-9_.]{1,30})\/?(?:[?#][^\s]*)?$/i,
      )?.[1];
    if (!username || reserved.has(username.toLowerCase())) {
      return {
        values,
        error: `Источник №${index + 1}: укажите ссылку на профиль вида https://www.instagram.com/имя/ или @имя. Проверьте начало ссылки: https://.`,
      };
    }
  }
  return { values, error: '' };
}
