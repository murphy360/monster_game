import js from '@eslint/js';
import prettier from 'eslint-config-prettier';
import react from 'eslint-plugin-react';
import reactHooks from 'eslint-plugin-react-hooks';
import reactRefresh from 'eslint-plugin-react-refresh';
import globals from 'globals';

export default [
  { ignores: ['dist', 'node_modules'] },
  js.configs.recommended,
  react.configs.flat.recommended,
  reactHooks.configs.flat.recommended,
  prettier,
  {
    files: ['**/*.{js,jsx}'],
    languageOptions: {
      ecmaVersion: 'latest',
      sourceType: 'module',
      parserOptions: { ecmaFeatures: { jsx: true } },
      globals: { ...globals.browser, ...globals.es2021, ...globals.node },
    },
    // An explicit version: eslint-plugin-react's 'detect' calls context.getFilename,
    // which ESLint 10 removed. Keep in step with the react dependency.
    settings: { react: { version: '19.3' } },
    plugins: { 'react-refresh': reactRefresh },
    rules: {
      'react-refresh/only-export-components': ['warn', { allowConstantExport: true }],
      'react/prop-types': 'off',
      'no-constant-condition': ['error', { checkLoops: false }],
      // eslint-plugin-react-hooks v7 folded React Compiler prep rules into
      // `recommended`. set-state-in-effect flags this app's existing
      // fetch-on-mount and reset-state-on-id-change effects, both documented,
      // intentional React patterns here rather than bugs; the plugin has no
      // lighter preset that omits it, so disable it explicitly.
      'react-hooks/set-state-in-effect': 'off',
    },
  },
];
