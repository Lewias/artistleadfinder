import { describe, expect, it } from 'vitest';
import { chatBlocks } from './chat';

describe('chat blocks', () => {
  it('gather each answer with its drafts and steps under the question', () => {
    const blocks = chatBlocks([
      { kind: 'user', text: 'напиши Jay' },
      { kind: 'tool', text: 'Открываю скилл продаж' },
      { kind: 'assistant', text: 'Сначала посмотрю переписку.' },
      { kind: 'escalation', text: 'Спрашиваю сильную модель' },
      { kind: 'tool', text: 'Готовлю черновик' },
      { kind: 'draft', text: '', draft_id: 7 },
      { kind: 'assistant', text: 'Готово.' },
      { kind: 'user', text: 'спасибо' },
    ]);
    expect(blocks).toEqual([
      { kind: 'user', text: 'напиши Jay' },
      {
        kind: 'answer',
        texts: ['Сначала посмотрю переписку.', 'Готово.'],
        drafts: [7],
        steps: [
          { kind: 'tool', text: 'Открываю скилл продаж' },
          { kind: 'escalation', text: 'Спрашиваю сильную модель' },
          { kind: 'tool', text: 'Готовлю черновик' },
        ],
      },
      { kind: 'user', text: 'спасибо' },
    ]);
  });

  it('start an answer even before its first text', () => {
    expect(chatBlocks([{ kind: 'tool', text: 'Ищу в CRM' }])).toEqual([
      { kind: 'answer', texts: [], drafts: [], steps: [{ kind: 'tool', text: 'Ищу в CRM' }] },
    ]);
  });
});
