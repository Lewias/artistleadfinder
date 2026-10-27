import type { AssistantSend, MacSendState } from '../../services/types';

/** How a Mac send reads in the list, and its tone. */
export const SEND_STATES: Record<MacSendState, { label: string; tone: 'wait' | 'good' | 'warn' | 'bad' }> = {
  queued: { label: 'Передаю в Сообщения', tone: 'wait' },
  dispatched: { label: 'Передано в Сообщения', tone: 'wait' },
  sent: { label: 'Отправлено', tone: 'good' },
  delivered: { label: 'Доставлено', tone: 'good' },
  uncertain: { label: 'Неизвестно', tone: 'warn' },
  failed: { label: 'Не отправлено', tone: 'bad' },
};

/** A send whose outcome Messages has not written yet: the screen checks more often. */
export const awaitingMessages = (sends: AssistantSend[]) =>
  sends.some(send => send.state === 'queued' || send.state === 'dispatched');
