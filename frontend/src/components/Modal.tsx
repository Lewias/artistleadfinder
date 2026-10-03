import { useEffect, type ReactNode } from 'react';
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
  useEffect(() => {
    const onKey = (event: globalThis.KeyboardEvent) => event.key === 'Escape' && onClose();
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);
  return (
    <>
      <div className="drawer-overlay" onClick={onClose} />
      <div
        className={['modal', wide && 'wide', narrow && 'narrow'].filter(Boolean).join(' ')}
        role="dialog"
        aria-modal="true"
        aria-label={title}
      >
        <button className="drawer-close" aria-label="Закрыть" onClick={onClose}>
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
