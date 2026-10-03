import { Instagram, Mail, Phone } from 'lucide-react';
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
  channels: [{ kind: crm === 'imessage' ? 'phone' : 'instagram', value: '' }],
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
