import { describe, expect, it } from 'vitest';
import { chatBlocks } from './chat';

describe('chat blocks', () => {
  it('gather each answer with its sends and steps under the question', () => {
    const blocks = chatBlocks([
      { kind: 'user', text: 'напиши Jay' },
      { kind: 'tool', text: 'Открываю скилл продаж' },
      { kind: 'assistant', text: 'Сначала посмотрю переписку.' },
      { kind: 'tool', text: 'Отправляю iMessage' },
      { kind: 'send', text: '', message_id: 7 },
      { kind: 'assistant', text: 'Готово.' },
      { kind: 'user', text: 'спасибо' },
    ]);
    expect(blocks).toEqual([
      { kind: 'user', text: 'напиши Jay' },
      {
        kind: 'answer',
        texts: ['Сначала посмотрю переписку.', 'Готово.'],
        sends: [7],
        steps: [
          { kind: 'tool', text: 'Открываю скилл продаж' },
          { kind: 'tool', text: 'Отправляю iMessage' },
        ],
      },
      { kind: 'user', text: 'спасибо' },
    ]);
  });

  it('start an answer even before its first text', () => {
    expect(chatBlocks([{ kind: 'tool', text: 'Ищу в CRM' }])).toEqual([
      { kind: 'answer', texts: [], sends: [], steps: [{ kind: 'tool', text: 'Ищу в CRM' }] },
    ]);
  });
});
