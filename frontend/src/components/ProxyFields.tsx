import { useState } from 'react';
import { parseProxyInput, type ProxyForm } from './proxyInput';

export function ProxyFields({
  value,
  input,
  onChange,
  onInputChange,
  disabled,
}: {
  value: ProxyForm;
  input: string;
  onChange: (value: ProxyForm) => void;
  onInputChange: (value: string) => void;
  disabled: boolean;
}) {
  const [inputError, setInputError] = useState('');
  const apply = (text: string) => {
    try {
      onChange(parseProxyInput(text, value.scheme));
      onInputChange(text);
      setInputError('');
    } catch (error) {
      onInputChange(text);
      setInputError(error instanceof Error ? error.message : 'Некорректный формат прокси.');
    }
  };
  return (
    <div className="profile-proxy-fields">
      <label>
        Подключение
        <select
          value={value.scheme}
          disabled={disabled}
          onChange={event => onChange({ ...value, scheme: event.target.value as ProxyForm['scheme'] })}
        >
          <option value="none">Без прокси</option>
          <option value="http">HTTP-прокси</option>
          <option value="socks5">SOCKS5-прокси</option>
        </select>
      </label>
      <label>
        Прокси одной строкой
        <input
          type="text"
          value={input}
          disabled={disabled}
          placeholder="socks5://логин:пароль@адрес:порт"
          autoComplete="off"
          spellCheck={false}
          onPaste={event => {
            event.preventDefault();
            apply(event.clipboardData.getData('text'));
          }}
          onChange={event => {
            onInputChange(event.target.value);
            setInputError('');
          }}
          onBlur={() => {
            if (input) apply(input);
          }}
        />
      </label>
      <p className="helper profile-proxy-hint">
        Можно вставить и без <code>socks5://</code> — такой прокси будет считаться SOCKS5. Для HTTP укажите{' '}
        <code>http://</code> или выберите тип выше.
      </p>
      {inputError && (
        <p className="error-text" role="alert">
          {inputError}
        </p>
      )}
      {value.scheme !== 'none' && value.host && (
        <p className="helper profile-proxy-summary">
          Настроен {value.scheme.toUpperCase()}: {value.host}:{value.port}
          {value.auth ? ' · с авторизацией' : ''}. Для замены вставьте новую строку, для удаления выберите
          «Без прокси».
        </p>
      )}
    </div>
  );
}
