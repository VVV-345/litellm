/** 本文件验证 CLIProxyAPI 插件商城与本地状态的解析和合并。 */

import { describe, expect, it } from "vitest";

import { accountPoolPluginSourceErrors, accountPoolRuntimePlugins } from "./AccountPoolPluginRuntime";

describe("accountPoolRuntimePlugins", () => {
  it("keeps store and installed versions plus runtime states", () => {
    const plugins = accountPoolRuntimePlugins(
      {
        plugins_enabled: true,
        plugins: [
          {
            store_id: "official/example",
            source_id: "official",
            source_name: "Official",
            id: "example",
            name: "Example",
            version: "2.0.0",
            installed_version: "1.0.0",
            install_type: "github-release",
            installed: true,
            configured: true,
            registered: true,
            enabled: true,
            effective_enabled: true,
            update_available: true,
          },
        ],
      },
      {
        plugins: [{ id: "example", metadata: { name: "Runtime name", version: "1.0.0" } }],
      },
    );

    expect(plugins).toEqual([
      expect.objectContaining({
        key: "official/example",
        name: "Example",
        storeVersion: "2.0.0",
        installedVersion: "1.0.0",
        sourceId: "official",
        configured: true,
        registered: true,
        enabled: true,
        effectiveEnabled: true,
        updateAvailable: true,
      }),
    ]);
  });

  it("retains locally installed plugins missing from the store", () => {
    const plugins = accountPoolRuntimePlugins(
      { plugins: [] },
      {
        plugins: [
          {
            id: "local-plugin",
            path: "/plugins/local-plugin",
            configured: true,
            registered: false,
            enabled: false,
            metadata: { name: "Local plugin", version: "0.9.0" },
          },
        ],
      },
    );

    expect(plugins).toEqual([
      expect.objectContaining({
        id: "local-plugin",
        name: "Local plugin",
        installed: true,
        installedVersion: "0.9.0",
        storeVersion: "",
      }),
    ]);
  });
});

describe("accountPoolPluginSourceErrors", () => {
  it("parses per-source store failures", () => {
    expect(
      accountPoolPluginSourceErrors({
        source_errors: [{ source_id: "community", source_name: "Community", message: "registry unavailable" }],
      }),
    ).toEqual([{ sourceId: "community", sourceName: "Community", message: "registry unavailable" }]);
  });
});
