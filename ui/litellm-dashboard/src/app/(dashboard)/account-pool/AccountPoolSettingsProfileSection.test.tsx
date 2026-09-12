/** 本文件验证命名配置的新增、继承切换和卡片唯一绑定交互。 */

import { useState } from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { AccountPoolSettingsProfileSection, type SettingsProfile } from "./AccountPoolSettingsProfileSection";
import type { AccountPoolEnvironment } from "./AccountPoolTypes";

type Values = { enabled: boolean };

const environment = (id: string, name: string) =>
  ({
    id,
    name,
    supplier: "openai_codex",
  }) as AccountPoolEnvironment;

const Harness = () => {
  const [globalValues, setGlobalValues] = useState<Values>({ enabled: true });
  const [profiles, setProfiles] = useState<SettingsProfile<Values>[]>([
    {
      id: "profile-one",
      name: "配置一",
      card_ids: ["card-one"],
      inherit_global: true,
      values: { enabled: true },
    },
    {
      id: "profile-two",
      name: "配置二",
      card_ids: [],
      inherit_global: false,
      values: { enabled: false },
    },
  ]);
  return (
    <AccountPoolSettingsProfileSection
      moduleName="流式传输"
      globalValues={globalValues}
      profiles={profiles}
      environments={[environment("card-one", "卡片一"), environment("card-two", "卡片二")]}
      busy={false}
      onGlobalChange={setGlobalValues}
      onProfilesChange={setProfiles}
      onProfileDeleted={() => undefined}
      onSave={() => undefined}
      renderValues={(values, onChange, disabled, editorId) => (
        <button type="button" disabled={disabled} onClick={() => onChange({ enabled: !values.enabled })}>
          {editorId}
        </button>
      )}
    />
  );
};

describe("AccountPoolSettingsProfileSection", () => {
  it("adds named configurations and prevents assigning a card twice in one module", async () => {
    const user = userEvent.setup();
    render(<Harness />);

    const cardSelectors = screen.getAllByRole("combobox", { name: /选择要应用的卡片|Select a card to apply/i });
    await user.click(cardSelectors[1]);
    expect(screen.queryByRole("option", { name: "卡片一" })).not.toBeInTheDocument();
    await user.click(await screen.findByRole("option", { name: "卡片二" }));

    expect(screen.getByText("卡片二")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /新增配置|Add configuration/i }));
    expect(screen.getByDisplayValue("流式传输 3")).toBeInTheDocument();
  });

  it("requires disabling inheritance before editing independent values", async () => {
    const user = userEvent.setup();
    render(<Harness />);

    expect(screen.getByRole("button", { name: "流式传输-profile-one" })).toBeDisabled();
    await user.click(screen.getAllByRole("switch")[0]);
    expect(screen.getByRole("button", { name: "流式传输-profile-one" })).toBeEnabled();
  });
});
