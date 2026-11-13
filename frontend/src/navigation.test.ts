import { describe, expect, it } from 'vitest';
import { pages } from './navigation';
describe('desktop navigation contract', () => {
  it('exposes the required sections with distinct identities', () => {
    expect(pages.map(page => page.id)).toEqual([
      'discovery',
      'spotify',
      'profiles',
      'leads',
      'history',
      'dashboard',
      'crm',
      'crm-users',
      'imessage',
      'imessage-templates',
      'imessage-log',
      'imessage-dialogs',
      'imessage-crm',
      'imessage-crm-users',
      'email',
      'email-accounts',
      'email-warmup',
      'email-crm',
      'assistant',
      'admin',
      'settings',
    ]);
    expect(pages.filter(page => page.placement === 'footer').map(page => page.id)).toEqual([
      'assistant',
      'admin',
      'settings',
    ]);
    expect(new Set(pages.map(page => page.id)).size).toBe(21);
  });
  it('splits the main sections between the three channels', () => {
    const main = (channel: string) =>
      pages.filter(page => page.placement === 'main' && page.channel === channel).map(page => page.id);
    expect(main('imessage')).toEqual([
      'imessage',
      'imessage-templates',
      'imessage-log',
      'imessage-dialogs',
      'imessage-crm',
      'imessage-crm-users',
    ]);
    expect(main('email')).toEqual(['email', 'email-accounts', 'email-warmup', 'email-crm']);
    expect(main('instagram')).toHaveLength(8);
    // CRM and the other users' CRM close the list of each channel.
    expect(main('instagram').slice(-2)).toEqual(['crm', 'crm-users']);
  });
});
