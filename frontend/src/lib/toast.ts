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
}
interface Options {
  title?: string;
  duration?: number;
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
  if (!toasts.some(item => item.id === id && !item.leaving)) return;
  toasts = toasts.map(item => (item.id === id ? { ...item, leaving: true } : item));
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
  const same = toasts.find(
    item => !item.leaving && item.kind === kind && item.message === text && item.title === title,
  );
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
    { id, kind, title, message: text, code, count: 1, revision: 0, duration, leaving: false, at: Date.now() },
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

/** Any thrown value as an error notification. */
export function reportError(error: unknown, title?: string) {
  return toast.error(errorText(error), { title });
}

/** For tests: start from an empty stack. */
export function resetToasts() {
  toasts = [];
  emit();
}
