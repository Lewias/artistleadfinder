import { describe, expect, it } from 'vitest';
import { deepLink, jobStatusDetail, jobStatusLabel, parseRecipients, recipientsText } from './imessageText';

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
