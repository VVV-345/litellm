import { createHash, randomUUID } from "node:crypto";
import {
  chmodSync,
  copyFileSync,
  existsSync,
  lstatSync,
  mkdirSync,
  readFileSync,
  readdirSync,
  realpathSync,
  renameSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { dirname, join, resolve } from "node:path";
import {
  inspectJob,
  installedPath,
  privateDirectory,
  readActive,
  rejectSymlink,
  restartService,
  runCommand,
  stopService,
  VERSION_PATTERN,
  writeJsonPrivate,
} from "./manager-service.mjs";

export const PACKAGE_FILES = Object.freeze([
  "README.md",
  "package.json",
  "package-lock.json",
  "cli.mjs",
  "core.mjs",
  "browser.mjs",
  "server.mjs",
  "login.mjs",
  "authorize.mjs",
  "manage.mjs",
  "manager-service.mjs",
  "manager-package.mjs",
  "package-release.mjs",
  "管理.command",
]);
const MANIFEST = "SHA256SUMS.json";
const hash = (path) =>
  createHash("sha256").update(readFileSync(path)).digest("hex");
function regularFile(path) {
  rejectSymlink(path);
  if (!lstatSync(path).isFile()) throw new Error(`安装包文件无效: ${path}`);
}
function packageIdentity(source) {
  const value = JSON.parse(readFileSync(join(source, "package.json"), "utf8"));
  const lock = JSON.parse(
    readFileSync(join(source, "package-lock.json"), "utf8"),
  );
  if (
    value.name !== "litellm-camoufox-helper" ||
    !VERSION_PATTERN.test(value.version) ||
    value.engines?.node !== ">=22" ||
    lock.version !== value.version ||
    lock.name !== value.name ||
    lock.lockfileVersion !== 3 ||
    lock.packages?.[""]?.version !== value.version
  )
    throw new Error("安装包版本或锁文件不匹配");
  return value.version;
}
export function createPackage(source, destination) {
  rejectSymlink(source);
  for (const file of PACKAGE_FILES) regularFile(join(source, file));
  const version = packageIdentity(source);
  mkdirSync(destination, { mode: 0o700 });
  for (const file of PACKAGE_FILES) {
    copyFileSync(join(source, file), join(destination, file));
    chmodSync(join(destination, file), file === "管理.command" ? 0o755 : 0o644);
  }
  const files = Object.fromEntries(
    PACKAGE_FILES.map((file) => [file, hash(join(destination, file))]),
  );
  writeFileSync(
    join(destination, MANIFEST),
    JSON.stringify({ version, files }, null, 2) + "\n",
    { mode: 0o644 },
  );
  return verifyPackage(destination);
}
export function verifyPackage(source, { installed = false } = {}) {
  rejectSymlink(source);
  const allowed = [
    ...PACKAGE_FILES,
    MANIFEST,
    ...(installed ? ["node_modules"] : []),
  ];
  if (readdirSync(source).some((file) => !allowed.includes(file)))
    throw new Error("安装包包含清单外文件");
  for (const file of [...PACKAGE_FILES, MANIFEST])
    regularFile(join(source, file));
  const manifest = JSON.parse(readFileSync(join(source, MANIFEST), "utf8"));
  if (
    !manifest ||
    !manifest.files ||
    typeof manifest.files !== "object" ||
    Array.isArray(manifest.files) ||
    Object.keys(manifest.files).sort().join("\n") !==
      [...PACKAGE_FILES].sort().join("\n")
  )
    throw new Error("安装包文件清单无效");
  for (const file of PACKAGE_FILES) {
    if (
      !/^[a-f0-9]{64}$/.test(manifest.files[file]) ||
      hash(join(source, file)) !== manifest.files[file]
    )
      throw new Error(`安装包校验失败: ${file}`);
  }
  if (installed) rejectSymlink(join(source, "node_modules"));
  const version = packageIdentity(source);
  if (manifest.version !== version) throw new Error("安装包清单版本无效");
  return { version, files: manifest.files };
}
export function installDependencies(app) {
  const npm = join(dirname(realpathSync(process.execPath)), "npm");
  if (!existsSync(npm))
    throw new Error(
      "当前 Node 没有配套 npm，请重新安装 Node.js 22+（包含 npm）",
    );
  const result = runCommand(
    process.execPath,
    [realpathSync(npm), "ci", "--ignore-scripts", "--no-audit", "--no-fund"],
    {
      cwd: app,
      env: {
        ...process.env,
        PATH: `${dirname(process.execPath)}:${process.env.PATH ?? ""}`,
      },
      timeout: 300_000,
    },
  );
  if (result.status !== 0)
    throw new Error(
      "npm ci 安装依赖失败，请检查网络及 npm 配置后重试；旧版本未切换",
    );
}
export function smoke(app) {
  const result = runCommand(
    process.execPath,
    [
      "--input-type=module",
      "-e",
      "await import('./core.mjs'); await import('./browser.mjs'); await import('./server.mjs'); await import('./login.mjs');",
    ],
    { cwd: app, timeout: 15_000 },
  );
  if (result.status !== 0) throw new Error("新版本模块自检失败，旧版本未切换");
}
const defaultService = {
  inspect: inspectJob,
  stop: stopService,
  start: restartService,
};
async function selectVersion(root, next, service) {
  const previous = readActive(root);
  const job = service.inspect(root);
  if (job.loaded && !job.owned)
    throw new Error("该 launchd 服务属于其他数据目录，未切换版本");
  if (job.loaded) await service.stop(root);
  writeJsonPrivate(join(root, "active.json"), next);
  try {
    if (job.loaded) await service.start(root);
  } catch (error) {
    if (previous) writeJsonPrivate(join(root, "active.json"), previous);
    else rmSync(join(root, "active.json"));
    try {
      if (previous) await service.start(root);
    } catch {
      throw new Error(
        "新版本启动失败，旧版本选择已恢复，但旧服务也未能启动。请运行 diagnose",
      );
    }
    throw error;
  }
}
function newerThan(left, right) {
  const other = right.split(".").map(Number);
  const position = left
    .split(".")
    .map(Number)
    .findIndex((number, index) => number !== other[index]);
  return position !== -1 && Number(left.split(".")[position]) > other[position];
}
export async function installPackage(root, source, dependencies = {}) {
  const verified = verifyPackage(source);
  privateDirectory(root);
  const active = readActive(root);
  if (
    active &&
    verified.version !== active.version &&
    !newerThan(verified.version, active.version)
  )
    throw new Error("更新必须选择更高版本，切回旧版请使用 rollback");
  privateDirectory(join(root, "apps"));
  const destination = installedPath(root, verified.version);
  if (existsSync(destination)) {
    const existing = verifyPackage(destination, { installed: true });
    if (JSON.stringify(existing.files) !== JSON.stringify(verified.files))
      throw new Error("同版本安装内容不一致，拒绝覆盖");
    (dependencies.smoke ?? smoke)(destination);
  } else {
    const stage = join(root, "apps", `.stage-${randomUUID()}`);
    try {
      mkdirSync(stage, { mode: 0o700 });
      for (const file of [...PACKAGE_FILES, MANIFEST])
        copyFileSync(join(source, file), join(stage, file));
      chmodSync(join(stage, "管理.command"), 0o755);
      verifyPackage(stage);
      (dependencies.installDependencies ?? installDependencies)(stage);
      (dependencies.smoke ?? smoke)(stage);
      verifyPackage(stage, { installed: true });
      renameSync(stage, destination);
    } finally {
      rmSync(stage, { recursive: true, force: true });
    }
  }
  if (active?.version === verified.version) return active;
  const next = {
    version: verified.version,
    previousVersion: active?.version ?? null,
  };
  await selectVersion(root, next, dependencies.service ?? defaultService);
  return next;
}
export async function rollback(root, dependencies = {}) {
  const active = readActive(root);
  if (!active?.previousVersion) throw new Error("没有可回退的已安装版本");
  const app = installedPath(root, active.previousVersion);
  verifyPackage(app, { installed: true });
  (dependencies.smoke ?? smoke)(app);
  const next = {
    version: active.previousVersion,
    previousVersion: active.version,
  };
  await selectVersion(root, next, dependencies.service ?? defaultService);
  return next;
}
export async function withManagerLock(root, action) {
  privateDirectory(root);
  const lock = join(resolve(root), "manager.lock");
  try {
    mkdirSync(lock, { mode: 0o700 });
  } catch (error) {
    if (error.code !== "EEXIST") throw error;
    throw new Error(
      `已有管理操作进行中；若之前异常中断，确认没有运行中的管理窗口后删除 ${lock}`,
    );
  }
  try {
    return await action();
  } finally {
    rmSync(lock, { recursive: true });
  }
}
