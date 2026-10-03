import { Download, LoaderCircle, Pause, Play, Send, Smartphone, Square } from 'lucide-react';
import type { IMessageState } from '../../services/types';
import { number } from '../../lib/format';
import { Button } from '../ui/button';
import { Modal } from '../Modal';
import { QrCode } from './QrCode';

export type PhoneStage = 'starting' | 'ready' | 'error';

/** «Рассылка через телефон»: the bridge status, progress, the launch QR and setup help. */
export function PhoneModal({
  state,
  stage,
  error,
  busy,
  onControl,
  onSave,
  onClose,
}: {
  state: IMessageState | undefined;
  stage: PhoneStage;
  error: string;
  busy: boolean;
  onControl: (action: 'pause' | 'resume' | 'stop') => void;
  onSave: () => void;
  onClose: () => void;
}) {
  const campaign = state?.campaign;
  const bridge = state?.bridge;
  const counts = campaign?.counts;
  const total = campaign?.total ?? 0;
  const done = counts?.execution_acknowledged ?? 0;
  const percent = total ? Math.round((done / total) * 100) : 0;
  const link = campaign && state?.active ? bridge?.deep_links?.[campaign.protocol] : undefined;
  const lastSeen = bridge?.last_seen?.at ? new Date(bridge.last_seen.at).getTime() : 0;
  const started = campaign ? new Date(campaign.created_at).getTime() : 0;
  const phoneStarted =
    !!counts && (counts.issued > 0 || done > 0 || counts.uncertain > 0 || lastSeen >= started);

  let title = 'Ожидание старта с iPhone…';
  let spinning = true;
  let tone = '';
  if (stage === 'starting') title = 'Запуск моста…';
  else if (stage === 'error') {
    title = 'Не удалось запустить';
    spinning = false;
    tone = 'bad';
  } else if (campaign?.status === 'paused') {
    title = 'Пауза';
    spinning = false;
  } else if (campaign?.status === 'finished') {
    title = 'Готово';
    spinning = false;
    tone = 'good';
  } else if (campaign?.status === 'stopped') {
    title = 'Остановлено';
    spinning = false;
  } else if (phoneStarted) title = 'Идёт отправка с iPhone…';

  return (
    <Modal title="Рассылка через телефон" narrow icon={<Smartphone size={17} />} onClose={onClose}>
      <div className={`phone-status ${tone}`} role="status">
        {spinning ? <LoaderCircle size={18} className="spin" /> : <Send size={16} />}
        <div>
          <strong>{title}</strong>
          <span>
            {stage === 'error'
              ? error
              : `Выполнено на iPhone: ${number(done)} из ${number(total)} (${percent}%)`}
            {stage !== 'error' && counts && counts.uncertain > 0 && ` · неизвестно ${counts.uncertain}`}
          </span>
        </div>
      </div>
      <div className="phone-progress" aria-hidden="true">
        <i style={{ width: `${percent}%` }} />
      </div>

      {link ? (
        <div className="phone-qr">
          <QrCode value={link} size={176} label="QR-код запуска команды" />
          <strong>Наведите камеру iPhone на QR-код</strong>
          <span>iPhone и ПК должны быть в одной сети Wi-Fi</span>
          {bridge?.ip && (
            <span className="phone-address">
              Мост: {bridge.ip}:{bridge.port}
            </span>
          )}
        </div>
      ) : (
        stage !== 'error' && <div className="phone-qr-placeholder" />
      )}

      {state?.active && (
        <div className="actions phone-controls">
          {campaign?.status === 'running' ? (
            <Button variant="outline" disabled={busy} onClick={() => onControl('pause')}>
              <Pause size={14} /> Пауза
            </Button>
          ) : (
            <Button variant="outline" disabled={busy} onClick={() => onControl('resume')}>
              <Play size={14} /> Продолжить
            </Button>
          )}
          <Button variant="outline" disabled={busy} onClick={() => onControl('stop')}>
            <Square size={14} /> Остановить
          </Button>
        </div>
      )}

      <div className="phone-help">
        <strong>
          <Send size={14} /> Команда ещё не установлена на iPhone?
        </strong>
        <p>
          Сохраните файл, перешлите его на iPhone (AirDrop, iCloud Drive, Telegram) и откройте — iPhone
          предложит добавить команду «{state?.workspace.legacy_shortcut_name}».
        </p>
        <Button variant="outline" onClick={onSave}>
          <Download size={15} /> Сохранить файл команды
        </Button>
      </div>
    </Modal>
  );
}
