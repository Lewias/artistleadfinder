import { createLucideIcon, Mail, Phone } from 'lucide-react';

// Lucide 1.x dropped brand icons; this is the outline its 0.x versions drew for Instagram.
const Instagram = createLucideIcon('instagram', [
  ['rect', { width: '20', height: '20', x: '2', y: '2', rx: '5', ry: '5', key: '2e1cvw' }],
  ['path', { d: 'M16 11.37A4 4 0 1 1 12.63 8 4 4 0 0 1 16 11.37z', key: '9exkf1' }],
  ['line', { x1: '17.5', x2: '17.51', y1: '6.5', y2: '6.5', key: 'r4j83e' }],
]);
import type { CrmChannel, CrmChannelKind, CrmContact, CrmId } from '../../services/types';
import { dateInput } from './crmText';

export const kindIcons: Record<CrmChannelKind, typeof Mail> = {
  instagram: Instagram,
  email: Mail,
  phone: Phone,
};

export interface ContactDraft {
  id?: number;
  name: string;
  channels: CrmChannel[];
  statuses: string[];
  notes: string;
  last_contact_at: string;
  next_action: string;
  next_action_at: string;
  earned: string;
  potential: string;
}

export const emptyDraft = (crm: CrmId): ContactDraft => ({
  name: '',
  channels: [{ kind: crm === 'imessage' ? 'phone' : crm === 'email' ? 'email' : 'instagram', value: '' }],
  statuses: [],
  notes: '',
  last_contact_at: '',
  next_action: '',
  next_action_at: '',
  earned: '',
  potential: '',
});

export const draftOf = (contact: CrmContact): ContactDraft => ({
  id: contact.id,
  name: contact.name,
  channels: contact.channels.length ? contact.channels : [{ kind: 'instagram', value: '' }],
  statuses: contact.statuses,
  notes: contact.notes,
  last_contact_at: dateInput(contact.last_contact_at),
  next_action: contact.next_action,
  next_action_at: dateInput(contact.next_action_at),
  earned: contact.earned ? String(contact.earned) : '',
  potential: contact.potential ? String(contact.potential) : '',
});
