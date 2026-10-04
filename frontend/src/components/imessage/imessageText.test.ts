import { describe, expect, it } from 'vitest';
import {
  chainPlan,
  deepLink,
  jobStatusDetail,
  jobStatusLabel,
  parseRecipients,
  recipientsText,
} from './imessageText';

describe('iMessage recipients', () => {
  it('reads one number per line with an optional text after ";"', () => {
    const { recipients, invalid } = parseRecipients(
      '+1 (555) 555-0123\n\n0015555550124; Привет; как дела?\n+15555550125\tТекст\n89991234567\nFan@Example.com',
    );
    expect(recipients).toEqual([
      { phone: '+15555550123', message: '' },
      { phone: '+15555550124', message: 'Привет; как дела?' },
      { phone: '+15555550125', message: 'Текст' },
      { phone: 'fan@example.com', message: '' },
    ]);
    expect(invalid).toEqual(['89991234567']);
  });
  it('writes the list back in the same format', () => {
    const list = [
      { phone: '+15555550123', message: '' },
      { phone: '+15555550124', message: 'Текст' },
    ];
    expect(parseRecipients(recipientsText(list)).recipients).toEqual(list);
  });
});

describe('Shortcuts deep link', () => {
  it('encodes the name and the whole task URL', () => {
    const url = 'http://192.168.1.5:47615/v2/next?token=a-b_c&x=1';
    const link = deepLink('Verse Мост & Co', url);
    const params = new URLSearchParams(link.split('?')[1]);
    expect(link.startsWith('shortcuts://run-shortcut?name=Verse%20')).toBe(true);
    expect(params.get('name')).toBe('Verse Мост & Co');
    expect(params.get('input')).toBe('text');
    expect(params.get('text')).toBe(url);
    expect([...params.keys()]).toEqual(['name', 'input', 'text']);
  });
});

describe('job statuses', () => {
  it('never claim delivery', () => {
    for (const label of Object.values(jobStatusLabel)) expect(label.toLowerCase()).not.toContain('доставлен');
    const acknowledged = jobStatusDetail({
      status: 'execution_acknowledged',
      ack_scope: 'text',
      text_acked_at: null,
    });
    expect(acknowledged).toContain('доставка не подтверждаются');
  });
});

describe('chains of messages', () => {
  const file = { id: 'a'.repeat(32), filename: 'cover.png', mime: 'image/png', size: 10 };
  const part = (text: string, files = 0) => ({ text, attachments: Array(files).fill(file) });
  it('sends texts in one launch and gives files a launch of their own', () => {
    expect(chainPlan([part('Привет'), part('Как дела?')])).toEqual({ messages: 2, launches: 1, error: null });
    expect(chainPlan([part('Привет'), part('', 1)])).toEqual({ messages: 1, launches: 1, error: null });
    expect(chainPlan([part('Привет'), part('Трек', 1), part('Пока')]).launches).toBe(3);
    expect(chainPlan([part('Трек', 1), part('Пока'), part('Ещё')]).launches).toBe(2);
  });
  it('cannot open with files or keep an empty message', () => {
    expect(chainPlan([part('', 1), part('Текст')]).error).toMatch('начинает');
    expect(chainPlan([part('Текст'), part('')]).error).toMatch('пустое');
  });
});
