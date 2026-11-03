import test from 'node:test';
import assert from 'node:assert/strict';
import { parseCredential, totp, automateLogin } from './login.mjs';
const secret = 'GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ';
const credential = { email: 'account@example.test', password: 'test-password', totpSecret: secret };

test('parses one credential record without executing or interpreting the export header', () => {
  assert.deepEqual(parseCredential('Export\naccount@example.test----test-password----'+secret), credential);
  assert.throws(() => parseCredential('a@example.test----p----'+secret+'\nb@example.test----p----'+secret));
  assert.throws(() => parseCredential('account@example.test----p----invalid secret'));
});
test('generates six digit SHA1 TOTP from RFC 6238 vectors', () => {
  assert.equal(totp(secret, 59_000), '287082');
  assert.equal(totp(secret, 1111111109_000), '081804');
  assert.equal(totp(secret, 1111111111_000), '050471');
});
function pageFixture(stages) {
  let position = 0;
  const filled = [];
  const clicked = [];
  const stage = () => stages[position];
  const locator = (role, name) => ({
    isVisible: async () => role === 'textbox' ? name === stage().field : role === 'heading' ? name === stage().heading : false,
    inputValue: async () => stage().email ?? credential.email,
    fill: async (value) => filled.push({ name, value }),
    click: async () => { clicked.push(stage().path); position = Math.min(position + 1, stages.length - 1); },
    first() { return this; },
  });
  const page = {
    url: () => (stage().origin ?? 'https://auth.openai.com') + stage().path,
    isClosed: () => false,
    title: async () => 'Sign in to Codex with ChatGPT - OpenAI',
    getByRole: (role, options) => locator(role, options.name),
    getByLabel: (name) => locator('textbox', name),
    getByText: (text) => ({ waitFor: async () => {
      if (text !== (stage().email ?? credential.email)) throw new Error("not visible");
    } }),
  };
  return { page, filled, clicked };
}
test('fills email, password and authenticator code then stops before consent', async () => {
  const { page, filled, clicked } = pageFixture([
    { path: '/log-in', field: 'Email address' },
    { path: '/log-in/password', field: 'Password' },
    { path: '/mfa-challenge/test', field: 'One-time code', heading: 'Check your authenticator app' },
    { path: '/sign-in-with-chatgpt/codex/consent' },
  ]);
  assert.deepEqual(await automateLogin(page, credential, { now: () => 59_000, allowConsent: false }), { status: 'confirmation_required' });
  assert.deepEqual(filled, [{ name: 'Email address', value: credential.email }, { name: 'Password', value: credential.password }, { name: 'One-time code', value: '287082' }]);
  assert.equal(clicked.length, 3);
});
test('never sends credentials to another origin or a different account', async () => {
  for (const stage of [
    { origin: 'https://evil.example', path: '/log-in', field: 'Email address' },
    { path: '/log-in/password', field: 'Password', email: 'other@example.test' },
  ]) {
    const { page, filled, clicked } = pageFixture([stage]);
    const result = await automateLogin(page, credential);
    assert.ok(['verification_required', 'account_mismatch'].includes(result.status));
    assert.equal(filled.length, 0); assert.equal(clicked.length, 0);
  }
});
test('does not treat emailed verification or CAPTCHA as an authenticator challenge', async () => {
  const { page, filled } = pageFixture([{ path: '/email-verification', field: 'One-time code', heading: 'Check your inbox' }]);
  assert.deepEqual(await automateLogin(page, credential), { status: 'verification_required' });
  assert.equal(filled.length, 0);
});
test('honors cancellation before filling any field', async () => {
  const { page, filled } = pageFixture([{ path: '/log-in', field: 'Email address' }]);
  const controller = new AbortController(); controller.abort();
  assert.deepEqual(await automateLogin(page, credential, { signal: controller.signal }), { status: 'stopped' });
  assert.equal(filled.length, 0);
});
test('authorizes by default after verifying the account and follows only the expected callback', async () => {
  const callback = 'http://127.0.0.1:1456/auth/callback?state=expected&code=test';
  const { page, clicked } = pageFixture([
    { path: '/sign-in-with-chatgpt/codex/consent' },
    { origin: 'http://127.0.0.1:1456', path: '/auth/callback?state=expected&code=test' },
  ]);
  assert.deepEqual(await automateLogin(page, credential, { callback }), { status: 'callback_received' });
  assert.equal(clicked.length, 1);
  const wrong = pageFixture([{ path: '/sign-in-with-chatgpt/codex/consent', email: 'other@example.test' }]);
  assert.equal((await automateLogin(wrong.page, credential, { callback })).status, 'account_mismatch');
  assert.equal(wrong.clicked.length, 0);
});
test('never reports callback success for a foreign state or an OAuth error', async () => {
  for (const path of ['/auth/callback?state=wrong&code=test', '/auth/callback?state=expected&error=access_denied']) {
    const { page } = pageFixture([{ origin: 'http://127.0.0.1:1456', path }]);
    assert.equal((await automateLogin(page, credential, { callback: 'http://127.0.0.1:1456/auth/callback?state=expected' })).status, 'verification_required');
  }
});
