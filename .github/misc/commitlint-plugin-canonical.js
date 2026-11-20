// Local commitlint rule: canonical commits carry a :emoji: scope, automation keeps its own scope.
const EMOJI = /^:[a-z0-9_+-]+:$/;
const isAutomation = (type, scope) =>
  (type === 'chore' && ['release', 'deps', 'deps-dev'].includes(scope)) ||
  (type === 'build' && scope === 'deps');

module.exports = {
  rules: {
    'canonical-scope': ({ type, scope }) => [
      Boolean(scope) && (EMOJI.test(scope) || isAutomation(type, scope)),
      'use <type>(:emoji:): <summary>; run /het-commit ' +
        '(see .github/skills/het-commit/SKILL.md)',
    ],
  },
};
