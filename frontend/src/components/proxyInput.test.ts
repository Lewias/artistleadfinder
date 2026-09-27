import { describe, expect, it } from 'vitest';
import { parseProxyInput } from './proxyInput';

describe('proxy quick entry', () => {
  it('uses an explicit scheme and separates credentials', () => {
    expect(parseProxyInput('socks5://example-user:example-password@82.23.165.83:59101', 'none')).toEqual({
      scheme: 'socks5', host: '82.23.165.83', port: '59101', auth: true,
      username: 'example-user', password: 'example-password', hasPassword: false,
    });
  });

  it('accepts shop-style credentials without a prefix and defaults to SOCKS5', () => {
    expect(parseProxyInput('example-user:example-password@82.23.165.83:59101', 'none').scheme).toBe('socks5');
    expect(parseProxyInput('example-user:example-password@82.23.165.83:59101', 'http').scheme).toBe('http');
    expect(parseProxyInput('http://example-user:example-password@proxy.example.com:8080', 'none').scheme).toBe('http');
    expect(parseProxyInput('proxy.example.com:8080', 'http').auth).toBe(false);
  });

  it('decodes percent-encoded credentials and preserves colons in passwords', () => {
    expect(parseProxyInput('socks5://user:p%40ss%3Aword@proxy.example.com:1080', 'none').password).toBe('p@ss:word');
  });

  it('rejects malformed ports and incomplete credentials without echoing secrets', () => {
    for (const input of ['82.23.165.83:59101:user:pass', 'socks5://user:pass@host:99999', 'socks5://user@host:8080', 'https://user:pass@host:8080']) {
      expect(() => parseProxyInput(input, 'none'), input).toThrow();
    }
  });
});
