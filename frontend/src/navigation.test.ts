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
      'crm-users',
      'imessage',
      'imessage-templates',
      'imessage-log',
      'imessage-crm',
      'imessage-crm-users',
      'admin',
      'settings',
    ]);
    expect(pages.filter(page => page.placement === 'footer').map(page => page.id)).toEqual([
      'admin',
      'settings',
    ]);
    expect(new Set(pages.map(page => page.id)).size).toBe(15);
  });
  it('splits the main sections between the two channels', () => {
    const main = (channel: string) =>
      pages.filter(page => page.placement === 'main' && page.channel === channel).map(page => page.id);
    expect(main('imessage')).toEqual([
      'imessage',
      'imessage-templates',
      'imessage-log',
      'imessage-crm',
      'imessage-crm-users',
    ]);
    expect(main('instagram')).toHaveLength(8);
    // CRM and the other users' CRM close the list of each channel.
    expect(main('instagram').slice(-2)).toEqual(['crm', 'crm-users']);
  });
});
