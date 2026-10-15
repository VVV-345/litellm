# Local braces security patch

Upstream: braces 3.0.3, https://registry.npmjs.org/braces/-/braces-3.0.3.tgz

Integrity: `sha512-yQbXgO/OSZVD2IsiLlro+7Hf6Q18EJrKSEsdoMzKePKXct3gvD8oLcOQdIzGupr5Fj+EDe8gO/lxc1BzfMpxvA==`

The upstream version and MIT license are unchanged. Only `lib/parse.js` changes runtime code. The local manifest omits upstream development dependencies and scripts to avoid installing their unrelated toolchain. `SECURITY.patch` records the change

Mitigates GHSA-vfj7-8cjw-p6xm for string inputs by limiting combined brace and parenthesis AST depth to 128 before recursive traversal. Escaped delimiters, quotes and bracket classes keep their existing semantics. Hand-built AST objects are trusted internal input and are not validated by this parser limit

No lifecycle script is needed. The root npm override resolves every braces dependency to this local package. Run `node --test scripts/braces-security.test.mjs` after `npm ci --ignore-scripts`. Audit tools may still report 3.0.3; do not suppress or rename that identity. Revisit when an official fixed release is available

The registry audit currently reports zero but does not analyze this local package. The parser regression and installed micromatch/fast-glob resolution tests are required evidence, not a registry clearance. Remove the local dependency and override only after an official release passes these same tests
