import {
  forwardRef,
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  useState,
  type ButtonHTMLAttributes,
  type InputHTMLAttributes,
  type ReactNode,
} from 'react';
import { createPortal } from 'react-dom';
import { Check, ChevronDown, Search } from 'lucide-react';
import { Button } from './button';

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(
  function Input(props, ref) {
    return <input {...props} ref={ref} className={`alf-input ${props.className || ''}`} />;
  },
);
export function SearchField(props: InputHTMLAttributes<HTMLInputElement>) {
  return (
    <label className="alf-search">
      <Search aria-hidden="true" />
      <Input {...props} type="search" />
    </label>
  );
}
export function IconButton({
  label,
  children,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { label: string }) {
  return (
    <Tooltip text={label}>
      <Button {...props} icon variant="outline" aria-label={label}>
        {children}
      </Button>
    </Tooltip>
  );
}
export function Tooltip({ text, children }: { text: string; children: ReactNode }) {
  const id = useId();
  return (
    <span className="alf-tooltip-host" aria-describedby={id}>
      {children}
      <span className="alf-tooltip" id={id} role="tooltip">
        {text}
      </span>
    </span>
  );
}
export function Checkbox({ label, ...props }: InputHTMLAttributes<HTMLInputElement> & { label: string }) {
  return (
    <label className="alf-checkbox">
      <input {...props} type="checkbox" aria-label={label} />
      <Check aria-hidden="true" />
    </label>
  );
}
export function Switch({
  label,
  checked,
  onChange,
  disabled,
}: {
  label: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      className="alf-switch"
      role="switch"
      aria-label={label}
      aria-checked={checked}
      disabled={disabled}
      onClick={() => onChange(!checked)}
    >
      <span />
    </button>
  );
}
export function StatusIndicator({
  children,
  tone = 'neutral',
}: {
  children: ReactNode;
  tone?: 'neutral' | 'positive' | 'warning' | 'danger';
}) {
  return (
    <span className={`alf-status alf-status-${tone}`}>
      <i aria-hidden="true" />
      {children}
    </span>
  );
}
export function SectionHeader({
  title,
  icon,
  trailing,
}: {
  title: string;
  icon?: ReactNode;
  trailing?: ReactNode;
}) {
  return (
    <header className="alf-section-header">
      <span>
        {icon}
        <span>{title}</span>
      </span>
      {trailing}
    </header>
  );
}
export function Panel({
  title,
  icon,
  children,
  className = '',
  trailing,
}: {
  title: string;
  icon?: ReactNode;
  children: ReactNode;
  className?: string;
  trailing?: ReactNode;
}) {
  return (
    <section className={`alf-panel ${className}`}>
      <SectionHeader title={title} icon={icon} trailing={trailing} />
      <div className="alf-panel-body">{children}</div>
    </section>
  );
}
export function NavigationItem({
  active,
  children,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { active?: boolean }) {
  return (
    <button
      {...props}
      type="button"
      className={`nav-item${active ? ' active' : ''}`}
      aria-current={active ? 'page' : undefined}
    >
      {children}
    </button>
  );
}
export function Tabs({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: string;
  options: { value: string; label: string }[];
  onChange: (value: string) => void;
}) {
  const root = useRef<HTMLDivElement>(null);
  return (
    <div className="alf-tabs" role="tablist" aria-label={label} ref={root}>
      {options.map((option, index) => (
        <button
          key={option.value}
          type="button"
          role="tab"
          aria-selected={value === option.value}
          tabIndex={value === option.value ? 0 : -1}
          onClick={() => onChange(option.value)}
          onKeyDown={event => {
            if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
            event.preventDefault();
            const next =
              event.key === 'Home'
                ? 0
                : event.key === 'End'
                  ? options.length - 1
                  : (index + (event.key === 'ArrowRight' ? 1 : -1) + options.length) % options.length;
            onChange(options[next].value);
            root.current?.querySelectorAll('button')[next].focus();
          }}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}
/** Custom presentation with keyboard/typeahead behavior; values remain existing RPC values. */
export function Dropdown({
  label,
  value,
  options,
  onChange,
  disabled,
  className = '',
}: {
  label: string;
  value: string;
  options: { value: string; label: string }[];
  onChange: (value: string) => void;
  disabled?: boolean;
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  const [index, setIndex] = useState(0);
  const [position, setPosition] = useState({ left: 0, top: 0, width: 0 });
  const trigger = useRef<HTMLButtonElement>(null);
  const list = useRef<HTMLDivElement>(null);
  const id = useId();
  const typeahead = useRef({ text: '', at: 0 });
  const show = () => {
    const rect = trigger.current!.getBoundingClientRect();
    const tokens = getComputedStyle(document.documentElement);
    const gap = parseFloat(tokens.getPropertyValue('--alf-space-2'));
    const width = Math.max(rect.width, parseFloat(tokens.getPropertyValue('--alf-menu-width')));
    const height = parseFloat(tokens.getPropertyValue('--alf-menu-height'));
    setPosition({
      left: Math.max(gap, Math.min(rect.left, innerWidth - width - gap)),
      top: Math.max(gap, Math.min(rect.bottom + gap, innerHeight - height - gap)),
      width,
    });
    setIndex(
      Math.max(
        0,
        options.findIndex(o => o.value === value),
      ),
    );
    setOpen(true);
  };
  useEffect(() => {
    if (!open) return;
    const outside = (event: PointerEvent) => {
      if (!trigger.current?.contains(event.target as Node) && !list.current?.contains(event.target as Node))
        setOpen(false);
    };
    const close = (event: Event) => {
      if (!list.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener('pointerdown', outside);
    window.addEventListener('resize', close);
    window.addEventListener('scroll', close, true);
    return () => {
      document.removeEventListener('pointerdown', outside);
      window.removeEventListener('resize', close);
      window.removeEventListener('scroll', close, true);
    };
  }, [open]);
  useEffect(() => {
    if (open) list.current?.children[index]?.scrollIntoView({ block: 'nearest' });
  }, [index, open]);
  return (
    <>
      <button
        ref={trigger}
        type="button"
        className={`alf-dropdown ${className}`}
        disabled={disabled}
        role="combobox"
        aria-label={label}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? id : undefined}
        aria-activedescendant={open ? `${id}-${index}` : undefined}
        onClick={() => (open ? setOpen(false) : show())}
        onKeyDown={event => {
          if (event.key === 'Tab') {
            setOpen(false);
            return;
          }
          if (event.key === 'Escape') {
            event.preventDefault();
            event.stopPropagation();
            setOpen(false);
            return;
          }
          if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) {
            event.preventDefault();
            if (!open) show();
            else
              setIndex(i =>
                event.key === 'Home'
                  ? 0
                  : event.key === 'End'
                    ? options.length - 1
                    : (i + (event.key === 'ArrowDown' ? 1 : -1) + options.length) % options.length,
              );
          }
          if (open && ['Enter', ' '].includes(event.key)) {
            event.preventDefault();
            onChange(options[index].value);
            setOpen(false);
          }
          if (open && event.key.length === 1 && event.key !== ' ') {
            const now = Date.now();
            typeahead.current.text =
              (now - typeahead.current.at < 700 ? typeahead.current.text : '') + event.key.toLowerCase();
            typeahead.current.at = now;
            const match = options.findIndex(o => o.label.toLowerCase().startsWith(typeahead.current.text));
            if (match >= 0) setIndex(match);
          }
        }}
      >
        <span>{options.find(option => option.value === value)?.label || label}</span>
        <ChevronDown aria-hidden="true" />
      </button>
      {open &&
        createPortal(
          <div
            className="alf-dropdown-menu"
            id={id}
            ref={list}
            role="listbox"
            aria-label={label}
            style={position}
          >
            {options.map((option, i) => (
              <div
                key={option.value}
                id={`${id}-${i}`}
                role="option"
                aria-selected={value === option.value}
                className={index === i ? 'highlighted' : ''}
                onPointerMove={() => setIndex(i)}
                onMouseDown={e => e.preventDefault()}
                onClick={() => {
                  onChange(option.value);
                  setOpen(false);
                  trigger.current?.focus();
                }}
              >
                <span>{option.label}</span>
                {value === option.value && <Check aria-hidden="true" />}
              </div>
            ))}
          </div>,
          trigger.current?.closest('.drawer[role="dialog"]') || document.body,
        )}
    </>
  );
}

/** Sidebar disclosure group. The list folds by animating its grid row (0fr ↔ 1fr), and a
 * marker slides along the group's guide line to the active item. */
export function NavGroup({
  title,
  icon,
  children,
  label,
  contentKey,
}: {
  title: string;
  icon: ReactNode;
  children: ReactNode;
  label: string;
  /** A new key replays the items' entrance, e.g. when the channel changes. */
  contentKey?: string;
}) {
  const [open, setOpen] = useState(true);
  const list = useRef<HTMLElement>(null);
  const body = useId();
  useLayoutEffect(() => {
    const nav = list.current;
    if (!nav) return;
    const place = () => {
      const active = nav.querySelector<HTMLElement>('.nav-item.active');
      nav.style.setProperty('--marker-y', `${active?.offsetTop ?? 0}px`);
      nav.style.setProperty('--marker-h', `${active?.offsetHeight ?? 0}px`);
      nav.classList.toggle('has-active', !!active);
    };
    place();
    const observer = new MutationObserver(place);
    observer.observe(nav, { subtree: true, childList: true, attributes: true, attributeFilter: ['class'] });
    return () => observer.disconnect();
  }, [contentKey]);
  return (
    <div className={`alf-nav-group${open ? ' is-open' : ''}`}>
      <button
        type="button"
        className="alf-nav-summary"
        aria-expanded={open}
        aria-controls={body}
        title={title}
        onClick={() => setOpen(value => !value)}
      >
        {icon}
        <span>{title}</span>
        <ChevronDown className="alf-nav-chevron" aria-hidden="true" />
      </button>
      <div className="alf-collapse" id={body} inert={!open}>
        <div className="alf-collapse-inner">
          <nav aria-label={label} ref={list} key={contentKey}>
            <span className="alf-nav-marker" aria-hidden="true" />
            {children}
          </nav>
        </div>
      </div>
    </div>
  );
}

const TAB_LISTS = '.crm-tabs, .segmented, .alf-tabs, .emoji-tabs';
const TAB_SELECTED =
  ':scope > [aria-selected="true"], :scope > [aria-checked="true"], :scope > .active, :scope > button.selected';

/** One observer for every tab strip and segmented control in the app: it measures the selected
 * button and hands its box to CSS, which slides a shared highlight under it. Pages keep their
 * markup; a strip without a selection simply shows no highlight. */
export function useTabIndicators() {
  useEffect(() => {
    let frame = 0;
    const update = () => {
      frame = 0;
      document.querySelectorAll<HTMLElement>(TAB_LISTS).forEach(strip => {
        const selected = strip.querySelector<HTMLElement>(TAB_SELECTED);
        if (!selected || !selected.offsetWidth) {
          strip.classList.remove('has-indicator');
          return;
        }
        const next = `${selected.offsetLeft},${selected.offsetTop},${selected.offsetWidth},${selected.offsetHeight}`;
        if (strip.dataset.indicator === next) return;
        strip.dataset.indicator = next;
        strip.style.setProperty('--tab-x', `${selected.offsetLeft}px`);
        strip.style.setProperty('--tab-y', `${selected.offsetTop}px`);
        strip.style.setProperty('--tab-w', `${selected.offsetWidth}px`);
        strip.style.setProperty('--tab-h', `${selected.offsetHeight}px`);
        if (!strip.classList.contains('has-indicator')) {
          // The first placement lands without travel; later changes slide.
          strip.classList.add('has-indicator');
          requestAnimationFrame(() => strip.classList.add('indicator-ready'));
        }
      });
    };
    const schedule = () => {
      if (!frame) frame = requestAnimationFrame(update);
    };
    const observer = new MutationObserver(schedule);
    observer.observe(document.body, {
      subtree: true,
      childList: true,
      attributes: true,
      attributeFilter: ['aria-selected', 'aria-checked', 'class'],
    });
    const resize = new ResizeObserver(schedule);
    resize.observe(document.body);
    schedule();
    return () => {
      observer.disconnect();
      resize.disconnect();
      if (frame) cancelAnimationFrame(frame);
    };
  }, []);
}
