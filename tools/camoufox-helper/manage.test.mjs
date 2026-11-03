import test from "node:test";
import assert from "node:assert/strict";
import {
  mkdtempSync,
  existsSync,
  readFileSync,
  writeFileSync,
  rmSync,
} from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
const directory = fileURLToPath(new URL(".", import.meta.url));
test("help is dependency-free and no command on noninteractive input performs no mutation", (t) => {
  const temporary = mkdtempSync(join(tmpdir(), "camoufox menu "));
  t.after(() => rmSync(temporary, { recursive: true, force: true }));
  const root = join(temporary, "uncreated");
  const help = spawnSync(
    process.execPath,
    [join(directory, "manage.mjs"), "--help"],
    { encoding: "utf8" },
  );
  assert.equal(help.status, 0, help.stderr);
  assert.match(help.stdout, /install.*configure.*start/s);
  const idle = spawnSync(
    process.execPath,
    [join(directory, "manage.mjs"), "--data-dir", root],
    { encoding: "utf8", input: "" },
  );
  assert.equal(idle.status, 1);
  assert.equal(existsSync(root), false);
});
test("double-click launcher finds a supported Node and passes arguments unchanged", () => {
  const result = spawnSync(
    "/bin/bash",
    [join(directory, "管理.command"), "--help"],
    { encoding: "utf8" },
  );
  assert.equal(result.status, 0, result.stderr);
  assert.match(result.stdout, /macOS Camoufox/);
});
test("explicit complete configure repairs invalid settings and rejected input preserves valid settings", (t) => {
  const root = mkdtempSync(join(tmpdir(), "camoufox repair "));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const path = join(root, "settings.json");
  writeFileSync(path, "{broken");
  const call = (args) =>
    spawnSync(
      process.execPath,
      [join(directory, "manage.mjs"), "configure", "--data-dir", root, ...args],
      { encoding: "utf8" },
    );
  const partial = call(["--origin", "https://pool.example"]);
  assert.equal(partial.status, 1);
  assert.equal(readFileSync(path, "utf8"), "{broken");
  const complete = call([
    "--origin",
    "https://pool.example",
    "--port",
    "10110",
    "--callback-port",
    "1456",
    "--executable",
    "/Applications/Camoufox.app/Contents/MacOS/camoufox",
  ]);
  assert.equal(complete.status, 0, complete.stderr);
  const saved = readFileSync(path, "utf8");
  assert.equal(JSON.parse(saved).port, 10110);
  assert.equal(JSON.parse(saved).callbackPort, 1456);
  const invalid = call(["--port", "0"]);
  assert.equal(invalid.status, 1);
  assert.equal(readFileSync(path, "utf8"), saved);
  const update = call(["--origin", "https://new.example"]);
  assert.equal(update.status, 0, update.stderr);
  assert.equal(JSON.parse(readFileSync(path, "utf8")).port, 10110);
});
