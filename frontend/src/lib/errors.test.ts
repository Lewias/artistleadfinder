import { afterEach, describe, expect, it, vi } from 'vitest';
import { errorText, splitCode } from './errors';
import { dismiss, resetToasts, subscribe, toast, type Toast } from './toast';

describe('error texts', () => {
  it('keeps the core text and drops the technical prefix', () => {
    expect(errorText('Некому отправлять: все номера уже получили сообщение.')).toBe(
      'Некому отправлять: все номера уже получили сообщение.',
    );
    expect(errorText(new Error('Ядро недоступно'))).toBe('Ядро недоступно');
    expect(errorText({ message: 'Файл больше 20 МБ.' })).toBe('Файл больше 20 МБ.');
  });
  it('says technical failures in plain words', () => {
    expect(errorText(new TypeError("Cannot read properties of undefined (reading 'invoke')"))).toMatch(
      'Нет связи с ядром',
    );
    expect(errorText('Failed to fetch')).toMatch('Нет соединения');
    expect(errorText(undefined)).toMatch('Что-то пошло не так');
    expect(errorText('boom')).toBe('Непредвиденная ошибка: boom');
  });
  it('separates the log code', () => {
    expect(splitCode('Ошибка базы данных (код a1b2c3). Подробности в журнале.')).toEqual({
      text: 'Ошибка базы данных. Подробности в журнале.',
      code: 'a1b2c3',
    });
  });
});

describe('notifications', () => {
  let items: Toast[] = [];
  subscribe(next => (items = next));
  afterEach(() => {
    vi.useRealTimers();
    resetToasts();
  });
  it('counts repeats instead of stacking copies', () => {
    vi.useFakeTimers();
    toast.error('Нет доступа к файлу (код 0f0f0f).');
    toast.error('Нет доступа к файлу (код 0f0f0f).'); // the same report twice at once
    expect(items).toHaveLength(1);
    vi.advanceTimersByTime(1000);
    toast.error('Нет доступа к файлу (код 0f0f0f).');
    expect(items[0]).toMatchObject({ message: 'Нет доступа к файлу.', code: '0f0f0f', count: 2 });
  });
  it('keeps at most four and lets a closed one leave', () => {
    vi.useFakeTimers();
    for (const n of [1, 2, 3, 4, 5]) toast.error(`Ошибка ${n}`);
    expect(items.filter(item => !item.leaving).map(item => item.message)).toEqual([
      'Ошибка 2',
      'Ошибка 3',
      'Ошибка 4',
      'Ошибка 5',
    ]);
    dismiss(items.at(-1)!.id);
    vi.advanceTimersByTime(300);
    expect(items.map(item => item.message)).toEqual(['Ошибка 2', 'Ошибка 3', 'Ошибка 4']);
  });
  it('ignores empty texts', () => {
    toast.error('  ');
    expect(items).toHaveLength(0);
  });
});
