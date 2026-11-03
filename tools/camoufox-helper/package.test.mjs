import test from "node:test";
import assert from "node:assert/strict";
import {
  mkdtempSync,
  readFileSync,
  writeFileSync,
  readdirSync,
  mkdirSync,
  rmSync,
  symlinkSync,
} from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import {
  createPackage,
  verifyPackage,
  installPackage,
  rollback,
  PACKAGE_FILES,
} from "./manager-package.mjs";
import { readActive, writeJsonPrivate } from "./manager-service.mjs";

function fixture(t) {
  const root = mkdtempSync(join(tmpdir(), "camoufox 安装 "));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const source = join(root, "source");
  mkdirSync(source);
  for (const name of PACKAGE_FILES)
    writeFileSync(join(source, name), "export {};\n");
  writeFileSync(
    join(source, "package.json"),
    JSON.stringify({
      name: "litellm-camoufox-helper",
      version: "0.2.0",
      engines: { node: ">=22" },
    }),
  );
  writeFileSync(
    join(source, "package-lock.json"),
    JSON.stringify({
      name: "litellm-camoufox-helper",
      version: "0.2.0",
      lockfileVersion: 3,
      packages: { "": { name: "litellm-camoufox-helper", version: "0.2.0" } },
    }),
  );
  return {
    root,
    source,
    state: join(root, "state"),
    package: join(root, "package"),
  };
}
function setVersion(source, version) {
  for (const name of ["package.json", "package-lock.json"]) {
    const json = JSON.parse(readFileSync(join(source, name), "utf8"));
    json.version = version;
    if (json.packages) json.packages[""].version = version;
    writeFileSync(join(source, name), JSON.stringify(json));
  }
}
const dependencies = {
  installDependencies: () => {},
  smoke: () => {},
  service: { inspect: () => ({ loaded: false, owned: false }) },
};
test("distribution excludes state, dependencies and tests and rejects altered checksums and paths", (t) => {
  const paths = fixture(t);
  for (const file of ["helper.key", "private.test.mjs", "registry.json"])
    writeFileSync(join(paths.source, file), "secret");
  mkdirSync(join(paths.source, "node_modules"));
  createPackage(paths.source, paths.package);
  assert.deepEqual(
    readdirSync(paths.package).sort(),
    [...PACKAGE_FILES, "SHA256SUMS.json"].sort(),
  );
  assert.equal(verifyPackage(paths.package).version, "0.2.0");
  writeFileSync(join(paths.package, "cli.mjs"), "tampered");
  assert.throws(() => verifyPackage(paths.package), /checksum|校验/);
  const manifest = JSON.parse(
    readFileSync(join(paths.package, "SHA256SUMS.json"), "utf8"),
  );
  manifest.files["../helper.key"] = "a".repeat(64);
  writeFileSync(
    join(paths.package, "SHA256SUMS.json"),
    JSON.stringify(manifest),
  );
  assert.throws(() => verifyPackage(paths.package), /清单|manifest/);
});
test("packaging and installation reject symlinks and extra files", (t) => {
  const paths = fixture(t);
  createPackage(paths.source, paths.package);
  rmSync(join(paths.package, "cli.mjs"));
  symlinkSync(join(paths.source, "cli.mjs"), join(paths.package, "cli.mjs"));
  assert.throws(() => verifyPackage(paths.package), /symbolic|symlink/i);
  assert.throws(
    () => createPackage(paths.package, join(paths.root, "other")),
    /symbolic|symlink/i,
  );
  rmSync(join(paths.package, "cli.mjs"));
  writeFileSync(join(paths.package, "cli.mjs"), "export {};\n");
  writeFileSync(join(paths.package, "helper.key"), "secret");
  assert.throws(() => verifyPackage(paths.package), /文件|files/);
});
test("failed staging leaves previous version and persistent files byte-for-byte intact", async (t) => {
  const paths = fixture(t);
  createPackage(paths.source, paths.package);
  mkdirSync(paths.state);
  writeJsonPrivate(join(paths.state, "active.json"), {
    version: "0.1.0",
    previousVersion: null,
  });
  mkdirSync(join(paths.state, "profiles"));
  for (const name of [
    "helper.key",
    "registry.json",
    "settings.json",
    "profiles/cookies",
  ])
    writeFileSync(join(paths.state, name), "untouched");
  await assert.rejects(
    installPackage(paths.state, paths.package, {
      ...dependencies,
      installDependencies: () => {
        throw new Error("npm failed");
      },
    }),
    /npm failed/,
  );
  assert.equal(readActive(paths.state).version, "0.1.0");
  for (const name of [
    "helper.key",
    "registry.json",
    "settings.json",
    "profiles/cookies",
  ])
    assert.equal(readFileSync(join(paths.state, name), "utf8"), "untouched");
});
test("updates and rollback select retained installations while preserving state", async (t) => {
  const paths = fixture(t);
  createPackage(paths.source, paths.package);
  await installPackage(paths.state, paths.package, dependencies);
  writeFileSync(join(paths.state, "helper.key"), "unchanged");
  setVersion(paths.source, "0.2.1");
  const newer = join(paths.root, "newer");
  createPackage(paths.source, newer);
  await installPackage(paths.state, newer, dependencies);
  assert.deepEqual(readActive(paths.state), {
    version: "0.2.1",
    previousVersion: "0.2.0",
  });
  await rollback(paths.state, dependencies);
  assert.deepEqual(readActive(paths.state), {
    version: "0.2.0",
    previousVersion: "0.2.1",
  });
  assert.equal(
    readFileSync(join(paths.state, "helper.key"), "utf8"),
    "unchanged",
  );
});
test("a failed managed restart restores active selection and restarts the old version", async (t) => {
  const paths = fixture(t);
  createPackage(paths.source, paths.package);
  await installPackage(paths.state, paths.package, dependencies);
  setVersion(paths.source, "0.2.1");
  const newer = join(paths.root, "newer");
  createPackage(paths.source, newer);
  const started = [];
  const service = {
    inspect: () => ({ loaded: true, owned: true }),
    stop: async () => {},
    start: async () => {
      const version = readActive(paths.state).version;
      started.push(version);
      if (version === "0.2.1") throw new Error("new version failed");
    },
  };
  await assert.rejects(
    installPackage(paths.state, newer, { ...dependencies, service }),
    /new version failed/,
  );
  assert.deepEqual(started, ["0.2.1", "0.2.0"]);
  assert.equal(readActive(paths.state).version, "0.2.0");
});
