export const pages = [
  {
    id: 'dashboard',
    label: 'Обзор',
    eyebrow: 'ВАШЕ ПРОСТРАНСТВО',
    title: 'Обзор',
    description: 'Что происходит с поисками, артистами и вашей базой.',
  },
  {
    id: 'discovery',
    label: 'Скаутинг',
    eyebrow: 'ПОИСК АРТИСТОВ',
    title: 'Скаутинг артистов',
    description:
      'Укажите музыкальные источники. Приложение найдёт артистов и покажет, какую услугу им предложить.',
  },
  {
    id: 'leads',
    label: 'База артистов',
    eyebrow: 'ВАШИ КОНТАКТЫ',
    title: 'База артистов',
    description: 'Все собранные профили, сигналы и статусы в одном месте.',
  },
  {
    id: 'history',
    label: 'История поисков',
    eyebrow: 'ЗАПУСКИ',
    title: 'История поисков',
    description: 'Результаты и состояние прошедших поисков.',
  },
  {
    id: 'profiles',
    label: 'Профили',
    eyebrow: 'БРАУЗЕРНЫЕ СЕССИИ',
    title: 'Профили Instagram',
    description: 'Отдельная сессия, cookies и прокси для каждого профиля.',
  },
  {
    id: 'settings',
    label: 'Настройки',
    eyebrow: 'ПАРАМЕТРЫ',
    title: 'Настройки',
    description: 'Параметры оценки, источники и локальные данные.',
  },
] as const;
export type PageId = (typeof pages)[number]['id'];
