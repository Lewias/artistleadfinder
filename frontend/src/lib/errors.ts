/** One readable text for any error the app can meet: a core reply, a Tauri rejection,
 * a JS exception or a value thrown by a library. */

const CODE = /\s*\(код ([0-9a-f]{6})\)/;
const CYRILLIC = /[А-Яа-яЁё]/;

// Technical messages of the webview and the bridge, said in plain words.
const KNOWN: [RegExp, string][] = [
  [
    /__TAURI_INTERNALS__|reading 'invoke'|invoke is not a function/i,
    'Нет связи с ядром приложения. Перезапустите приложение.',
  ],
  [
    /failed to fetch|networkerror|network request failed|load failed/i,
    'Нет соединения с сетью. Проверьте интернет и повторите.',
  ],
  [/timed? ?out|timeout/i, 'Действие заняло слишком много времени. Повторите ещё раз.'],
  [/quota|storage.*full/i, 'Не хватает места для данных приложения.'],
];

function rawText(error: unknown): string {
  if (error == null) return '';
  if (typeof error === 'string') return error;
  if (error instanceof Error) return error.message;
  if (typeof error === 'object') {
    const value =
      (error as { message?: unknown; error?: unknown }).message ?? (error as { error?: unknown }).error;
    if (typeof value === 'string') return value;
    try {
      return JSON.stringify(error);
    } catch {
      return '';
    }
  }
  return String(error);
}

export function errorText(error: unknown): string {
  const text = rawText(error)
    .replace(/^(Uncaught\s+)?(\w*Error):\s*/, '')
    .trim();
  if (!text) return 'Что-то пошло не так. Повторите действие.';
  if (CYRILLIC.test(text)) return text;
  const known = KNOWN.find(([pattern]) => pattern.test(text));
  if (known) return known[1];
  return `Непредвиденная ошибка: ${text.slice(0, 200)}`;
}

/** The core adds «(код abc123)» to errors it logged; the code is shown apart. */
export function splitCode(text: string): { text: string; code?: string } {
  const match = text.match(CODE);
  if (!match) return { text };
  return { text: text.replace(CODE, '').replace(/\s+\./g, '.').trim(), code: match[1] };
}
