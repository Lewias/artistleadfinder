import { describe, expect, it } from 'vitest';
import { pages } from './navigation';
describe('desktop navigation contract', () => {
  it('exposes the required sections with distinct identities', () => {
    expect(pages.map(page => page.id)).toEqual([
      'dashboard',
      'discovery',
      'leads',
      'history',
      'profiles',
      'settings',
    ]);
    expect(new Set(pages.map(page => page.id)).size).toBe(6);
  });
});
