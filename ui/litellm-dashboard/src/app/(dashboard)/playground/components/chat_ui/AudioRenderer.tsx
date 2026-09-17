import React from "react";
import { MessageType } from "@/components/chat_ui/types";
import { useTranslation } from "react-i18next";

interface AudioRendererProps {
  message: MessageType;
}

const AudioRenderer: React.FC<AudioRendererProps> = ({ message }) => {
  const { t } = useTranslation();
  // Check if this message contains audio
  if (!message.isAudio || typeof message.content !== "string") {
    return null;
  }

  return (
    <div className="mb-2">
      <audio controls src={message.content} className="max-w-full" style={{ maxWidth: "500px" }}>
        {t("ui.Your browser does not support the audio element.")}
      </audio>
    </div>
  );
};

export default AudioRenderer;
