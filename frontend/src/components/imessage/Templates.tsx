import { ErrorToast } from '../Toaster';
import { useState } from 'react';
import { open } from '@tauri-apps/plugin-dialog';
import {
  ArrowDown,
  ArrowUp,
  CircleAlert,
  LayoutTemplate,
  Paperclip,
  Plus,
  Save,
  SquarePen,
  Trash2,
  X,
} from 'lucide-react';
import { api } from '../../services/api';
import { useResource } from '../../hooks/useResource';
import type { IMessageAttachment, IMessagePart, IMessageTemplate } from '../../services/types';
import { Button } from '../ui/button';
import { Modal } from '../Modal';
import { errorText } from '../accountRuns';
import { ATTACHMENT_EXTENSIONS, chainPlan, fileSize, partsPayload, planText } from './imessageText';

/** Files picked for a template or a chain message: stored by the core, not yet used. */
async function pickFiles(): Promise<IMessageAttachment[]> {
  const path = await open({
    title: 'Файлы для сообщения',
    multiple: true,
    filters: [{ name: 'Фото, видео, аудио, PDF', extensions: ATTACHMENT_EXTENSIONS }],
  });
  const added: IMessageAttachment[] = [];
  for (const item of !path ? [] : Array.isArray(path) ? path : [path]) {
    const result = await api.request<{ attachment: IMessageAttachment }>('imessage.attachment_add', {
      path: item,
      target: 'file',
    });
    added.push(result.attachment);
  }
  return added;
}

/** Ordered messages of text and/or files. Text is committed on blur, the rest at once. */
export function PartsEditor({
  parts,
  busy,
  onChange,
  onCommit,
  onError,
}: {
  parts: IMessagePart[];
  busy?: boolean;
  onChange: (parts: IMessagePart[]) => void;
  /** Called after a change that should be saved right away (files, order, removal). */
  onCommit?: (parts: IMessagePart[]) => void;
  onError: (message: string) => void;
}) {
  const [uploading, setUploading] = useState<number | null>(null);
  const change = (next: IMessagePart[], commit = true) => {
    onChange(next);
    if (commit) onCommit?.(next);
  };
  const update = (index: number, patch: Partial<IMessagePart>, commit = true) =>
    change(
      parts.map((part, at) => (at === index ? { ...part, ...patch } : part)),
      commit,
    );
  const move = (index: number, by: number) => {
    const next = [...parts];
    [next[index], next[index + by]] = [next[index + by], next[index]];
    change(next);
  };
  const addFiles = async (index: number) => {
    setUploading(index);
    try {
      const added = await pickFiles();
      if (added.length) update(index, { attachments: [...parts[index].attachments, ...added] });
    } catch (err) {
      onError(errorText(err));
    } finally {
      setUploading(null);
    }
  };
  return (
    <div className="parts-editor">
      {parts.map((part, index) => (
        <div className="part-card" key={index}>
          <header>
            <span className="part-number">{index + 1}</span>
            <strong>Сообщение {index + 1}</strong>
            <span className="part-tools">
              <Button
                variant="outline"
                icon
                aria-label="Выше"
                title="Выше"
                disabled={busy || index === 0}
                onClick={() => move(index, -1)}
              >
                <ArrowUp size={14} />
              </Button>
              <Button
                variant="outline"
                icon
                aria-label="Ниже"
                title="Ниже"
                disabled={busy || index === parts.length - 1}
                onClick={() => move(index, 1)}
              >
                <ArrowDown size={14} />
              </Button>
              <Button
                variant="outline"
                icon
                aria-label={`Удалить сообщение ${index + 1}`}
                title="Удалить сообщение"
                disabled={busy || parts.length === 1}
                onClick={() => change(parts.filter((_, at) => at !== index))}
              >
                <Trash2 size={14} />
              </Button>
            </span>
          </header>
          <textarea
            className="part-text"
            rows={3}
            aria-label={`Текст сообщения ${index + 1}`}
            placeholder="Текст сообщения. Можно оставить пустым, если это только файлы."
            value={part.text}
            disabled={busy}
            onChange={event => update(index, { text: event.target.value }, false)}
            onBlur={() => onCommit?.(parts)}
          />
          <div className="part-files">
            {part.attachments.map(item => (
              <span className="chip board-chip" key={item.id} title={item.mime}>
                <Paperclip size={13} aria-hidden="true" />
                {item.filename} · {fileSize(item.size)}
                <button
                  type="button"
                  aria-label={`Убрать ${item.filename}`}
                  disabled={busy}
                  onClick={() =>
                    update(index, { attachments: part.attachments.filter(file => file.id !== item.id) })
                  }
                >
                  <X size={13} />
                </button>
              </span>
            ))}
            <Button
              variant="outline"
              disabled={busy || uploading !== null}
              onClick={() => void addFiles(index)}
            >
              <Paperclip size={14} /> {uploading === index ? 'Загрузка…' : 'Файлы'}
            </Button>
          </div>
        </div>
      ))}
      <Button
        variant="outline"
        className="part-add"
        disabled={busy || parts.length >= 10}
        onClick={() => change([...parts, { text: '', attachments: [] }], false)}
      >
        <Plus size={15} /> Добавить сообщение
      </Button>
    </div>
  );
}

/** How the chain goes out through «Verse iMessage»; red when it cannot. */
export function PlanNote({ parts }: { parts: IMessagePart[] }) {
  const plan = chainPlan(parts);
  return (
    <p className={plan.error ? 'error-text plan-note' : 'helper plan-note'}>
      {plan.error && <CircleAlert size={14} aria-hidden="true" />}
      {planText(plan)}
    </p>
  );
}

export function TemplateCard({
  template,
  busy,
  onEdit,
  onDelete,
  onUse,
  useLabel = 'В рассылку',
}: {
  template: IMessageTemplate;
  busy?: boolean;
  onEdit?: () => void;
  onDelete?: () => void;
  onUse: () => void;
  useLabel?: string;
}) {
  return (
    <article className="template-card">
      <header>
        <span className="template-icon">
          <LayoutTemplate size={15} />
        </span>
        <strong title={template.name}>{template.name}</strong>
        {onDelete && (
          <button
            type="button"
            className="template-delete"
            aria-label={`Удалить шаблон ${template.name}`}
            disabled={busy}
            onClick={onDelete}
          >
            <Trash2 size={14} />
          </button>
        )}
      </header>
      {template.folder && <span className="template-folder">{template.folder}</span>}
      <ol className="template-parts">
        {template.parts.map((part, index) => (
          <li key={index}>
            <span className="part-number">{index + 1}</span>
            <div>
              {part.text && <p title={part.text}>{part.text}</p>}
              {part.attachments.map(item => (
                <span className="template-file" key={item.id}>
                  <Paperclip size={11} aria-hidden="true" /> {item.filename}
                </span>
              ))}
            </div>
          </li>
        ))}
      </ol>
      {(template.plan.error || template.plan.launches > 1) && (
        <p className={template.plan.error ? 'template-warning bad' : 'template-warning'}>
          <CircleAlert size={12} aria-hidden="true" />
          {template.plan.error
            ? 'Команда не сможет отправить — откройте, чтобы узнать почему'
            : `Запусков команды: ${template.plan.launches}`}
        </p>
      )}
      <footer>
        {onEdit && (
          <Button variant="outline" disabled={busy} onClick={onEdit}>
            <SquarePen size={14} /> Изменить
          </Button>
        )}
        <Button variant="outline" disabled={busy} onClick={onUse}>
          <Plus size={14} /> {useLabel}
        </Button>
      </footer>
    </article>
  );
}

export function TemplateEditor({
  template,
  initial,
  folders,
  onClose,
  onSaved,
}: {
  template?: IMessageTemplate;
  /** Messages for a new template, e.g. the current chain. */
  initial?: IMessagePart[];
  folders: string[];
  onClose: () => void;
  onSaved: (template: IMessageTemplate) => void;
}) {
  const [name, setName] = useState(template?.name ?? '');
  const [folder, setFolder] = useState(template?.folder ?? '');
  const [parts, setParts] = useState<IMessagePart[]>(
    template?.parts ?? initial ?? [{ text: '', attachments: [] }],
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const save = async () => {
    setBusy(true);
    setError('');
    try {
      const saved = await api.request<IMessageTemplate>('imessage.template_save', {
        id: template?.id,
        name,
        folder,
        parts: partsPayload(parts),
      });
      onSaved(saved);
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal title={template ? 'Изменить шаблон' : 'Новый шаблон'} wide onClose={onClose}>
      <div className="template-fields">
        <label>
          Название
          <input
            autoFocus
            maxLength={120}
            value={name}
            placeholder="Приветствие"
            onChange={event => setName(event.target.value)}
          />
        </label>
        <label>
          Папка
          <input
            list="imessage-template-folders"
            maxLength={60}
            value={folder}
            placeholder="Без папки"
            onChange={event => setFolder(event.target.value)}
          />
          <datalist id="imessage-template-folders">
            {folders.map(item => (
              <option key={item} value={item} />
            ))}
          </datalist>
        </label>
      </div>
      <p className="helper">
        Шаблон — одно или несколько сообщений. В каждом — текст, файлы или и то и другое: файлы уходят следом
        за текстом. {'{Phone}'} подставит телефон или email получателя.
      </p>
      <PartsEditor parts={parts} busy={busy} onChange={setParts} onError={setError} />
      <PlanNote parts={parts} />
      <ErrorToast message={error} />
      <div className="actions">
        <Button disabled={busy || !name.trim()} onClick={() => void save()}>
          <Save size={15} /> Сохранить
        </Button>
        <Button variant="outline" onClick={onClose}>
          Отмена
        </Button>
      </div>
    </Modal>
  );
}

/** «Шаблоны» on the campaign board: pick a template to put into the list. */
export function TemplatePicker({
  chain,
  onClose,
  onUse,
}: {
  chain: boolean;
  onClose: () => void;
  onUse: (template: IMessageTemplate) => void;
}) {
  const templates = useResource<IMessageTemplate[]>('imessage.templates');
  const items = templates.data ?? [];
  return (
    <Modal title="Шаблоны iMessage" wide onClose={onClose}>
      <p className="helper">
        {chain
          ? 'Шаблон заменит текущую цепочку сообщений.'
          : 'Шаблон из одного сообщения добавит текст к вариантам, а файлы — к вложениям. Шаблон из нескольких сообщений станет цепочкой: каждый получатель получит их по порядку.'}
      </p>
      {templates.error && <p className="error-text">{templates.error}</p>}
      {items.length ? (
        <div className="template-grid">
          {items.map(item => (
            <TemplateCard key={item.id} template={item} useLabel="Вставить" onUse={() => onUse(item)} />
          ))}
        </div>
      ) : (
        <p className="im-empty">Шаблонов пока нет — создайте их в разделе «Шаблоны».</p>
      )}
    </Modal>
  );
}
