import { describe, expect, test } from 'vitest';
import { accountsLabel, chipTitle, messagesLabel, parseMessages, parseUsernames } from './outreachList';

describe('primary outreach list', () => {
  test('parses handles, links and separators, drops duplicates and reports bad entries', () => {
    expect(
      parseUsernames('@Getabag.bo, shotbyjae_\nhttps://www.instagram.com/vezolotti/ getabag.bo bad!name'),
    ).toEqual({ usernames: ['getabag.bo', 'shotbyjae_', 'vezolotti'], invalid: ['bad!name'] });
  });

  test('splits bulk messages on empty lines and keeps line breaks inside one', () => {
    expect(parseMessages('Yo bro\nwhats ur #?\n\n\n  Second one  \n')).toEqual([
      'Yo bro\nwhats ur #?',
      'Second one',
    ]);
  });

  test('labels counts and chip states in Russian', () => {
    expect(accountsLabel(59)).toBe('59 аккаунтов');
    expect(accountsLabel(21)).toBe('21 аккаунт');
    expect(messagesLabel(19)).toBe('19 сообщений');
    expect(messagesLabel(3)).toBe('3 сообщения');
    expect(chipTitle({ username: 'a', status: 'skipped', reason: 'ALREADY_CONTACTED', details: null })).toBe(
      'Пропущен — Уже писали',
    );
  });
});
