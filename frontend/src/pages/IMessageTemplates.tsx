import { ErrorToast } from '../components/Toaster';
import { useMemo, useState } from 'react';
import { Plus, Search, Trash2 } from 'lucide-react';
import { api } from '../services/api';
import { useResource } from '../hooks/useResource';
import type { IMessageTemplate } from '../services/types';
import { number } from '../lib/format';
import { PageHeader } from '../components/PageHeader';
import { Button } from '../components/ui/button';
import { Modal } from '../components/Modal';
import { errorText } from '../components/accountRuns';
import { TemplateCard, TemplateEditor } from '../components/imessage/Templates';

const ALL = '';

/** iMessage templates as cards: search, folders, create, edit, delete and use. */
export function IMessageTemplates({ onUsed }: { onUsed: () => void }) {
  const resource = useResource<IMessageTemplate[]>('imessage.templates');
  const templates = useMemo(() => resource.data ?? [], [resource.data]);
  const [search, setSearch] = useState('');
  const [folder, setFolder] = useState(ALL);
  const [editing, setEditing] = useState<IMessageTemplate | 'new' | null>(null);
  const [removing, setRemoving] = useState<IMessageTemplate | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const folders = useMemo(
    () =>
      [...new Set(templates.map(item => item.folder).filter(Boolean))].sort((a, b) =>
        a.localeCompare(b, 'ru'),
      ),
    [templates],
  );
  const query = search.trim().toLowerCase();
  const shown = templates.filter(
    item =>
      (folder === ALL || item.folder === folder) &&
      (!query ||
        [
          item.name,
          item.folder,
          ...item.parts.flatMap(part => [part.text, ...part.attachments.map(file => file.filename)]),
        ]
          .join('\n')
          .toLowerCase()
          .includes(query)),
  );

  const run = async (method: string, params: object) => {
    setBusy(true);
    setError('');
    try {
      await api.request(method, params);
      return true;
    } catch (err) {
      setError(errorText(err));
      return false;
    } finally {
      setBusy(false);
    }
  };
  const use = async (template: IMessageTemplate) => {
    if (await run('imessage.template_use', { id: template.id })) onUsed();
  };
  const remove = async () => {
    if (removing && (await run('imessage.template_delete', { id: removing.id }))) {
      setRemoving(null);
      resource.refresh();
    }
  };

  return (
    <div className="screen">
      <PageHeader
        page="imessage-templates"
        count={number(templates.length)}
        actions={
          <Button onClick={() => setEditing('new')}>
            <Plus size={15} /> Новый шаблон
          </Button>
        }
      />

      <div className="template-filters">
        <label className="crm-search">
          <Search size={15} aria-hidden="true" />
          <input
            aria-label="Поиск по шаблонам"
            placeholder="Поиск по названию, тексту, файлам или папке…"
            value={search}
            onChange={event => setSearch(event.target.value)}
          />
        </label>
        <select aria-label="Папка" value={folder} onChange={event => setFolder(event.target.value)}>
          <option value={ALL}>Все папки</option>
          {folders.map(item => (
            <option key={item} value={item}>
              {item}
            </option>
          ))}
        </select>
      </div>

      <ErrorToast message={error} />
      {resource.error && (
        <p role="alert" className="error-text">
          {resource.error}
        </p>
      )}

      {shown.length ? (
        <div className="template-grid">
          {shown.map(item => (
            <TemplateCard
              key={item.id}
              template={item}
              busy={busy}
              onEdit={() => setEditing(item)}
              onDelete={() => setRemoving(item)}
              onUse={() => void use(item)}
            />
          ))}
        </div>
      ) : (
        <p className="im-empty template-empty">
          {templates.length
            ? 'Ничего не найдено.'
            : 'Шаблонов пока нет. Создайте первый: одно сообщение или цепочку из текста и файлов.'}
        </p>
      )}

      {editing && (
        <TemplateEditor
          template={editing === 'new' ? undefined : editing}
          folders={folders}
          onClose={() => setEditing(null)}
          onSaved={() => {
            setEditing(null);
            resource.refresh();
          }}
        />
      )}
      {removing && (
        <Modal title="Удалить шаблон?" onClose={() => setRemoving(null)}>
          <p className="helper">
            «{removing.name}» будет удалён. Рассылку, в которую он уже вставлен, это не изменит.
          </p>
          <div className="actions">
            <Button variant="danger" disabled={busy} onClick={() => void remove()}>
              <Trash2 size={15} /> Удалить
            </Button>
            <Button variant="outline" onClick={() => setRemoving(null)}>
              Отмена
            </Button>
          </div>
        </Modal>
      )}
    </div>
  );
}
