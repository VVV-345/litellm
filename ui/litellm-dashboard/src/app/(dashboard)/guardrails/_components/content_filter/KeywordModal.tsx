import React from "react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { ACTION_ITEMS } from "./action_options";
import { ABOVE_ANTD_MODAL } from "./dialog_layering";
import { translateUiText } from "@/utils/i18nText";

interface KeywordModalProps {
  visible: boolean;
  keyword: string;
  action: "BLOCK" | "MASK";
  description: string;
  onKeywordChange: (keyword: string) => void;
  onActionChange: (action: "BLOCK" | "MASK") => void;
  onDescriptionChange: (description: string) => void;
  onAdd: () => void;
  onCancel: () => void;
}

const KeywordModal: React.FC<KeywordModalProps> = ({
  visible,
  keyword,
  action,
  description,
  onKeywordChange,
  onActionChange,
  onDescriptionChange,
  onAdd,
  onCancel,
}) => {
  const { t } = useTranslation();
  const ui = (text: string) => translateUiText(t, text);
  return (
    <Dialog open={visible} onOpenChange={(open) => !open && onCancel()}>
      <DialogContent className={`max-h-[calc(100dvh-2rem)] overflow-y-auto sm:max-w-[800px] ${ABOVE_ANTD_MODAL}`}>
        <DialogHeader>
          <DialogTitle>{ui("Add blocked keyword")}</DialogTitle>
        </DialogHeader>

        <div className="space-y-6">
          <div>
            <p className="font-semibold">{ui("Keyword")}</p>
            <Input
              className="mt-2"
              placeholder={ui("Enter sensitive keyword or phrase")}
              value={keyword}
              onChange={(e) => onKeywordChange(e.target.value)}
            />
          </div>

          <div>
            <p className="font-semibold">{ui("Action")}</p>
            <p className="mt-1 mb-2 text-muted-foreground">
              {ui("Choose what action the guardrail should take when this keyword is detected")}
            </p>
            <Select
              items={ACTION_ITEMS}
              value={action}
              onValueChange={(value: string | null) => value && onActionChange(value as "BLOCK" | "MASK")}
            >
              <SelectTrigger className="w-full" aria-label={ui("Action")}>
                <SelectValue />
              </SelectTrigger>
              <SelectContent alignItemWithTrigger={false}>
                {ACTION_ITEMS.map((item) => (
                  <SelectItem key={item.value} value={item.value}>
                    {ui(item.label)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div>
            <p className="font-semibold">{ui("Description (optional)")}</p>
            <Textarea
              className="mt-2 field-sizing-fixed"
              placeholder={ui("Explain why this keyword is sensitive")}
              value={description}
              onChange={(e) => onDescriptionChange(e.target.value)}
              rows={3}
            />
          </div>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={onCancel}>
            {ui("Cancel")}
          </Button>
          <Button onClick={onAdd}>{ui("Add")}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
};

export default KeywordModal;
