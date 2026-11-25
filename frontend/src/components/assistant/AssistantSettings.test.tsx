import { renderToStaticMarkup } from 'react-dom/server';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { AssistantSettings } from './AssistantSettings';
import { Assistant } from '../../pages/Assistant';

const resource = vi.hoisted(() => ({ configured: true }));
vi.mock('../../hooks/useResource', () => ({
  useResource: (method: string) => ({
    data:
      method === 'telegram.state'
        ? { linked: false }
        : {
            configured: resource.configured,
            running: false,
            error: '',
            items: [],
            sends: [],
            chat_sends: [],
            actions: [],
            models: { main: 'Haiku 5.5' },
          },
    error: '',
    loading: false,
    refresh: () => {},
  }),
}));

describe('ready assistant without provider setup', () => {
  beforeEach(() => {
    resource.configured = true;
  });

  function hasNoSetup(html: string) {
    for (const label of ['Ключ API', 'Сменить модель', 'Адрес API', 'Основная модель', 'Подключите модель']) {
      expect(html).not.toContain(label);
    }
    expect(html).not.toContain('type="password"');
    expect(html).not.toContain('radiogroup');
    expect(html).not.toContain('<select');
  }

  it('shows only Telegram connection in assistant settings', () => {
    const html = renderToStaticMarkup(<AssistantSettings />);
    expect(html).toContain('Подключить Telegram');
    hasNoSetup(html);
    expect(html).not.toContain('<input');
  });

  it('opens the chat immediately without model selection', () => {
    const html = renderToStaticMarkup(<Assistant />);
    expect(html).toContain('Спросите что угодно');
    hasNoSetup(html);
  });

  it('never offers a key or provider form even when unavailable', () => {
    resource.configured = false;
    const html = renderToStaticMarkup(<Assistant />);
    expect(html).toContain('Ассистент временно недоступен');
    expect(html).toContain('Повторить');
    hasNoSetup(html);
  });
});
