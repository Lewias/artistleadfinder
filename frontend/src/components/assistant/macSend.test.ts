import { describe, expect, it } from 'vitest';
import type { AssistantSend, MacSendState } from '../../services/types';
import { SEND_STATES, awaitingMessages } from './macSend';

const send = (state: MacSendState): AssistantSend => ({
  id: 1,
  draft_id: 1,
  handle: '+15555550123',
  contact_name: 'Jay',
  text: 'yo',
  state,
  note: '',
  confirmed_at: null,
  delivered_at: null,
});

describe('Mac sends', () => {
  it('wait for Messages only until it writes the row', () => {
    expect(awaitingMessages([send('queued')])).toBe(true);
    expect(awaitingMessages([send('delivered'), send('dispatched')])).toBe(true);
    expect(awaitingMessages([send('sent'), send('uncertain'), send('failed')])).toBe(false);
    expect(awaitingMessages([])).toBe(false);
  });

  it('never call an uncertain send sent', () => {
    expect(SEND_STATES.uncertain.tone).toBe('warn');
    expect(SEND_STATES.uncertain.label).not.toMatch(/Отправлено|Доставлено/);
    expect(SEND_STATES.failed.tone).toBe('bad');
  });
});
