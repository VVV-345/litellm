import { spawnSync } from "node:child_process";
import {
  accessSync,
  chmodSync,
  constants,
  existsSync,
  lstatSync,
  mkdirSync,
  readFileSync,
  realpathSync,
  renameSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { createServer } from "node:net";
import { dirname, isAbsolute, join, resolve } from "node:path";
import { setTimeout as delay } from "node:timers/promises";
import { randomUUID } from "node:crypto";

export const LABEL = "local.litellm.camoufox-helper";
export const VERSION_PATTERN = /^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$/;
export const DEFAULT_SETTINGS = Object.freeze({
  origins: ["http://127.0.0.1:4001"],
  port: 10109,
  callbackPort: null,
  executable: "/Applications/Camoufox.app/Contents/MacOS/camoufox",
});
export function rejectSymlink(path) {
  try {
    if (lstatSync(path).isSymbolicLink())
      throw new Error(`Refusing symbolic link: ${path}`);
  } catch (error) {
    if (error.code !== "ENOENT") throw error;
  }
}
export function privateDirectory(path) {
  rejectSymlink(path);
  mkdirSync(path, { recursive: true, mode: 0o700 });
  chmodSync(path, 0o700);
}
export function writeJsonPrivate(path, value) {
  rejectSymlink(path);
  const temporary = `${path}.${randomUUID()}.tmp`;
  try {
    writeFileSync(temporary, JSON.stringify(value, null, 2) + "\n", {
      mode: 0o600,
      flag: "wx",
    });
    renameSync(temporary, path);
  } finally {
    rmSync(temporary, { force: true });
  }
}
function readJson(path) {
  rejectSymlink(path);
  return JSON.parse(readFileSync(path, "utf8"));
}
export function validateSettings(value) {
  if (
    !value ||
    typeof value !== "object" ||
    Array.isArray(value) ||
    Object.keys(value).some(
      (key) => !Object.keys(DEFAULT_SETTINGS).includes(key),
    )
  )
    throw new Error("配置字段无效");
  if (!Array.isArray(value.origins) || !value.origins.length)
    throw new Error("至少配置一个网站来源");
  const origins = value.origins.map((raw) => {
    const url = new URL(raw);
    if (
      typeof raw !== "string" ||
      raw !== url.origin ||
      url.username ||
      url.password ||
      !(
        url.protocol === "https:" ||
        (url.protocol === "http:" &&
          ["localhost", "127.0.0.1", "[::1]"].includes(url.hostname))
      )
    )
      throw new Error("网站来源必须是 HTTPS 或本机 HTTP，不能包含路径");
    return raw;
  });
  if (
    ![
      value.port,
      ...(value.callbackPort === null ? [] : [value.callbackPort]),
    ].every((port) => Number.isInteger(port) && port > 0 && port <= 65535)
  )
    throw new Error("端口必须是 1 到 65535 的整数");
  if (
    typeof value.executable !== "string" ||
    !isAbsolute(value.executable) ||
    /[\x00-\x1f]/.test(value.executable)
  )
    throw new Error("Camoufox 路径必须是完整的可执行文件路径");
  return {
    origins: [...new Set(origins)],
    port: value.port,
    callbackPort: value.callbackPort,
    executable: value.executable,
  };
}
export function loadSettings(root) {
  return validateSettings(readJson(join(root, "settings.json")));
}
export function saveSettings(root, value) {
  const settings = validateSettings(value);
  privateDirectory(root);
  writeJsonPrivate(join(root, "settings.json"), settings);
  return settings;
}
export function readActive(root) {
  const path = join(root, "active.json");
  rejectSymlink(path);
  if (!existsSync(path)) return null;
  const value = readJson(path);
  if (
    !value ||
    !VERSION_PATTERN.test(value.version) ||
    !(
      value.previousVersion === null ||
      VERSION_PATTERN.test(value.previousVersion)
    )
  )
    throw new Error("安装版本记录无效");
  return { version: value.version, previousVersion: value.previousVersion };
}
export function installedPath(root, version) {
  if (!VERSION_PATTERN.test(version)) throw new Error("版本无效");
  const path = join(root, "apps", version);
  rejectSymlink(join(root, "apps"));
  rejectSymlink(path);
  return path;
}
export function runCommand(command, args, options = {}) {
  const result = spawnSync(command, args, {
    encoding: "utf8",
    timeout: 120_000,
    maxBuffer: 4 * 1024 * 1024,
    ...options,
  });
  if (result.error)
    throw new Error(`无法执行 ${command}: ${result.error.code ?? "failed"}`);
  return result;
}
const xmlEscape = (text) =>
  String(text).replace(
    /[&<>"']/g,
    (char) =>
      ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&apos;",
      })[char],
  );
export function launchPlist(root, app, settings, node = process.execPath) {
  const args = [
    node,
    join(app, "cli.mjs"),
    "--data-dir",
    root,
    "--port",
    String(settings.port),
    "--executable",
    settings.executable,
    ...settings.origins.flatMap((origin) => ["--origin", origin]),
    ...(settings.callbackPort === null
      ? []
      : ["--callback-port", String(settings.callbackPort)]),
  ];
  return `<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n<plist version="1.0"><dict>\n<key>Label</key><string>${LABEL}</string>\n<key>ProgramArguments</key><array>${args.map((arg) => `<string>${xmlEscape(arg)}</string>`).join("\n")}</array>\n<key>RunAtLoad</key><true/>\n<key>KeepAlive</key><false/>\n<key>WorkingDirectory</key><string>${xmlEscape(app)}</string>\n<key>StandardOutPath</key><string>${xmlEscape(join(root, "logs", "helper.log"))}</string>\n<key>StandardErrorPath</key><string>${xmlEscape(join(root, "logs", "helper.error.log"))}</string>\n<key>Umask</key><integer>63</integer>\n</dict></plist>\n`;
}
const serviceTarget = () => `gui/${process.getuid()}/${LABEL}`;
export function inspectJob(root, { run = runCommand } = {}) {
  const result = run("/bin/launchctl", ["print", serviceTarget()]);
  if (result.status === 113 && /could not find service/i.test(result.stderr))
    return { loaded: false, owned: false };
  if (result.status !== 0)
    throw new Error(`launchctl 状态查询失败 (${result.status})`);
  const path = /^\s*path = (.+)$/m.exec(result.stdout)?.[1];
  const canonicalRoot = existsSync(root) ? realpathSync(root) : resolve(root);
  const canonicalPath =
    path && existsSync(dirname(path))
      ? join(realpathSync(dirname(path)), "helper.plist")
      : path;
  return {
    loaded: true,
    owned:
      path?.endsWith("/helper.plist") === true &&
      canonicalPath === join(canonicalRoot, "helper.plist"),
  };
}
export const executableExists = (path) => {
  try {
    accessSync(path, constants.X_OK);
    return lstatSync(path).isFile();
  } catch {
    return false;
  }
};
export function portAvailable(port) {
  return new Promise((done) => {
    const server = createServer();
    server.once("error", () => done(false));
    server.listen(port, "127.0.0.1", () => server.close(() => done(true)));
  });
}
export async function health(root, settings) {
  try {
    const path = join(root, "helper.key");
    rejectSymlink(path);
    const key = readFileSync(path, "utf8").trim();
    if (!/^[a-f0-9]{64}$/.test(key)) return false;
    const response = await fetch(
      `http://127.0.0.1:${settings.port}/v1/health`,
      {
        headers: {
          Origin: settings.origins[0],
          Authorization: `Bearer ${key}`,
        },
        signal: AbortSignal.timeout(1000),
        redirect: "error",
      },
    );
    const result = response.ok ? await response.json() : null;
    return result?.name === "litellm-camoufox-helper" && result.version === 1;
  } catch {
    return false;
  }
}
export async function serviceStatus(root, dependencies = {}) {
  const active = readActive(root);
  const configured = existsSync(join(root, "settings.json"));
  const settings = configured ? loadSettings(root) : null;
  const job = inspectJob(root, dependencies);
  return {
    ...job,
    configured,
    version: active?.version ?? null,
    previousVersion: active?.previousVersion ?? null,
    healthy: settings
      ? await (dependencies.health ?? health)(root, settings)
      : false,
    port: settings?.port ?? null,
    dataDirectory: root,
    logs: join(root, "logs"),
  };
}
export async function stopService(root, dependencies = {}) {
  const run = dependencies.run ?? runCommand;
  const job = inspectJob(root, dependencies);
  if (!job.loaded) return;
  if (!job.owned) throw new Error("该 launchd 服务属于其他数据目录，未停止");
  const result = run("/bin/launchctl", ["bootout", serviceTarget()]);
  if (result.status !== 0)
    throw new Error(`launchctl 停止失败 (${result.status})`);
  for (let attempt = 0; attempt < 40; attempt++) {
    if (!inspectJob(root, dependencies).loaded) return;
    await delay(100);
  }
  throw new Error("服务尚未停止，请稍后检查状态");
}
export async function startService(root, dependencies = {}) {
  const settings = loadSettings(root);
  const active = readActive(root);
  if (!active) throw new Error("尚未安装，请先选择安装/更新");
  if (!(dependencies.executableExists ?? executableExists)(settings.executable))
    throw new Error(
      `找不到可执行的 Camoufox，请安装独立 Camoufox，或在配置中填写正确路径: ${settings.executable}`,
    );
  const run = dependencies.run ?? runCommand;
  const job = inspectJob(root, dependencies);
  if (job.loaded) {
    if (!job.owned) throw new Error("该 launchd 服务属于其他数据目录，未修改");
    if (await (dependencies.health ?? health)(root, settings)) return;
    throw new Error("已加载的服务未通过健康检查，请查看诊断后重启");
  }
  if (!(await (dependencies.portAvailable ?? portAvailable)(settings.port)))
    throw new Error(
      `端口 ${settings.port} 已占用。请先关闭手动启动的助手或修改端口`,
    );
  privateDirectory(root);
  privateDirectory(join(root, "logs"));
  for (const name of ["helper.log", "helper.error.log"]) {
    const path = join(root, "logs", name);
    rejectSymlink(path);
    writeFileSync(path, "", { flag: "a", mode: 0o600 });
    chmodSync(path, 0o600);
  }
  const plist = join(root, "helper.plist");
  rejectSymlink(plist);
  writeFileSync(
    plist,
    launchPlist(root, installedPath(root, active.version), settings),
    { mode: 0o600 },
  );
  chmodSync(plist, 0o600);
  const result = run("/bin/launchctl", [
    "bootstrap",
    `gui/${process.getuid()}`,
    plist,
  ]);
  if (result.status !== 0)
    throw new Error(`launchctl 启动失败 (${result.status})，请运行 diagnose`);
  for (
    let attempt = 0;
    attempt < (dependencies.healthAttempts ?? 30);
    attempt++
  ) {
    if (await (dependencies.health ?? health)(root, settings)) return;
    await delay(200);
  }
  await stopService(root, dependencies);
  throw new Error(`助手启动后未通过健康检查，请查看 ${join(root, "logs")}`);
}
export async function restartService(root, dependencies = {}) {
  await stopService(root, dependencies);
  const settings = loadSettings(root);
  for (let attempt = 0; attempt < 50; attempt++) {
    if (await (dependencies.portAvailable ?? portAvailable)(settings.port))
      return startService(root, dependencies);
    await delay(100);
  }
  throw new Error("旧服务的端口尚未释放，请稍后启动");
}
