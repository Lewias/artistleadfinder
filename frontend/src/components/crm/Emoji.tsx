/// <reference types="vite/client" />
import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { SmilePlus } from 'lucide-react';
import { emojiGroups } from './emojiSet';

// Apple images of the picker set, so the emoji look the same on Windows and macOS.
const files = import.meta.glob<string>('../../assets/emoji/*.png', {
  eager: true,
  import: 'default',
  query: '?url',
});
const images = new Map(
  Object.entries(files).map(([path, url]) => [path.slice(path.lastIndexOf('/') + 1, -4), url]),
);

/** File key of an emoji: its code points without the FE0F variation selector. */
const emojiKey = (emoji: string) =>
  [...emoji]
    .map(char => char.codePointAt(0)!.toString(16))
    .filter(code => code !== 'fe0f')
    .join('-');

/** The Apple image of an emoji from the set; any other emoji as text. */
export function Emoji({ emoji, size = 14 }: { emoji: string; size?: number }) {
  const url = images.get(emojiKey(emoji));
  if (!url)
    return (
      <span className="emoji" style={{ fontSize: size }}>
        {emoji}
      </span>
    );
  return <img className="emoji" src={url} alt={emoji} width={size} height={size} draggable={false} />;
}

/** A button with the chosen emoji that opens the Apple emoji set; «Без эмодзи» clears it. */
export function EmojiPicker({
  value,
  label,
  onChange,
}: {
  value: string;
  label: string;
  onChange: (emoji: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [group, setGroup] = useState(0);
  // The modal scrolls and is transformed, so the popover lives in <body>, fixed to the window.
  const [place, setPlace] = useState<{ top: number; left: number }>();
  const button = useRef<HTMLButtonElement>(null);
  const menu = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = () => setOpen(false);
    const onDown = (event: MouseEvent) => {
      const target = event.target as Node;
      if (!menu.current?.contains(target) && !button.current?.contains(target)) close();
    };
    // Captured on the document, so Escape closes the picker and not the modal under it.
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      event.stopPropagation();
      close();
    };
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey, true);
    window.addEventListener('resize', close);
    return () => {
      document.removeEventListener('mousedown', onDown);
      document.removeEventListener('keydown', onKey, true);
      window.removeEventListener('resize', close);
    };
  }, [open]);
  const toggle = () => {
    const rect = button.current?.getBoundingClientRect();
    if (rect) {
      const top = rect.bottom + 6 + 330 > window.innerHeight ? rect.top - 336 : rect.bottom + 6;
      setPlace({ top: Math.max(8, top), left: Math.min(rect.left, window.innerWidth - 320) });
    }
    setOpen(current => !current);
  };
  const pick = (emoji: string) => {
    onChange(emoji);
    setOpen(false);
  };
  return (
    <>
      <button
        ref={button}
        type="button"
        className={`emoji-button${value ? ' set' : ''}`}
        aria-label={`Эмодзи статуса ${label}`}
        title={value ? 'Сменить эмодзи' : 'Добавить эмодзи'}
        aria-expanded={open}
        onClick={toggle}
      >
        {value ? <Emoji emoji={value} size={18} /> : <SmilePlus size={16} />}
      </button>
      {open &&
        place &&
        createPortal(
          <div className="emoji-menu" ref={menu} role="dialog" aria-label="Эмодзи" style={place}>
            <div className="emoji-tabs" role="tablist">
              {emojiGroups.map((item, index) => (
                <button
                  key={item.name}
                  type="button"
                  role="tab"
                  aria-selected={group === index}
                  aria-label={item.name}
                  title={item.name}
                  className={group === index ? 'active' : undefined}
                  onClick={() => setGroup(index)}
                >
                  <Emoji emoji={item.items[0]} size={18} />
                </button>
              ))}
            </div>
            <strong className="emoji-group-name">{emojiGroups[group].name}</strong>
            <div className="emoji-grid">
              {emojiGroups[group].items.map(emoji => (
                <button
                  key={emoji}
                  type="button"
                  aria-label={emoji}
                  className={emoji === value ? 'active' : undefined}
                  onClick={() => pick(emoji)}
                >
                  <Emoji emoji={emoji} size={22} />
                </button>
              ))}
            </div>
            {value && (
              <button type="button" className="emoji-clear" onClick={() => pick('')}>
                Без эмодзи
              </button>
            )}
          </div>,
          document.body,
        )}
    </>
  );
}
