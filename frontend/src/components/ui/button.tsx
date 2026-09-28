import type { ButtonHTMLAttributes } from 'react';

type Variant = 'default' | 'outline' | 'violet' | 'green' | 'danger';
type Props = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: Variant;
  /** Round icon-only button; pass an aria-label. */
  icon?: boolean;
};
const variants: Record<Variant, string> = {
  default: 'button-primary',
  outline: 'button-outline',
  violet: 'button-violet',
  green: 'button-green',
  danger: 'button-danger',
};
export function Button({ className, variant = 'default', icon = false, ...props }: Props) {
  return (
    <button
      className={['button', variants[variant], icon && 'button-icon', className].filter(Boolean).join(' ')}
      {...props}
    />
  );
}
