import type { AssistantItem } from '../../services/types';

type ChatBlock =
  | { kind: 'user'; text: string }
  | { kind: 'answer'; texts: string[]; steps: AssistantItem[]; sends: number[] };

/** One answer per question: its texts, submitted messages and steps. */
export function chatBlocks(items: AssistantItem[]): ChatBlock[] {
  const blocks: ChatBlock[] = [];
  for (const item of items) {
    if (item.kind === 'user') {
      blocks.push({ kind: 'user', text: item.text });
      continue;
    }
    let answer = blocks[blocks.length - 1];
    if (!answer || answer.kind !== 'answer') {
      answer = { kind: 'answer', texts: [], steps: [], sends: [] };
      blocks.push(answer);
    }
    if (item.kind === 'assistant') answer.texts.push(item.text);
    else if (item.kind === 'send' && item.message_id) answer.sends.push(item.message_id);
    else if (item.kind === 'tool') answer.steps.push(item);
  }
  return blocks;
}
