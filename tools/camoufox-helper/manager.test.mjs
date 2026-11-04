import test from "node:test";
import assert from "node:assert/strict";
import {
  mkdtempSync,
  readFileSync,
  realpathSync,
  statSync,
  rmSync,
  writeFileSync,
  symlinkSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  DEFAULT_SETTINGS,
  validateSettings,
  saveSettings,
  loadSettings,
  launchPlist,
  inspectJob,
  startService,
  stopService,
} from "./manager-service.mjs";

function fixture(t) {
  const root = mkdtempSync(join(tmpdir(), "camoufox 管理 "));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  return root;
}
test("settings reject unsafe origins and ports and persist privately without changing the key", (t) => {
  const root = fixture(t);
  writeFileSync(join(root, "helper.key"), "preserved");
  const settings = {
    ...DEFAULT_SETTINGS,
    origins: ["https://pool.example"],
    callbackPort: 1456,
  };
  saveSettings(root, settings);
  assert.deepEqual(loadSettings(root), settings);
  assert.equal(statSync(join(root, "settings.json")).mode & 0o777, 0o600);
  assert.equal(statSync(root).mode & 0o777, 0o700);
  assert.equal(readFileSync(join(root, "helper.key"), "utf8"), "preserved");
  for (const bad of [
    [],
    ["http://remote.example"],
    ["https://pool.example/"],
    ["https://u:p@pool.example"],
  ]) {
    assert.throws(() => validateSettings({ ...settings, origins: bad }));
  }
  for (const bad of [0, 65536, 1.1, "10109"])
    assert.throws(() => validateSettings({ ...settings, port: bad }));
  assert.throws(() =>
    validateSettings({ ...settings, executable: "relative/camoufox" }),
  );
  assert.throws(() => validateSettings({ ...settings, unexpected: true }));
});
test("settings refuse symlink destinations without altering their target", (t) => {
  const root = fixture(t);
  const target = join(root, "untouched");
  writeFileSync(target, "private");
  symlinkSync(target, join(root, "settings.json"));
  assert.throws(
    () => saveSettings(root, DEFAULT_SETTINGS),
    /symbolic|symlink/i,
  );
  assert.equal(readFileSync(target, "utf8"), "private");
});
test("launchd uses escaped argument entries and private logs without login registration", () => {
  const xml = launchPlist(
    "/tmp/目录 & space",
    "/tmp/应用 <new>",
    { ...DEFAULT_SETTINGS, callbackPort: 1456 },
    "/tmp/node & bin",
  );
  assert.match(xml, /<string>\/tmp\/node &amp; bin<\/string>/);
  assert.match(xml, /<string>\/tmp\/应用 &lt;new&gt;\/cli.mjs<\/string>/);
  assert.match(
    xml,
    /<string>--data-dir<\/string>\s*<string>\/tmp\/目录 &amp; space<\/string>/,
  );
  assert.match(
    xml,
    /<string>--callback-port<\/string>\s*<string>1456<\/string>/,
  );
  assert.doesNotMatch(xml, /\/bin\/(?:ba)?sh|LaunchAgents|helper.key/);
});
test("a launchd job from another data directory cannot be stopped", async (t) => {
  const root = fixture(t);
  const commands = [];
  const run = (_command, args) => {
    commands.push(args);
    return {
      status: 0,
      stdout: "path = /another/state/helper.plist\n",
      stderr: "",
    };
  };
  assert.equal(inspectJob(root, { run }).owned, false);
  await assert.rejects(stopService(root, { run }), /another|其他/);
  assert.equal(
    commands.every(([command]) => command === "print"),
    true,
  );
});
test("unexpected launchctl failures are reported rather than treated as stopped", (t) => {
  const root = fixture(t);
  assert.throws(
    () =>
      inspectJob(root, {
        run: () => ({ status: 1, stderr: "permission denied", stdout: "" }),
      }),
    /launchctl/,
  );
});
test("occupied ports refuse startup without bootstrapping or killing a manual helper", async (t) => {
  const root = fixture(t);
  saveSettings(root, DEFAULT_SETTINGS);
  writeFileSync(
    join(root, "active.json"),
    JSON.stringify({ version: "0.2.0", previousVersion: null }),
  );
  const commands = [];
  const run = (_cmd, args) => {
    commands.push(args);
    return { status: 113, stdout: "", stderr: "Could not find service" };
  };
  await assert.rejects(
    startService(root, {
      run,
      portAvailable: async () => false,
      executableExists: () => true,
    }),
    /端口|port/,
  );
  assert.equal(
    commands.every(([command]) => command === "print"),
    true,
  );
});
test("launchd ownership matches canonical paths for macOS temporary directory aliases", (t) => {
  const root = fixture(t);
  const real = realpathSync(root);
  const run = () => ({
    status: 0,
    stdout: `path = ${join(real, "helper.plist")}\n`,
    stderr: "",
  });
  assert.equal(inspectJob(root, { run }).owned, true);
});
