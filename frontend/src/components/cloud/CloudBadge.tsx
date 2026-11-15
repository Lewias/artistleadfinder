import { Cloud } from 'lucide-react';
import type { CloudContactInfo } from '../../services/types';
import { categoryLabels } from './cloudText';

/** A CRM contact the cloud parser found: its category, with the reason on hover. */
/** The cloud parser's category. With `statuses` it shrinks to the icon when a status already says it. */
export function CloudBadge({ info, statuses = [] }: { info: CloudContactInfo; statuses?: string[] }) {
  const category = info.category ? categoryLabels[info.category] : 'Без категории';
  const confidence = info.ai && info.confidence ? ` · ${info.confidence}%` : '';
  const title = [
    `Облачный парсер: ${category}${confidence}`,
    info.ai === false ? 'Классификация не выполнена' : null,
    info.reason,
  ]
    .filter(Boolean)
    .join('\n');
  const repeated = statuses.some(label => label.trim().toLowerCase() === category.toLowerCase());
  return (
    <span className={`cloud-badge${repeated ? ' icon-only' : ''}`} title={title}>
      <Cloud size={12} aria-label={repeated ? `Облачный парсер: ${category}` : undefined} />
      {!repeated && ` ${category}`}
    </span>
  );
}
