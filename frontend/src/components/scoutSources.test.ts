import { expect, test } from 'vitest';
import { parseScoutSources, sourceEntries, sourceHandle } from './scoutSources';

test('identifies the mistyped source in a multiline list without changing the input', () => {
  const values = [
    'ttps://www.instagram.com/detroit.rapdaily/',
    ...Array.from({ length: 8 }, (_, index) => `https://www.instagram.com/source${index}/`),
  ];
  const result = parseScoutSources(values.join('\n'));
  expect(result.error).toContain('Источник №1');
  expect(result.error).toContain('https://');
  expect(result.values).toEqual(values);
  expect(parseScoutSources('h' + values.join('\n')).error).toBe('');
});

test('accepts profiles and handles while rejecting publication URLs and other hosts', () => {
  expect(parseScoutSources(' @music_news\nhttps://instagram.com/artist/?hl=ru\n').error).toBe('');
  for (const invalid of [
    'https://instagram.com/p/one/',
    'https://instagram.com/explore/',
    'https://instagram.com.evil.test/artist/',
    'https://user:secret@instagram.com/artist/',
  ]) {
    const result = parseScoutSources(`@valid\n${invalid}`);
    expect(result.error).toContain('Источник №2');
    expect(result.error).not.toContain(invalid);
  }
});

test('reports the source limit and allows an empty form without an error', () => {
  expect(parseScoutSources(' \n').error).toBe('');
  expect(parseScoutSources(Array.from({ length: 21 }, (_, i) => `@source${i}`).join('\n')).error).toContain(
    '20',
  );
});

test('chip input accepts bare handles, handles and links; chips show the username', () => {
  expect(sourceEntries(' rapgoat.tv, @topdailyrap\nhttps://www.instagram.com/rapczn/ ')).toEqual([
    '@rapgoat.tv',
    '@topdailyrap',
    'https://www.instagram.com/rapczn/',
  ]);
  expect(parseScoutSources(sourceEntries('rapgoat.tv viralraps').join('\n')).error).toBe('');
  expect(sourceHandle('https://www.instagram.com/rapgoat.tv/')).toBe('rapgoat.tv');
});
