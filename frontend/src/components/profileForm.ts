import type { BrowserProfile, BrowserProxy } from '../services/types';
import { parseProxyInput, type ProxyForm } from './proxyInput';

export type ProfileForm = { name: string; proxy: ProxyForm; proxyInput: string };
const emptyProxy = (): ProxyForm => ({
  scheme: 'none',
  host: '',
  port: '',
  auth: false,
  username: '',
  password: '',
  hasPassword: false,
});
export const emptyForm = (): ProfileForm => ({ name: '', proxy: emptyProxy(), proxyInput: '' });
export const fromProfile = (profile: BrowserProfile): ProfileForm => ({
  name: profile.name,
  proxy: profile.proxy
    ? {
        scheme: profile.proxy.scheme,
        host: profile.proxy.host,
        port: String(profile.proxy.port),
        auth: Boolean(profile.proxy.username),
        username: profile.proxy.username || '',
        password: '',
        hasPassword: Boolean(profile.proxy.has_password),
      }
    : emptyProxy(),
  proxyInput: '',
});
export function getProxy(draft: ProfileForm): (BrowserProxy & { password?: string }) | null {
  const form = draft.proxyInput.trim() ? parseProxyInput(draft.proxyInput, draft.proxy.scheme) : draft.proxy;
  if (form.scheme === 'none') return null;
  const host = form.host.trim();
  const port = Number(form.port);
  if (
    !/^[A-Za-z0-9.-]+$/.test(host) ||
    host.length > 253 ||
    host.startsWith('.') ||
    host.endsWith('.') ||
    host.includes('..') ||
    host.startsWith('-') ||
    host.endsWith('-')
  )
    throw new Error('Укажите корректный адрес прокси без протокола и логина.');
  if (!Number.isInteger(port) || port < 1 || port > 65535)
    throw new Error('Порт прокси должен быть от 1 до 65535.');
  if (!form.auth) return { scheme: form.scheme, host, port };
  const username = form.username.trim();
  const hasControl = (value: string) =>
    [...value].some(char => char.charCodeAt(0) < 32 || char.charCodeAt(0) === 127);
  if (
    !username ||
    username.includes(':') ||
    hasControl(username) ||
    new TextEncoder().encode(username).length > 128
  )
    throw new Error('Укажите корректный логин прокси.');
  if (!form.password && !form.hasPassword) throw new Error('Укажите пароль прокси.');
  if (form.password && (hasControl(form.password) || new TextEncoder().encode(form.password).length > 255))
    throw new Error('Некорректный пароль прокси.');
  return { scheme: form.scheme, host, port, username, ...(form.password ? { password: form.password } : {}) };
}
