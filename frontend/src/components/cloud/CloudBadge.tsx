import { Cloud } from 'lucide-react';
import type { CloudContactInfo } from '../../services/types';
import { categoryLabels } from './cloudText';

/** A CRM contact the cloud parser found: its category, with the reason on hover. */
export function CloudBadge({ info }: { info: CloudContactInfo }) {
  const category = info.category ? categoryLabels[info.category] : 'Без категории';
  const confidence = info.ai && info.confidence ? ` · ${info.confidence}%` : '';
  const title = [
    `Облачный парсер: ${category}${confidence}`,
    info.ai === false ? 'Классификация не выполнена' : null,
    info.reason,
  ]
    .filter(Boolean)
    .join('\n');
  return (
    <span className="cloud-badge" title={title}>
      <Cloud size={12} /> {category}
    </span>
  );
}
