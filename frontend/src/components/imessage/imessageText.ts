import type {
  IMessageCampaign,
  IMessageJob,
  IMessageJobStatus,
  IMessagePart,
  IMessagePlan,
} from '../../services/types';
import { plural } from '../../lib/format';

const EMAIL = /^[^@\s]{1,64}@[^@\s]+\.[^@\s.]{2,}$/;

/** One recipient per line: a phone «+15555550123» or an email, optionally «; own text». */
export function parseRecipients(text: string) {
  const recipients: { phone: string; message: string }[] = [];
  const invalid: string[] = [];
  for (const line of text.split('\n')) {
    if (!line.trim()) continue;
    const match = line.match(/^\s*([^;\t]+?)\s*(?:[;\t]\s*(.*))?$/);
    const raw = (match?.[1] ?? '').trim();
    const value = raw.includes('@') ? raw.toLowerCase() : raw.replace(/[\s()\-.]/g, '').replace(/^00/, '+');
    if (!(EMAIL.test(value) || /^\+\d{6,15}$/.test(value))) {
      invalid.push(line.trim().slice(0, 24));
      continue;
    }
    recipients.push({ phone: value, message: (match?.[2] ?? '').trim() });
  }
  return { recipients, invalid };
}

export function recipientsText(recipients: { phone: string; message: string }[]) {
  return recipients.map(item => (item.message ? `${item.phone}; ${item.message}` : item.phone)).join('\n');
}

/** Mirrors the core: the name and the whole task URL percent-encoded. */
export function deepLink(shortcutName: string, taskUrl: string) {
  return `shortcuts://run-shortcut?name=${encodeURIComponent(shortcutName)}&input=text&text=${encodeURIComponent(taskUrl)}`;
}

// Never "доставлено": a Shortcut only reports that its own steps ran.
export const jobStatusLabel: Record<IMessageJobStatus, string> = {
  pending: 'В очереди',
  issued: 'Выдано телефону',
  execution_acknowledged: 'Выполнено на iPhone',
  uncertain: 'Неизвестно',
  failed: 'Не отправлено',
};
export const jobStatusTone: Record<IMessageJobStatus, string> = {
  pending: '',
  issued: 'queued',
  execution_acknowledged: 'sent',
  uncertain: 'warn',
  failed: 'bad',
};

export function jobStatusDetail(job: Pick<IMessageJob, 'status' | 'ack_scope' | 'text_acked_at'>) {
  if (job.status === 'execution_acknowledged') {
    if (job.ack_scope === 'text')
      return 'Исходный Shortcut прошёл шаг текста; вложения и доставка не подтверждаются';
    if (job.ack_scope === 'manual') return 'Отмечено вами после проверки на iPhone';
    return 'Shortcut передал текст и вложения в Messages; доставку iOS не сообщает';
  }
  if (job.status === 'issued')
    return job.text_acked_at
      ? 'Текст передан в Messages, ждём вложения и подтверждение'
      : 'Ждём подтверждения от Shortcut';
  if (job.status === 'uncertain')
    return 'Сообщение могло уйти: подтверждение не пришло. Проверьте переписку на iPhone';
  if (job.status === 'failed') return 'Не отправлялось';
  return 'Ждёт очереди';
}

export const campaignStatusLabel: Record<IMessageCampaign['status'], string> = {
  running: 'Идёт',
  paused: 'Пауза',
  stopped: 'Остановлена',
  finished: 'Завершена',
};

export const eventLabel: Record<string, string> = {
  bridge_started: 'Мост',
  bridge_stopped: 'Мост',
  token_rotated: 'Токен',
  phone_connected: 'iPhone',
  request_rejected: 'Отказ',
  campaign_started: 'Запуск',
  campaign_paused: 'Пауза',
  campaign_running: 'Продолжено',
  campaign_stopped: 'Остановка',
  campaign_finished: 'Готово',
  jobs_issued: 'Выдано',
  job_issued: 'Выдано',
  text_ack: 'Текст',
  job_acknowledged: 'Выполнено',
  ack_repeated: 'Повтор ACK',
  ack_late: 'Поздний ACK',
  job_uncertain: 'Неизвестно',
  job_resolved: 'Решение',
  settings: 'Настройки',
};
export const eventTone: Record<string, string> = {
  job_acknowledged: 'log-lead',
  ack_late: 'log-lead',
  job_uncertain: 'log-warn',
  request_rejected: 'log-error',
  campaign_stopped: 'log-skip',
};

export function fileSize(bytes: number) {
  if (bytes < 1024) return `${bytes} Б`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} КБ`;
  return `${(bytes / 1024 / 1024).toFixed(1)} МБ`;
}

export const ATTACHMENT_EXTENSIONS = [
  'jpg',
  'jpeg',
  'png',
  'gif',
  'heic',
  'webp',
  'mp4',
  'mov',
  'm4a',
  'mp3',
  'pdf',
];

/** Mirrors the core's `chain.plan`: the signed Shortcut sends text first, then the files
 * of its list after every entry, so files join the text before them and a message with
 * files is a launch of its own. */
export function chainPlan(parts: IMessagePart[]): IMessagePlan {
  const steps: { files: number }[] = [];
  for (const [index, part] of parts.entries()) {
    const text = part.text.trim();
    if (text) steps.push({ files: part.attachments.length });
    else if (part.attachments.length) {
      if (!steps.length)
        return {
          messages: 0,
          launches: 0,
          error:
            '«Verse iMessage» начинает каждое сообщение с текста: в первом сообщении нужен текст. Файлы без текста перед ними команда отправить не может.',
        };
      steps[steps.length - 1].files += part.attachments.length;
    } else
      return { messages: 0, launches: 0, error: `Сообщение №${index + 1} пустое: добавьте текст или файл.` };
  }
  if (!steps.length) return { messages: 0, launches: 0, error: 'Добавьте хотя бы одно сообщение.' };
  let run = 0;
  let inRun = 0;
  for (const step of steps) {
    if (step.files && inRun) {
      run += 1;
      inRun = 0;
    }
    inRun += 1;
    if (step.files) {
      run += 1;
      inRun = 0;
    }
  }
  return { messages: steps.length, launches: inRun ? run + 1 : run, error: null };
}

/** One line on how a chain goes out, for the editor and the cards. */
export function planText(plan: IMessagePlan) {
  if (plan.error) return plan.error;
  const gap = plan.messages > 1 ? ', между ними 20–50 с' : '';
  const launches =
    plan.launches > 1 ? ` Запусков команды: ${plan.launches} — после каждого отсканируйте QR-код снова.` : '';
  return `Каждый получатель получит ${plural(plan.messages, ['сообщение', 'сообщения', 'сообщений'])}${gap}.${launches}`;
}

/** The core's shape of a chain or template message. */
export const partsPayload = (parts: IMessagePart[]) =>
  parts.map(part => ({ text: part.text, attachment_ids: part.attachments.map(item => item.id) }));
