import type { AssistantItem } from '../../services/types';

type ChatBlock =
  | { kind: 'user'; text: string }
  | { kind: 'answer'; texts: string[]; steps: AssistantItem[]; drafts: number[] };

/** One answer per question, as in a chat: its texts, the drafts it made and its steps. */
export function chatBlocks(items: AssistantItem[]): ChatBlock[] {
  const blocks: ChatBlock[] = [];
  for (const item of items) {
    if (item.kind === 'user') {
      blocks.push({ kind: 'user', text: item.text });
      continue;
    }
    let answer = blocks[blocks.length - 1];
    if (!answer || answer.kind !== 'answer') {
      answer = { kind: 'answer', texts: [], steps: [], drafts: [] };
      blocks.push(answer);
    }
    if (item.kind === 'assistant') answer.texts.push(item.text);
    else if (item.kind === 'draft' && item.draft_id) answer.drafts.push(item.draft_id);
    else if (item.kind === 'tool' || item.kind === 'escalation') answer.steps.push(item);
  }
  return blocks;
}
