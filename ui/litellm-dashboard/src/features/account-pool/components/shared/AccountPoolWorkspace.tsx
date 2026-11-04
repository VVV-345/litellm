/** 共享的摘要标签工作区：只展开当前模块，已访问表单保留草稿。 */
"use client";

import { useId, useState, type ReactNode } from "react";
import { ChevronDown } from "lucide-react";
import { DeferredTabPanel } from "@/components/shared/DeferredTabPanel";
import { Button } from "@/components/ui/button";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";

export function AccountPoolWorkspace({
  title,
  items,
  defaultValue,
  defaultOpen = false,
}: {
  title: string;
  items: readonly { value: string; label: string; summary?: string; content: ReactNode }[];
  defaultValue?: string;
  defaultOpen?: boolean;
}) {
  const id = useId();
  const [selection, setSelection] = useState(defaultValue ?? items[0]?.value ?? "");
  const value = items.some((item) => item.value === selection) ? selection : items[0]?.value ?? "";
  const [expanded, setExpanded] = useState(defaultOpen);
  const [visited, setVisited] = useState<ReadonlySet<string>>(() => new Set(defaultOpen ? [value] : []));
  const open = (next: string) => {
    setSelection(next);
    setExpanded(true);
    setVisited((current) => new Set([...current, next]));
  };
  return (
    <section className="min-w-0 rounded-xl border bg-card" aria-label={title}>
      <h2 className="sr-only">{title}</h2>
      <Tabs value={value} onValueChange={open} className="gap-0">
        <div className="flex items-start gap-2 px-3 py-2">
          <TabsList
            variant="line"
            className="h-auto group-data-horizontal/tabs:h-auto min-w-0 flex-1 flex-wrap justify-start gap-1 rounded-none p-0"
          >
            {items.map((item) => (
              <TabsTrigger
                key={item.value}
                value={item.value}
                onClick={() => open(item.value)}
                className="h-auto min-w-0 flex-none gap-2 rounded-md px-3 py-2 text-sm"
              >
                {item.label}
                {item.summary && (
                  <span className="text-xs font-normal tabular-nums text-muted-foreground">{item.summary}</span>
                )}
              </TabsTrigger>
            ))}
          </TabsList>
          <Button
            size="sm"
            variant="ghost"
            aria-label={`${expanded ? "收起" : "展开"}${title}`}
            aria-expanded={expanded}
            aria-controls={id}
            onClick={() => (expanded ? setExpanded(false) : open(value))}
          >
            {expanded ? "收起" : "展开"}
            <ChevronDown className={`size-4 transition-transform ${expanded ? "rotate-180" : ""}`} />
          </Button>
        </div>
        <div id={id} hidden={!expanded}>
          {items.map((item) => (
            <DeferredTabPanel
              key={item.value}
              value={item.value}
              active={expanded && value === item.value}
              visited={visited.has(item.value)}
              className="m-0 min-w-0 border-t p-4"
            >
              {item.content}
            </DeferredTabPanel>
          ))}
        </div>
      </Tabs>
    </section>
  );
}
