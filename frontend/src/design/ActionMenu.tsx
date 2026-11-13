import { useEffect, useRef, useState, type ReactNode } from 'react';
import { ChevronDown } from 'lucide-react';

/** Specimen-only menu: managed keyboard focus and return to its trigger. */
export function ActionMenu({
  label,
  children,
}: {
  label: string;
  children: (close: () => void) => ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const close = () => {
    setOpen(false);
    trigger.current?.focus();
  };
  useEffect(() => {
    if (!open) return;
    root.current?.querySelector<HTMLButtonElement>('[role="menu"] button:not(:disabled)')?.focus();
    const outside = (e: PointerEvent) => {
      if (!root.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('pointerdown', outside);
    return () => document.removeEventListener('pointerdown', outside);
  }, [open]);
  return (
    <div
      className="spec-action-menu"
      ref={root}
      onKeyDown={e => {
        if (e.key === 'Escape') {
          e.stopPropagation();
          close();
        }
        if (e.key === 'Tab') setOpen(false);
        if (!open || !['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(e.key)) return;
        e.preventDefault();
        const items = Array.from(
          root.current?.querySelectorAll<HTMLButtonElement>('[role="menu"] button:not(:disabled)') || [],
        );
        const index = items.indexOf(document.activeElement as HTMLButtonElement);
        const next =
          e.key === 'Home'
            ? 0
            : e.key === 'End'
              ? items.length - 1
              : (index + (e.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length;
        items[next]?.focus();
      }}
    >
      <button
        ref={trigger}
        type="button"
        className="button button-outline"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
        onKeyDown={e => {
          if (e.key === 'ArrowDown') {
            e.preventDefault();
            setOpen(true);
          }
        }}
      >
        {label}
        <ChevronDown size={14} />
      </button>
      {open && (
        <div className="spec-menu-items" role="menu" aria-label={label}>
          {children(close)}
        </div>
      )}
    </div>
  );
}
