import React, { useState } from "react";
import { CopyToClipboard } from "react-copy-to-clipboard";
import { Button } from "@/components/ui/button";
import { toast } from "@/lib/toast";

interface CreatedKeyDisplayProps {
  apiKey: string;
}

/**
 * Shared component for displaying a newly-created virtual key.
 * Used on the Virtual Keys page and in the Add Agent wizard.
 */
const CreatedKeyDisplay: React.FC<CreatedKeyDisplayProps> = ({ apiKey }) => {
  const [copied, setCopied] = useState(false);
  const [visible, setVisible] = useState(false);

  const handleCopy = () => {
    setCopied(true);
    toast.success("Key copied to clipboard");
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div>
      <p className="mb-2">
        密钥默认隐藏，可以点击查看或直接复制。之后也可在虚拟密钥列表中查看自己有权限管理的密钥。
      </p>

      <p className="text-sm text-muted-foreground mt-3 mb-1">Virtual Key:</p>
      <div className="bg-muted rounded-md p-2.5 mb-2.5">
        <pre className="m-0 whitespace-normal break-words text-foreground">{visible ? apiKey : "****************"}</pre>
      </div>
      <Button type="button" variant="outline" onClick={() => setVisible(!visible)}>{visible ? "隐藏密钥" : "显示密钥"}</Button>

      <CopyToClipboard text={apiKey} onCopy={handleCopy}>
        <Button className="mt-3">{copied ? "Copied!" : "Copy Virtual Key"}</Button>
      </CopyToClipboard>
    </div>
  );
};

export default CreatedKeyDisplay;
