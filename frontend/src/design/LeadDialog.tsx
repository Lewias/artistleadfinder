import * as Dialog from '@radix-ui/react-dialog';
import { X } from 'lucide-react';
import { useRef, type ReactNode } from 'react';

export function LeadDialog({
  title,
  children,
  onClose,
  busy,
}: {
  title: string;
  children: ReactNode;
  onClose: () => void;
  busy: boolean;
}) {
  const content = useRef<HTMLDivElement>(null);
  const previousFocus = useRef(document.activeElement as HTMLElement | null);
  return (
    <Dialog.Root
      open
      onOpenChange={value => {
        if (!value && !busy) onClose();
      }}
    >
      <Dialog.Portal>
        <Dialog.Overlay className="spec-dialog-overlay" />
        <Dialog.Content
          ref={content}
          className="spec-dialog"
          onCloseAutoFocus={e => {
            e.preventDefault();
            previousFocus.current?.focus();
          }}
          onOpenAutoFocus={e => {
            e.preventDefault();
            content.current?.querySelector<HTMLButtonElement>('[data-cancel]')?.focus();
          }}
        >
          <Dialog.Title>{title}</Dialog.Title>
          <Dialog.Description>Удаление артистов из локальной базы</Dialog.Description>
          <Dialog.Close className="spec-dialog-close" disabled={busy} aria-label="Закрыть">
            <X size={18} />
          </Dialog.Close>
          {children}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
