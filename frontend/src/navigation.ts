// Sidebar order; `placement: 'footer'` entries sit under the divider at the bottom.
export const pages = [
  {
    id: 'discovery',
    label: 'Lead Scout',
    eyebrow: 'INSTAGRAM',
    title: 'Lead Scout',
    description:
      'SMM-источники → связанные профили → проверка артиста → фильтры → лид. Scout никому не пишет.',
    placement: 'main',
  },
  {
    id: 'profiles',
    label: 'Аккаунты',
    eyebrow: 'INSTAGRAM',
    title: 'Аккаунты',
    description: 'Отдельная сессия, cookies и прокси для каждого профиля.',
    placement: 'main',
  },
  {
    id: 'leads',
    label: 'База артистов',
    eyebrow: 'КОНТАКТЫ',
    title: 'База артистов',
    description: 'Все собранные профили, сигналы и статусы в одном месте.',
    placement: 'main',
  },
  {
    id: 'outreach',
    label: 'Рассылки',
    eyebrow: 'INSTAGRAM',
    title: 'Рассылки',
    description:
      'Первое сообщение найденным лидам: кампания → проверка → очередь → отправка из окна аккаунта → история и CRM.',
    placement: 'main',
  },
  {
    id: 'history',
    label: 'История',
    eyebrow: 'ЗАПУСКИ',
    title: 'История поисков',
    description: 'Результаты и состояние прошедших поисков.',
    placement: 'main',
  },
  {
    id: 'dashboard',
    label: 'Статистика',
    eyebrow: 'ОБЗОР',
    title: 'Статистика',
    description: 'Что происходит с поисками, артистами и вашей базой.',
    placement: 'main',
  },
  {
    id: 'settings',
    label: 'Настройки',
    eyebrow: 'ПАРАМЕТРЫ',
    title: 'Настройки',
    description: 'Темп сбора, параметры оценки и локальные данные.',
    placement: 'footer',
  },
] as const;
export type PageId = (typeof pages)[number]['id'];
