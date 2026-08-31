import React from "react";
import { useTranslation } from "react-i18next";
import { Bot } from "lucide-react";

interface EmptyStateProps {
  hasVariables: boolean;
}

const EmptyState: React.FC<EmptyStateProps> = ({ hasVariables }) => {
  const { t } = useTranslation();
  return (
    <div className="h-full flex flex-col items-center justify-center text-muted-foreground">
      <Bot className="mb-4 size-12" aria-hidden="true" />
      <span className="text-base">
        {hasVariables
          ? t("ui.Fill in the variables above, then type a message to start testing")
          : t("ui.Type a message below to start testing your prompt")}
      </span>
    </div>
  );
};

export default EmptyState;
