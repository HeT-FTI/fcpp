// Commit gate: <type>(:emoji:): <description>; see .github/skills/_shared/gitmoji.md and /het-commit.
// headerPattern must stay identical to .releaserc.json parserOpts (emoji <-> scope).
const canonical = require('./commitlint-plugin-canonical');

module.exports = {
  parserPreset: {
    parserOpts: {
      headerPattern: /^(\w*)(?:\(([^)]*)\))?!?: (.*)$/,
      headerCorrespondence: ['type', 'scope', 'subject'],
    },
  },
  plugins: [canonical],
  rules: {
    'canonical-scope': [2, 'always'],
    'type-enum': [
      2,
      'always',
      ['feat', 'fix', 'perf', 'docs', 'test', 'build', 'ci', 'refactor', 'style', 'chore'],
    ],
    'type-case': [2, 'always', 'lower-case'],
    'type-empty': [2, 'never'],
    'subject-empty': [2, 'never'],
    'subject-max-length': [2, 'always', 100],
    'header-max-length': [2, 'always', 120],
  },
};
