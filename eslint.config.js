import js from '@eslint/js';
import tseslint from 'typescript-eslint';
import globals from 'globals';
import hooks from 'eslint-plugin-react-hooks';
import refresh from 'eslint-plugin-react-refresh';

export default tseslint.config(
  { ignores: ['dist/**', 'node_modules/**', '.npm-cache/**', '.venv/**', 'src-tauri/target/**', 'backend/**', 'build/**', 'artifacts/**', 'src-tauri/binaries/**', 'src-tauri/core/**'] },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  { files: ['src-tauri/src/*.js'], languageOptions: { globals: globals.browser } },
  { files: ['scripts/**/*.mjs'], languageOptions: { globals: globals.node } },
  {
    files: ['frontend/src/**/*.{ts,tsx}'],
    languageOptions: { globals: globals.browser },
    plugins: { 'react-hooks': hooks, 'react-refresh': refresh },
    rules: { ...hooks.configs.recommended.rules,
      'react-refresh/only-export-components': ['warn', { allowConstantExport: true }] },
  },
);
