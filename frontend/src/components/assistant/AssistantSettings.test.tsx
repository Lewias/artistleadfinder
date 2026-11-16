import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { ModelForm } from './AssistantSettings';

const provider = {
  provider: 'compatible' as const,
  base_url: 'https://opencode.ai/inference/go/openai/v1',
  main: 'claude-haiku-5-5',
  strong: '',
  anthropic_key: false,
  compatible_key: false,
  shared_key_available: true,
};

describe('shared OpenCode connection', () => {
  it('offers a ready connection without a key field', () => {
    const html = renderToStaticMarkup(<ModelForm provider={provider} onSaved={() => {}} />);
    expect(html).toContain('Ключ уже встроен');
    expect(html).not.toContain('type="password"');
    expect(html).toContain('claude-haiku-5-5');
  });

  it('keeps the key input for builds without a shared credential', () => {
    const html = renderToStaticMarkup(
      <ModelForm provider={{ ...provider, shared_key_available: false }} onSaved={() => {}} />,
    );
    expect(html).toContain('type="password"');
    expect(html).not.toContain('Ключ уже встроен');
  });

  it('does not offer the shared credential to a different provider', () => {
    const html = renderToStaticMarkup(
      <ModelForm
        provider={{ ...provider, base_url: 'https://openrouter.ai/api/v1' }}
        onSaved={() => {}}
      />,
    );
    expect(html).toContain('type="password"');
    expect(html).not.toContain('Ключ уже встроен');
  });
});
