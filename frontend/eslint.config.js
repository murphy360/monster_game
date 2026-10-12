import js from '@eslint/js';
import globals from 'globals';
import react from 'eslint-plugin-react';
import reactHooks from 'eslint-plugin-react-hooks';
import { reactRefresh } from 'eslint-plugin-react-refresh';
import prettier from 'eslint-config-prettier';

export default [
  { ignores: ['dist', 'node_modules'] },
  {
    files: ['**/*.{js,jsx}'],
    languageOptions: {
      ecmaVersion: 'latest',
      sourceType: 'module',
      parserOptions: { ecmaFeatures: { jsx: true } },
      globals: { ...globals.browser, ...globals.node },
    },
    settings: { react: { version: 'detect' } },
    plugins: {
      react,
      'react-hooks': reactHooks,
      'react-refresh': reactRefresh.plugin,
    },
    rules: {
      ...js.configs.recommended.rules,
      ...react.configs.flat.recommended.rules,
      ...reactHooks.configs.flat.recommended.rules,
      'react-refresh/only-export-components': ['warn', { allowConstantExport: true }],
      'react/prop-types': 'off',
      // ESLint 9 changed the no-unused-vars default to caughtErrors: 'all';
      // keep the ESLint 8 behaviour so `catch (_)` stays allowed.
      'no-unused-vars': ['error', { caughtErrors: 'none' }],
      'no-constant-condition': ['error', { checkLoops: false }],
      // eslint-plugin-react-hooks v7 folded React Compiler prep rules into
      // `recommended`. set-state-in-effect flags this app's existing
      // fetch-on-mount and reset-state-on-id-change effects, both documented,
      // intentional React patterns here rather than bugs; the plugin has no
      // lighter preset that omits it, so disable it explicitly.
      'react-hooks/set-state-in-effect': 'off',
    },
  },
  prettier,
];
