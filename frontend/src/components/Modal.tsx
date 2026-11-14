import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';
import { X } from 'lucide-react';

/** Centered dialog over a dimmed page; Escape or the backdrop closes it. */
export function Modal({
  title,
  onClose,
  children,
  wide = false,
  narrow = false,
  icon,
}: {
  title: string;
  onClose: () => void;
  children: ReactNode;
  /** For dense content such as the Scout settings grid. */
  wide?: boolean;
  /** For a compact dialog such as the phone launch window. */
  narrow?: boolean;
  icon?: ReactNode;
}) {
  const root = useRef<HTMLDivElement>(null);
  // Escape, the backdrop and the cross play the exit first; a parent that unmounts the
  // dialog directly (after a save, say) still closes it at once.
  const [closing, setClosing] = useState(false);
  const latest = useRef(onClose);
  useEffect(() => {
    latest.current = onClose;
  }, [onClose]);
  const close = useCallback(() => {
    if (matchMedia('(prefers-reduced-motion: reduce)').matches) latest.current();
    else setClosing(true);
  }, []);
  useEffect(() => {
    if (!closing) return;
    const timer = window.setTimeout(() => latest.current(), 160);
    return () => window.clearTimeout(timer);
  }, [closing]);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    const focusable = () =>
      Array.from(
        root.current?.querySelectorAll<HTMLElement>(
          'button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex="0"]',
        ) || [],
      ).filter(element => element.getClientRects().length > 0);
    focusable()[0]?.focus({ preventScroll: true });
    const onTab = (event: KeyboardEvent) => {
      if (event.key !== 'Tab') return;
      const elements = focusable();
      const first = elements[0];
      const last = elements[elements.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last?.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first?.focus();
      }
    };
    document.addEventListener('keydown', onTab);
    return () => {
      document.removeEventListener('keydown', onTab);
      if (previous?.isConnected) previous.focus({ preventScroll: true });
    };
  }, []);
  useEffect(() => {
    const onKey = (event: globalThis.KeyboardEvent) => event.key === 'Escape' && close();
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [close]);
  return (
    <>
      <div className={`drawer-overlay${closing ? ' is-closing' : ''}`} onClick={close} />
      <div
        ref={root}
        className={['modal', wide && 'wide', narrow && 'narrow', closing && 'is-closing']
          .filter(Boolean)
          .join(' ')}
        role="dialog"
        aria-modal="true"
        aria-label={title}
      >
        <button className="drawer-close" aria-label="Закрыть" onClick={close}>
          <X size={16} />
        </button>
        <h2 className={icon ? 'modal-title-icon' : undefined}>
          {icon}
          {title}
        </h2>
        {children}
      </div>
    </>
  );
}
