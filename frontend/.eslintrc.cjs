module.exports = {
  root: true,
  env: { browser: true, es2021: true, node: true },
  extends: [
    'eslint:recommended',
    'plugin:react/recommended',
    'plugin:react-hooks/recommended',
    'prettier',
  ],
  parserOptions: {
    ecmaVersion: 'latest',
    sourceType: 'module',
    ecmaFeatures: { jsx: true },
  },
  settings: { react: { version: 'detect' } },
  plugins: ['react-refresh'],
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
};
