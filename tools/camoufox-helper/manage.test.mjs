import test from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, existsSync, rmSync } from "node:fs";
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
