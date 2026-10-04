import { Component, useEffect, useRef, useState, type ErrorInfo, type ReactNode } from 'react';
import { CircleAlert, CircleCheck, Info, RotateCcw, ScrollText, X } from 'lucide-react';
import { api } from '../services/api';
import { errorText } from '../lib/errors';
import { dismiss, reportError, subscribe, toast, type Toast } from '../lib/toast';
import { Button } from './ui/button';

const icons = { error: CircleAlert, success: CircleCheck, info: Info };

function ToastItem({ item }: { item: Toast }) {
  const [paused, setPaused] = useState(false);
  const remaining = useRef(item.duration);
  const started = useRef(0);
  // A repeat of the message starts the timer over.
  useEffect(() => {
    remaining.current = item.duration;
  }, [item.revision, item.duration]);
  useEffect(() => {
    if (paused || item.leaving) return;
    started.current = Date.now();
    const timer = setTimeout(() => dismiss(item.id), remaining.current);
    return () => {
      clearTimeout(timer);
      remaining.current = Math.max(0, remaining.current - (Date.now() - started.current));
    };
  }, [paused, item.leaving, item.id, item.revision]);
  const Icon = icons[item.kind];
  return (
    <div className={`toast-slot${item.leaving ? ' leaving' : ''}`}>
      <div
        className={`toast ${item.kind}${item.leaving ? ' leaving' : ''}`}
        role={item.kind === 'error' ? 'alert' : 'status'}
        onMouseEnter={() => setPaused(true)}
        onMouseLeave={() => setPaused(false)}
        onFocus={() => setPaused(true)}
        onBlur={() => setPaused(false)}
      >
        <span className="toast-icon">
          <Icon size={18} />
        </span>
        <div className="toast-body">
          <strong>
            {item.title}
            {item.count > 1 && (
              <span className="toast-count" key={item.count}>
                ×{item.count}
              </span>
            )}
          </strong>
          <p>{item.message}</p>
          {item.code && (
            <span className="toast-code">
              Код {item.code}
              <button type="button" onClick={() => void api.openLogs().catch(reportError)}>
                <ScrollText size={12} /> Журнал
              </button>
            </span>
          )}
        </div>
        <button
          type="button"
          className="toast-close"
          aria-label="Закрыть уведомление"
          onClick={() => dismiss(item.id)}
        >
          <X size={14} />
        </button>
        <i
          className="toast-timer"
          key={item.revision}
          style={{
            animationDuration: `${item.duration}ms`,
            animationPlayState: paused ? 'paused' : 'running',
          }}
        />
      </div>
    </div>
  );
}

/** Notifications in the bottom right corner; errors of the whole app end up here. */
export function Toaster() {
  const [items, setItems] = useState<Toast[]>([]);
  useEffect(() => subscribe(setItems), []);
  // Errors nobody caught still reach the user instead of vanishing in the console.
  useEffect(() => {
    const onRejection = (event: PromiseRejectionEvent) => {
      event.preventDefault();
      reportError(event.reason);
    };
    const onError = (event: ErrorEvent) => {
      if (/ResizeObserver loop/.test(event.message)) return;
      reportError(event.error ?? event.message);
    };
    window.addEventListener('unhandledrejection', onRejection);
    window.addEventListener('error', onError);
    return () => {
      window.removeEventListener('unhandledrejection', onRejection);
      window.removeEventListener('error', onError);
    };
  }, []);
  return (
    <div className="toaster" aria-live="polite">
      {items.map(item => (
        <ToastItem key={item.id} item={item} />
      ))}
    </div>
  );
}

/** Shows an error state as a notification each time it gets a new text. */
export function ErrorToast({ message, title }: { message?: string | null; title?: string }) {
  useEffect(() => {
    if (message) toast.error(message, { title });
  }, [message, title]);
  return null;
}

/** A section that failed to render shows a retry instead of a blank window. */
export class ErrorBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  componentDidCatch(error: Error, info: ErrorInfo) {
    toast.error(errorText(error), { title: 'Раздел не удалось показать' });
    console.error(error, info.componentStack);
  }
  render() {
    if (!this.state.failed) return this.props.children;
    return (
      <div className="panel crash-panel">
        <CircleAlert size={22} />
        <h2>Раздел не удалось показать</h2>
        <p className="helper">Данные не пострадали. Попробуйте открыть раздел снова.</p>
        <Button variant="outline" onClick={() => this.setState({ failed: false })}>
          <RotateCcw size={15} /> Повторить
        </Button>
      </div>
    );
  }
}
