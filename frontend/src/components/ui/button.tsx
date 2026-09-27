import type { ButtonHTMLAttributes } from 'react';

type Props = ButtonHTMLAttributes<HTMLButtonElement> & { variant?: 'default' | 'outline' };
export function Button({ className, variant = 'default', ...props }: Props) {
  return <button className={['button', variant === 'outline' ? 'button-outline' : 'button-primary', className].filter(Boolean).join(' ')} {...props} />;
}
