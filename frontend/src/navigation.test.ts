import { describe, expect, it } from 'vitest';
import { pages } from './navigation';
describe('desktop navigation contract', () => {
  it('exposes the required sections with distinct identities', () => {
    expect(pages.map(page => page.id)).toEqual([
      'discovery',
      'outreach',
      'profiles',
      'leads',
      'history',
      'dashboard',
      'crm',
      'imessage',
      'imessage-log',
      'imessage-crm',
      'settings',
    ]);
    expect(pages.filter(page => page.placement === 'footer').map(page => page.id)).toEqual(['settings']);
    expect(new Set(pages.map(page => page.id)).size).toBe(11);
  });
  it('splits the main sections between the two channels', () => {
    const main = (channel: string) =>
      pages.filter(page => page.placement === 'main' && page.channel === channel).map(page => page.id);
    expect(main('imessage')).toEqual(['imessage', 'imessage-log', 'imessage-crm']);
    expect(main('instagram')).toHaveLength(7);
    // CRM closes the list of each channel.
    expect(main('instagram').at(-1)).toBe('crm');
  });
});
