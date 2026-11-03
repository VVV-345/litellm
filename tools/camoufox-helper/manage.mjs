import {
  existsSync,
  mkdtempSync,
  readFileSync,
  realpathSync,
  rmSync,
} from "node:fs";
import { homedir, tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { parseArgs } from "node:util";
import { createInterface } from "node:readline/promises";
import {
  DEFAULT_SETTINGS,
  executableExists,
  loadSettings,
  portAvailable,
  readActive,
  rejectSymlink,
  restartService,
  saveSettings,
  serviceStatus,
  startService,
  stopService,
} from "./manager-service.mjs";
import {
  createPackage,
  installPackage,
  rollback,
  withManagerLock,
} from "./manager-package.mjs";

const directory = dirname(fileURLToPath(import.meta.url));
const help = `macOS Camoufox 本机助手管理\n\n管理.command [命令] [参数]\nnode manage.mjs [命令] [参数]\n\ninstall     从当前目录安装，或用 --source 指定已解压的安装包目录\nconfigure   保存 --origin 网站来源，可多次指定；--port、--callback-port、--executable 可选\nstart       启动后台服务\nstop        停止受管理的助手及其浏览器\nrestart     重启后台服务\nstatus      查看版本与连接状态（支持 --json）\ndiagnose    检查 Node、npm、Camoufox、配置和服务（支持 --json）\nshow-key    显示连接 Key，仅在明确执行此命令时显示\nrollback    回退到上一个已安装版本\n\n通用参数：--data-dir /完整/路径（默认 ~/.litellm-camoufox）、--help\n--callback-port 0 清除回调端口转发\n无命令时在交互终端显示菜单；非交互调用必须指定命令\n`;
function output(value, json) {
  if (json) console.log(JSON.stringify(value, null, 2));
  else
    for (const [key, item] of Object.entries(value))
      console.log(
        `${key}: ${Array.isArray(item) ? item.join("\n  ") : item ?? "未设置"}`,
      );
}
async function diagnostics(root) {
  const issues = [];
  let status = null;
  let settings = null;
  try {
    settings = loadSettings(root);
  } catch {
    issues.push("配置不存在或无效，请运行 configure 或菜单中的配置");
  }
  const npm = join(dirname(realpathSync(process.execPath)), "npm");
  if (!existsSync(npm))
    issues.push("当前 Node 缺少配套 npm，请重新安装 Node.js 22+（包含 npm）");
  const browser = executableExists(
    settings?.executable ?? DEFAULT_SETTINGS.executable,
  );
  if (!browser)
    issues.push(
      "未找到 Camoufox，请安装独立 Camoufox，再配置其可执行文件完整路径",
    );
  try {
    status = await serviceStatus(root);
  } catch (error) {
    issues.push(error.message);
  }
  if (!status?.version) issues.push("尚未安装应用版本，请运行 install");
  if (status?.loaded && !status.owned)
    issues.push("同名 launchd 服务属于其他数据目录，请在其原目录停止后再操作");
  if (status?.loaded && !status.healthy)
    issues.push(`服务未通过健康检查，请查看 ${join(root, "logs")} 并重启`);
  if (settings && !status?.loaded && !(await portAvailable(settings.port)))
    issues.push("端口已被手动助手或其他程序占用，请先停止该程序或修改端口");
  return {
    node: process.version,
    nodePath: process.execPath,
    npm: existsSync(npm),
    camoufox: browser,
    ...status,
    issues,
  };
}
async function install(root, source) {
  if (source) return installPackage(root, resolve(source));
  if (existsSync(join(directory, "SHA256SUMS.json")))
    return installPackage(root, directory);
  const temporary = mkdtempSync(join(tmpdir(), "camoufox-package-"));
  try {
    const packageDirectory = join(temporary, "package");
    createPackage(directory, packageDirectory);
    return await installPackage(root, packageDirectory);
  } finally {
    rmSync(temporary, { recursive: true, force: true });
  }
}
function configure(root, values) {
  const previous = existsSync(join(root, "settings.json"))
    ? loadSettings(root)
    : DEFAULT_SETTINGS;
  const settings = saveSettings(root, {
    origins: values.origin ?? previous.origins,
    port: values.port === undefined ? previous.port : Number(values.port),
    callbackPort:
      values["callback-port"] === undefined
        ? previous.callbackPort
        : values["callback-port"] === "0"
          ? null
          : Number(values["callback-port"]),
    executable: values.executable ?? previous.executable,
  });
  console.log("配置已保存。服务正在运行时，请重启使新配置生效");
  return settings;
}
async function execute(command, root, values) {
  if (command === "status")
    return output(await serviceStatus(root), values.json);
  if (command === "diagnose")
    return output(await diagnostics(root), values.json);
  if (command === "show-key") {
    const path = join(root, "helper.key");
    rejectSymlink(path);
    if (!existsSync(path)) throw new Error("尚无 Key，请先配置并启动助手");
    const key = readFileSync(path, "utf8").trim();
    if (!/^[a-f0-9]{64}$/.test(key)) throw new Error("Key 文件无效");
    console.log(key);
    return;
  }
  const actions = {
    install: () => install(root, values.source),
    configure: () => configure(root, values),
    start: () => startService(root),
    stop: () => stopService(root),
    restart: () => restartService(root),
    rollback: () => rollback(root),
  };
  if (!Object.hasOwn(actions, command))
    throw new Error("未知命令，请运行 --help");
  const result = await withManagerLock(root, actions[command]);
  if (result) output(result, values.json);
  console.log("操作完成");
}
async function menu(root) {
  const terminal = createInterface({
    input: process.stdin,
    output: process.stdout,
  });
  try {
    for (;;) {
      const active = readActive(root);
      console.log(
        `\nCamoufox 本机助手 | macOS | 已安装 ${active?.version ?? "无"}\n数据目录: ${root}\n1 安装/更新当前软件包\n2 配置网站与浏览器\n3 后台启动\n4 停止\n5 重启\n6 查看状态\n7 诊断\n8 显示连接 Key\n9 从其他已解压软件包更新\n10 回退上一版本\n0 退出`,
      );
      const choice = (await terminal.question("请选择: ")).trim();
      if (choice === "0") return;
      try {
        if (choice === "2") {
          const previous = existsSync(join(root, "settings.json"))
            ? loadSettings(root)
            : DEFAULT_SETTINGS;
          const origins = await terminal.question(
            `网站来源，多个用空格分隔 [${previous.origins.join(" ")}]: `,
          );
          const port = await terminal.question(`助手端口 [${previous.port}]: `);
          const callback = await terminal.question(
            `回调转发端口，0 表示不转发 [${previous.callbackPort ?? 0}]: `,
          );
          const executable = await terminal.question(
            `Camoufox 可执行文件 [${previous.executable}]: `,
          );
          await execute("configure", root, {
            origin: origins.trim()
              ? origins.trim().split(/\s+/)
              : previous.origins,
            port: port.trim() || String(previous.port),
            "callback-port":
              callback.trim() || String(previous.callbackPort ?? 0),
            executable: executable.trim() || previous.executable,
          });
        } else if (choice === "9") {
          const source = (
            await terminal.question("已解压安装包的完整目录路径: ")
          ).trim();
          if (source) await execute("install", root, { source });
        } else {
          const command = {
            1: "install",
            3: "start",
            4: "stop",
            5: "restart",
            6: "status",
            7: "diagnose",
            8: "show-key",
            10: "rollback",
          }[choice];
          if (command) await execute(command, root, {});
          else console.log("请输入菜单中的数字");
        }
      } catch (error) {
        console.error(error.message);
      }
    }
  } finally {
    terminal.close();
  }
}
try {
  const { values, positionals } = parseArgs({
    allowPositionals: true,
    options: {
      help: { type: "boolean" },
      json: { type: "boolean" },
      source: { type: "string" },
      "data-dir": {
        type: "string",
        default: join(homedir(), ".litellm-camoufox"),
      },
      origin: { type: "string", multiple: true },
      port: { type: "string" },
      "callback-port": { type: "string" },
      executable: { type: "string" },
    },
  });
  if (values.help) console.log(help);
  else {
    if (process.platform !== "darwin")
      throw new Error("管理入口目前只支持 macOS");
    if (Number(process.versions.node.split(".")[0]) < 22)
      throw new Error(
        "需要 Node.js 22+，请用 管理.command 自动寻找已安装版本，或安装 Node.js 22+（包含 npm）",
      );
    if (positionals.length > 1) throw new Error("每次只接受一个命令");
    const root = resolve(values["data-dir"]);
    rejectSymlink(root);
    if (positionals.length) await execute(positionals[0], root, values);
    else if (process.stdin.isTTY && process.stdout.isTTY) await menu(root);
    else throw new Error("非交互调用必须指定命令，请运行 --help");
  }
} catch (error) {
  console.error(error instanceof Error ? error.message : "管理操作失败");
  process.exitCode = 1;
}
