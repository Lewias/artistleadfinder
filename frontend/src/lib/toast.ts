import { errorText, splitCode } from './errors';

export type ToastKind = 'error' | 'success' | 'info';
export interface Toast {
  id: number;
  kind: ToastKind;
  title: string;
  message: string;
  /** Code of the core's log line, for a generic error. */
  code?: string;
  /** How many times the same message came in a row. */
  count: number;
  /** Bumped by a repeat: the timer starts over. */
  revision: number;
  duration: number;
  leaving: boolean;
  /** When the last copy came in. */
  at: number;
  /** A button in the toast, e.g. «Отменить»; pressing it also closes the toast. */
  action?: ToastAction;
  /** Runs once when the toast starts leaving, whatever closed it. */
  onLeave?: () => void;
}
export interface ToastAction {
  label: string;
  run: () => void;
}
interface Options {
  title?: string;
  duration?: number;
  action?: ToastAction;
  onLeave?: () => void;
}

const MAX_VISIBLE = 4;
// The same error reported twice at once (two views of one state) counts once.
const ECHO_MS = 400;
export const LEAVE_MS = 240;
const DURATION: Record<ToastKind, number> = { error: 7000, success: 4000, info: 5000 };
const TITLE: Record<ToastKind, string> = { error: 'Ошибка', success: 'Готово', info: 'Уведомление' };

let toasts: Toast[] = [];
let sequence = 0;
const listeners = new Set<(items: Toast[]) => void>();

function emit() {
  for (const listener of listeners) listener(toasts);
}

export function subscribe(listener: (items: Toast[]) => void) {
  listeners.add(listener);
  listener(toasts);
  return () => {
    listeners.delete(listener);
  };
}

export function dismiss(id: number) {
  const item = toasts.find(entry => entry.id === id && !entry.leaving);
  if (!item) return;
  toasts = toasts.map(entry => (entry.id === id ? { ...entry, leaving: true } : entry));
  item.onLeave?.();
  emit();
  setTimeout(() => {
    toasts = toasts.filter(item => item.id !== id);
    emit();
  }, LEAVE_MS);
}

function push(kind: ToastKind, message: string, options: Options = {}) {
  if (!message.trim()) return;
  const { text, code } = splitCode(message);
  const title = options.title ?? TITLE[kind];
  // A toast with its own action or callback stands alone: merging two would drop one of them.
  const merges = !options.action && !options.onLeave;
  const same = merges
    ? toasts.find(
        item => !item.leaving && item.kind === kind && item.message === text && item.title === title,
      )
    : undefined;
  if (same) {
    if (Date.now() - same.at < ECHO_MS) return same.id;
    toasts = toasts.map(item =>
      item === same
        ? {
            ...item,
            code: code ?? item.code,
            count: item.count + 1,
            revision: item.revision + 1,
            at: Date.now(),
          }
        : item,
    );
    emit();
    return same.id;
  }
  const id = ++sequence;
  const duration = options.duration ?? DURATION[kind];
  toasts = [
    ...toasts,
    {
      id,
      kind,
      title,
      message: text,
      code,
      count: 1,
      revision: 0,
      duration,
      leaving: false,
      at: Date.now(),
      action: options.action,
      onLeave: options.onLeave,
    },
  ];
  emit();
  const visible = toasts.filter(item => !item.leaving);
  for (const item of visible.slice(0, Math.max(0, visible.length - MAX_VISIBLE))) dismiss(item.id);
  return id;
}

export const toast = {
  error: (message: string, options?: Options) => push('error', message, options),
  success: (message: string, options?: Options) => push('success', message, options),
  info: (message: string, options?: Options) => push('info', message, options),
};

const UNDO_MS = 6000;

/**
 * A removal the user can take back: the caller hides the item at once, `commit` runs when the
 * toast leaves unless «Отменить» was pressed, and then `restore` shows the item again.
 */
export function undoable(
  message: string,
  { commit, restore, title = 'Удалено' }: { commit: () => unknown; restore: () => void; title?: string },
) {
  let undone = false;
  return push('info', message, {
    title,
    duration: UNDO_MS,
    action: {
      label: 'Отменить',
      run: () => {
        undone = true;
        restore();
      },
    },
    onLeave: () => {
      if (undone) return;
      Promise.resolve()
        .then(commit)
        .catch(error => {
          restore();
          reportError(error);
        });
    },
  });
}

/** Any thrown value as an error notification. */
export function reportError(error: unknown, title?: string) {
  return toast.error(errorText(error), { title });
}

/** For tests: start from an empty stack. */
export function resetToasts() {
  toasts = [];
  emit();
}
