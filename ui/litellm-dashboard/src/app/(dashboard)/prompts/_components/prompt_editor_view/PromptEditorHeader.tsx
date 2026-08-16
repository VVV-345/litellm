import React from "react";
import { useTranslation } from "react-i18next";
import { ArrowLeftIcon, SaveIcon, ClockIcon, LoaderCircleIcon } from "lucide-react";
import PromptCodeSnippets from "./PromptCodeSnippets";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";

interface PromptEditorHeaderProps {
  promptName: string;
  onNameChange: (name: string) => void;
  onBack: () => void;
  onSave: () => void;
  isSaving: boolean;
  editMode?: boolean;
  onShowHistory?: () => void;
  version?: string | null;
  promptModel?: string;
  promptVariables?: Record<string, string>;
  accessToken: string | null;
  proxySettings?: {
    PROXY_BASE_URL?: string;
    LITELLM_UI_API_DOC_BASE_URL?: string | null;
  };
  environment: string;
  onEnvironmentChange: (env: string) => void;
}

const PromptEditorHeader: React.FC<PromptEditorHeaderProps> = ({
  promptName,
  onNameChange,
  onBack,
  onSave,
  isSaving,
  editMode = false,
  onShowHistory,
  version,
  promptModel = "gpt-4o",
  promptVariables = {},
  accessToken,
  proxySettings,
  environment,
  onEnvironmentChange,
}) => {
  const { t } = useTranslation();
  return (
    <div className="bg-background border-b border-border px-6 py-3 flex items-center justify-between">
      <div className="flex items-center space-x-3">
        <Button variant="ghost" onClick={onBack} size="sm">
          <ArrowLeftIcon />
          {t("ui.Back")}
        </Button>
        <Input
          aria-label={t("ui.Prompt name")}
          value={promptName}
          onChange={(e) => onNameChange(e.target.value)}
          className="text-base font-medium border-none shadow-none"
          style={{ width: "200px" }}
        />
        {version && <Badge>{version}</Badge>}
        <Select value={environment} onValueChange={(value) => onEnvironmentChange(String(value))}>
          <SelectTrigger size="sm" className="w-[140px]" aria-label={t("ui.Environment")}>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="development">{t("ui.Development")}</SelectItem>
            <SelectItem value="staging">{t("ui.Staging")}</SelectItem>
            <SelectItem value="production">{t("ui.Production")}</SelectItem>
          </SelectContent>
        </Select>
        <Badge variant="secondary">{t("ui.Draft")}</Badge>
        <span className="text-xs text-muted-foreground">{t("ui.Unsaved changes")}</span>
      </div>
      <div className="flex items-center space-x-2">
        <PromptCodeSnippets
          promptId={promptName}
          model={promptModel}
          promptVariables={promptVariables}
          accessToken={accessToken}
          version={version?.replace("v", "") || "1"}
          proxySettings={proxySettings}
        />
        {editMode && onShowHistory && (
          <Button variant="outline" onClick={onShowHistory}>
            <ClockIcon />
            {t("ui.History")}
          </Button>
        )}
        <Button onClick={onSave} disabled={isSaving}>
          {isSaving ? <LoaderCircleIcon className="animate-spin" /> : <SaveIcon />}
          {editMode ? t("ui.Update") : t("ui.Save")}
        </Button>
      </div>
    </div>
  );
};

export default PromptEditorHeader;
