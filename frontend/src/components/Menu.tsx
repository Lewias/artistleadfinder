import { useEffect, useRef, useState, type KeyboardEvent, type ReactNode } from 'react';
import { ChevronDown, MoreHorizontal } from 'lucide-react';

/**
 * A menu of actions. Without `text` the trigger is the round «…» button opening upwards; with
 * `text` it is a labelled button whose menu opens downwards, aligned to its right edge.
 * Closes on an outside click or Escape; arrow keys move between the items.
 */
export function Menu({
  label,
  text,
  down = false,
  disabled = false,
  children,
}: {
  label: string;
  text?: ReactNode;
  /** Open the «…» menu downwards too (a labelled menu always does). */
  down?: boolean;
  disabled?: boolean;
  children: (close: () => void) => ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const items = () =>
    Array.from(root.current?.querySelectorAll<HTMLElement>('[role="menuitem"]:not(:disabled)') ?? []);
  const close = (refocus = false) => {
    setOpen(false);
    if (refocus) trigger.current?.focus();
  };
  useEffect(() => {
    if (!open) return;
    items()[0]?.focus();
    const onDown = (event: MouseEvent) => {
      if (!root.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKey = (event: globalThis.KeyboardEvent) => {
      if (event.key === 'Escape') close(true);
    };
    document.addEventListener('mousedown', onDown);
    window.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDown);
      window.removeEventListener('keydown', onKey);
    };
  }, [open]);
  const onMenuKey = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp') return;
    event.preventDefault();
    const list = items();
    const at = list.indexOf(document.activeElement as HTMLElement);
    const step = event.key === 'ArrowDown' ? 1 : -1;
    list[(at + step + list.length) % list.length]?.focus();
  };
  return (
    <div className={`menu-root${text || down ? ' menu-root-down' : ''}`} ref={root}>
      <button
        ref={trigger}
        type="button"
        className={`button button-outline${text ? '' : ' button-icon'}`}
        aria-label={text ? undefined : label}
        title={text ? undefined : label}
        aria-haspopup="menu"
        aria-expanded={open}
        disabled={disabled}
        onClick={() => setOpen(value => !value)}
      >
        {text ?? <MoreHorizontal size={16} />}
        {text && <ChevronDown size={14} />}
      </button>
      {open && (
        <div className="menu" role="menu" aria-label={label} onKeyDown={onMenuKey}>
          {children(() => close(true))}
        </div>
      )}
    </div>
  );
}
