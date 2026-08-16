import React from "react";
import { Copy, Info } from "lucide-react";
import { EndpointType } from "@/components/chat_ui/mode_endpoint_mapping";
import NotificationsManager from "@/components/molecules/notifications_manager";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useTranslation } from "react-i18next";

interface SessionManagementProps {
  endpointType: string;
  responsesSessionId: string | null;
  useApiSessionManagement: boolean;
  onToggleSessionManagement: (useApi: boolean) => void;
}

const SessionManagement: React.FC<SessionManagementProps> = ({
  endpointType,
  responsesSessionId,
  useApiSessionManagement,
  onToggleSessionManagement,
}) => {
  const { t } = useTranslation();
  if (endpointType !== EndpointType.RESPONSES) {
    return null;
  }

  const handleCopySessionId = async () => {
    if (responsesSessionId) {
      try {
        await navigator.clipboard.writeText(responsesSessionId);
        NotificationsManager.success(t("ui.Response ID copied to clipboard!"));
      } catch {
        NotificationsManager.error(t("ui.Unable to copy response ID"));
      }
    }
  };

  const getSessionDisplay = () => {
    if (!responsesSessionId) {
      return useApiSessionManagement ? t("ui.API Session: Ready") : t("ui.UI Session: Ready");
    }

    const sessionPrefix = useApiSessionManagement ? t("ui.Response ID") : t("ui.UI Session");
    const truncatedId = responsesSessionId.slice(0, 10);
    return `${sessionPrefix}: ${truncatedId}...`;
  };

  const getSessionDescription = () => {
    if (!responsesSessionId) {
      return useApiSessionManagement
        ? t("ui.LiteLLM will manage session using previous_response_id")
        : t("ui.UI will manage session using chat history");
    }

    return useApiSessionManagement
      ? t("ui.LiteLLM API session active - context maintained server-side")
      : t("ui.UI session active - context maintained client-side");
  };

  return (
    <div className="mb-4">
      {/* Session Management Toggle */}
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-2">
          <span className="text-sm font-medium text-gray-700">{t("ui.Session Management")}</span>
          <Tooltip>
            <TooltipTrigger aria-label={t("ui.About session management")}>
              <Info className="size-3 text-gray-400" />
            </TooltipTrigger>
            <TooltipContent>
              {t(
                "ui.Choose between LiteLLM API session management (using previous_response_id) or UI-based session management (using chat history)",
              )}
            </TooltipContent>
          </Tooltip>
        </div>
        <div className="flex items-center gap-2 text-xs text-gray-600">
          <span aria-hidden="true">UI</span>
          <Switch
            checked={useApiSessionManagement}
            onCheckedChange={onToggleSessionManagement}
            aria-label={t("ui.Use API session management")}
            size="sm"
          />
          <span aria-hidden="true">API</span>
        </div>
      </div>

      {/* Session Status Indicator */}
      <div
        className={`text-xs p-2 rounded-md ${
          responsesSessionId
            ? "bg-green-50 text-green-700 border border-green-200"
            : "bg-blue-50 text-blue-700 border border-blue-200"
        }`}
      >
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-1">
            <Info className="size-3" />
            {getSessionDisplay()}
          </div>
          {responsesSessionId && (
            <Tooltip>
              <TooltipTrigger
                render={
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon-xs"
                    onClick={handleCopySessionId}
                    aria-label={t("ui.Copy response ID")}
                    className="ml-2 hover:bg-green-100"
                  />
                }
              >
                <Copy className="size-3" />
              </TooltipTrigger>
              <TooltipContent className="max-w-lg">
                <div className="text-xs">
                  <div className="mb-1">{t("ui.Copy response ID to continue session:")}</div>
                  <div className="bg-gray-800 text-gray-100 p-2 rounded-sm font-mono text-xs whitespace-pre-wrap">
                    {`curl -X POST "your-proxy-url/v1/responses" \\
  -H "Authorization: Bearer your-api-key" \\
  -H "Content-Type: application/json" \\
  -d '{
    "model": "your-model",
    "input": [{"role": "user", "content": "your message", "type": "message"}],
    "previous_response_id": "${responsesSessionId}",
    "stream": true
  }'`}
                  </div>
                </div>
              </TooltipContent>
            </Tooltip>
          )}
        </div>
        <div className="text-xs opacity-75 mt-1">{getSessionDescription()}</div>
      </div>
    </div>
  );
};

export default SessionManagement;
