import { afterEach, describe, expect, it, vi } from 'vitest';
import { dismiss, resetToasts, subscribe, toast, undoable, type Toast } from './toast';

const current = () => {
  let items: Toast[] = [];
  subscribe(value => (items = value))();
  return items;
};
const settle = () => new Promise(resolve => setTimeout(resolve, 0));

afterEach(() => resetToasts());

describe('undoable', () => {
  it('commits once the toast leaves', async () => {
    const commit = vi.fn();
    const restore = vi.fn();
    const id = undoable('Источник убран', { commit, restore })!;
    expect(commit).not.toHaveBeenCalled();
    dismiss(id);
    dismiss(id);
    await settle();
    expect(commit).toHaveBeenCalledTimes(1);
    expect(restore).not.toHaveBeenCalled();
  });

  it('skips the commit after «Отменить»', async () => {
    const commit = vi.fn();
    const restore = vi.fn();
    const id = undoable('Источник убран', { commit, restore })!;
    current()
      .find(item => item.id === id)!
      .action!.run();
    dismiss(id);
    await settle();
    expect(restore).toHaveBeenCalledTimes(1);
    expect(commit).not.toHaveBeenCalled();
  });

  it('restores the item when the commit fails', async () => {
    const restore = vi.fn();
    const id = undoable('Источник убран', {
      commit: () => Promise.reject(new Error('down')),
      restore,
    })!;
    dismiss(id);
    await settle();
    await settle();
    expect(restore).toHaveBeenCalledTimes(1);
    expect(current().some(item => item.kind === 'error')).toBe(true);
  });

  it('keeps two removals with the same text apart', () => {
    undoable('Удалено', { commit: vi.fn(), restore: vi.fn() });
    undoable('Удалено', { commit: vi.fn(), restore: vi.fn() });
    toast.info('Обычное');
    toast.info('Обычное');
    expect(current().filter(item => item.message === 'Удалено')).toHaveLength(2);
    expect(current().filter(item => item.message === 'Обычное')).toHaveLength(1);
  });
});
