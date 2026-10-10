import { useState } from 'react';
import { Radar, Send } from 'lucide-react';
import { Modal } from './Modal';
import { ScoutSettingsPanel } from './ScoutSettingsPanel';
import { OutreachSettingsPanel } from './outreach/OutreachModals';

/** One «Настройки» for the parser and the outreach: what to look for, then who writes and how fast. */
export function WorkSettingsModal({ onClose }: { onClose: () => void }) {
  const [tab, setTab] = useState<'scout' | 'outreach'>('scout');
  return (
    <Modal title="Настройки" wide onClose={onClose}>
      <div className="crm-tabs" role="tablist" aria-label="Настройки">
        <button
          type="button"
          role="tab"
          aria-selected={tab === 'scout'}
          className={tab === 'scout' ? 'active' : ''}
          onClick={() => setTab('scout')}
        >
          <Radar size={14} /> Парсер
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={tab === 'outreach'}
          className={tab === 'outreach' ? 'active' : ''}
          onClick={() => setTab('outreach')}
        >
          <Send size={14} /> Аккаунты и рассылка
        </button>
      </div>
      {tab === 'scout' ? <ScoutSettingsPanel /> : <OutreachSettingsPanel />}
    </Modal>
  );
}
