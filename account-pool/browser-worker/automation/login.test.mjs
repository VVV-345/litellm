// 复用 PR #4 的登录、账号归属、RFC 6238 与人工验证边界测试。
import test from 'node:test';
import assert from 'node:assert/strict';
import { parseCredential, totp, automateLogin, verifyResumableAccount } from './login.mjs';
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
    isVisible: async () => role === 'textbox' ? (name === stage().field || (stage().path === '/log-in/password' && name === (stage().emailLabel ?? 'Email address'))) : role === 'heading' ? (name instanceof RegExp ? name.test(stage().heading ?? '') : name === stage().heading) : false,
    inputValue: async () => {
      if (stage().path === '/log-in/password' && name !== (stage().emailLabel ?? 'Email address')) throw new Error('no matching account field');
      return stage().email ?? credential.email;
    },
    fill: async (value) => filled.push({ name, value }),
    click: async () => {
      if (role === 'button' && stage().buttonName) {
        assert.ok(name instanceof RegExp ? name.test(stage().buttonName) : name === stage().buttonName);
      }
      clicked.push(stage().path); position = Math.min(position + 1, stages.length - 1);
    },
    press: async (key) => { assert.equal(key, 'Enter'); clicked.push(stage().path); position = Math.min(position + 1, stages.length - 1); },
    first() { return this; },
  });
  const page = {
    url: () => (stage().origin ?? 'https://auth.openai.com') + stage().path,
    isClosed: () => false,
    title: async () => stage().title ?? 'Sign in to Codex with ChatGPT - OpenAI',
    getByRole: (role, options) => locator(role, options.name),
    getByLabel: (name) => locator('textbox', name instanceof RegExp ? name.test(stage().field) ? stage().field : name.test(stage().emailLabel ?? 'Email address') ? (stage().emailLabel ?? 'Email address') : name : name),
    locator: (selector) => locator('textbox', stage().emailInputType === 'email' && selector.includes('email') ? 'email-type' : stage().inputSelector === selector ? stage().field : 'absent'),
    getByText: (text) => ({ waitFor: async () => {
      if (stage().hideEmail || text !== (stage().email ?? credential.email)) throw new Error("not visible");
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

test('recognizes a typed email form when the provider label is not associated with the input', async () => {
  const { page, filled } = pageFixture([
    { path: '/log-in', field: 'email-type', emailInputType: 'email' },
    { path: '/email-verification' },
  ]);
  assert.equal((await automateLogin(page, credential)).status, 'verification_required');
  assert.deepEqual(filled, [{ name: 'email-type', value: credential.email }]);
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

test('slow redirects and pause/resume cannot repeat a submitted form', async () => {
  const { page, clicked } = pageFixture([{ path: '/log-in', field: 'Email address' }]);
  const submitted = new Set();
  await automateLogin(page, credential, { submitted, timeoutMs: 20 });
  await automateLogin(page, credential, { submitted, timeoutMs: 20 });
  assert.equal(clicked.length, 1);
});

test('resume verifies the visible account again and never guesses an unknown challenge', async () => {
  const matching = pageFixture([{ path: '/sign-in-with-chatgpt/codex/consent' }]);
  assert.equal(await verifyResumableAccount(matching.page, credential), true);
  for (const stage of [
    { path: '/sign-in-with-chatgpt/codex/consent', email: 'other@example.test' },
    { path: '/log-in', field: 'Email address' },
    { path: '/captcha' },
    { origin: 'https://evil.example', path: '/log-in/password', field: 'Password' },
  ]) {
    assert.equal(await verifyResumableAccount(pageFixture([stage]).page, credential), false);
  }
});


test('submits the known email form with the observed Japanese continue button', async () => {
  const { page, filled, clicked } = pageFixture([
    { path: '/log-in', field: 'Email address', buttonName: '続行' },
    { path: '/email-verification' },
  ]);
  assert.equal((await automateLogin(page, credential)).status, 'verification_required');
  assert.deepEqual(filled, [{ name: 'Email address', value: credential.email }]);
  assert.deepEqual(clicked, ['/log-in']);
});


test('fills the observed Japanese password field and checks the account before submitting', async () => {
  const { page, filled, clicked } = pageFixture([
    { path: '/log-in/password', field: 'パスワード', emailLabel: 'メールアドレス', inputSelector: 'input[type="password"]', buttonName: '続行' },
    { path: '/email-verification' },
  ]);
  assert.equal((await automateLogin(page, credential, { locale: 'ja-JP', timeoutMs: 20 })).status, 'verification_required');
  assert.deepEqual(filled, [{ name: 'パスワード', value: credential.password }]);
  assert.deepEqual(clicked, ['/log-in/password']);
  const wrong = pageFixture([
    { path: '/log-in/password', field: 'パスワード', emailLabel: 'メールアドレス', inputSelector: 'input[type="password"]', email: 'other@example.test', buttonName: '続行' },
  ]);
  assert.equal((await automateLogin(wrong.page, credential, { locale: 'ja-JP', timeoutMs: 20 })).status, 'account_mismatch');
  assert.equal(wrong.filled.length, 0);
});


for (const [locale, emailLabel, passwordLabel, otpHeading, continueLabel] of [
  ['zh-CN', '电子邮件地址', '密码', '检查你的身份验证器应用', '继续'],
  ['zh-TW', '電子郵件地址', '密碼', '檢查您的驗證器應用程式', '繼續'],
  ['ja-JP', 'メールアドレス', 'パスワード', '認証アプリを確認してください', '続行'],
  ['ko-KR', '이메일 주소', '비밀번호', '인증 앱을 확인하세요', '계속'],
  ['fr-FR', 'Adresse e-mail', 'Mot de passe', 'Vérifiez votre application d’authentification', 'Continuer'],
  ['de-DE', 'E-Mail-Adresse', 'Passwort', 'Überprüfe deine Authentifizierungs-App', 'Weiter'],
  ['es-ES', 'Correo electrónico', 'Contraseña', 'Revisa tu aplicación de autenticación', 'Continuar'],
  ['pt-BR', 'Endereço de e-mail', 'Senha', 'Confira seu aplicativo autenticador', 'Continuar'],
]) {
  test(`stable form fields complete ${locale} login without relying on translated submit labels`, async () => {
    const { page, filled, clicked } = pageFixture([
      { path: '/log-in', field: 'email-type', emailInputType: 'email', buttonName: continueLabel },
      { path: '/log-in/password', field: passwordLabel, emailLabel, inputSelector: 'input[type="password"]', buttonName: continueLabel },
      { path: '/mfa-challenge/test', field: 'otp-type', inputSelector: 'input[autocomplete="one-time-code"]', heading: otpHeading, buttonName: continueLabel },
      { path: '/sign-in-with-chatgpt/codex/consent' },
    ]);
    assert.equal((await automateLogin(page, credential, { locale, now: () => 59_000, allowConsent: false, timeoutMs: 30 })).status, 'confirmation_required');
    assert.deepEqual(filled.map(x => x.value), [credential.email, credential.password, '287082']);
    assert.equal(clicked.length, 3);
  });
}

test('an MFA email challenge is never mistaken for an authenticator form', async () => {
  const { page, filled } = pageFixture([{ path: '/mfa-challenge/test', field: 'otp-type', inputSelector: 'input[autocomplete="one-time-code"]', heading: 'Check your inbox' }]);
  assert.equal((await automateLogin(page, credential, { timeoutMs: 20 })).status, 'verification_required');
  assert.equal(filled.length, 0);
});


test('localized consent retains the bound account, Codex app and one-time callback checks', async () => {
  const callback = 'http://127.0.0.1:1456/auth/callback?state=expected';
  const { page, clicked } = pageFixture([
    { path: '/sign-in-with-chatgpt/codex/consent', title: 'Se connecter à Codex avec ChatGPT - OpenAI', buttonName: 'Continuer' },
    { origin: 'http://127.0.0.1:1456', path: '/auth/callback?state=expected&code=test' },
  ]);
  assert.equal((await automateLogin(page, credential, { locale: 'fr-FR', callback })).status, 'callback_received');
  assert.equal(clicked.length, 1);
  const wrongApp = pageFixture([{ path: '/sign-in-with-chatgpt/codex/consent', title: 'Other app - OpenAI' }]);
  assert.equal((await automateLogin(wrongApp.page, credential, { callback })).status, 'verification_required');
  assert.equal(wrongApp.clicked.length, 0);
});


test('Japanese MFA without an account label requires the preceding password account proof', async () => {
  const mfa = { path: '/mfa-challenge/test', field: 'ワンタイムコード', heading: '認証アプリを確認してください', hideEmail: true };
  const { page, filled } = pageFixture([
    { path: '/log-in/password', field: 'パスワード', emailLabel: 'メールアドレス', inputSelector: 'input[type="password"]' },
    mfa, { path: '/email-verification' },
  ]);
  assert.equal((await automateLogin(page, credential, { locale: 'ja-JP', now: () => 59_000, timeoutMs: 30 })).status, 'verification_required');
  assert.deepEqual(filled.map(x => x.value), [credential.password, '287082']);
  const unbound = pageFixture([mfa]);
  assert.equal((await automateLogin(unbound.page, credential, { locale: 'ja-JP', timeoutMs: 30 })).status, 'verification_required');
  assert.equal(unbound.filled.length, 0);
});
