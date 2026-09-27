export type ProxyScheme = 'none' | 'http' | 'socks5';

export type ProxyForm = {
  scheme: ProxyScheme;
  host: string;
  port: string;
  auth: boolean;
  username: string;
  password: string;
  hasPassword: boolean;
};

export function parseProxyInput(input: string, selected: ProxyScheme): ProxyForm {
  const value = input.trim();
  const match = /^(?:(http|socks5):\/\/)?(?:([^\s/]+)@)?([^:\s/@]+):(\d{1,5})$/i.exec(value);
  if (!match) throw new Error('Формат: socks5://логин:пароль@адрес:порт (или без socks5://).');
  const explicit = match[1]?.toLowerCase() as Exclude<ProxyScheme, 'none'> | undefined;
  const scheme = explicit || (selected === 'none' ? 'socks5' : selected);
  const [, , credentials, host, port] = match;
  if (!/^[A-Za-z0-9.-]+$/.test(host) || host.length > 253 || host.startsWith('.') || host.endsWith('.') || host.includes('..') || host.startsWith('-') || host.endsWith('-')) throw new Error('Некорректный адрес прокси.');
  const number = Number(port);
  if (number < 1 || number > 65535) throw new Error('Порт прокси должен быть от 1 до 65535.');
  const hasControl = (text: string) => [...text].some(char => char.charCodeAt(0) < 32 || char.charCodeAt(0) === 127);
  let username = '';
  let password = '';
  if (credentials !== undefined) {
    const separator = credentials.indexOf(':');
    if (separator < 1 || separator === credentials.length - 1) throw new Error('Укажите логин и пароль перед @.');
    try {
      username = decodeURIComponent(credentials.slice(0, separator));
      password = decodeURIComponent(credentials.slice(separator + 1));
    } catch {
      throw new Error('Некорректное кодирование логина или пароля прокси.');
    }
    if (!username || username.includes(':') || hasControl(username) || new TextEncoder().encode(username).length > 128 || !password || hasControl(password) || new TextEncoder().encode(password).length > 255) throw new Error('Некорректный логин или пароль прокси.');
  }
  return { scheme, host, port, auth: credentials !== undefined, username, password, hasPassword: false };
}
