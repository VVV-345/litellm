"use client";

import { ChevronDown } from "lucide-react";
import { useId, useState, type ReactNode } from "react";
import { cn } from "@/lib/cva.config";

export function DashboardSection({
  title, summary, actions, children, className, collapsible = false, expanded, onExpandedChange,
}: {
  title: ReactNode;
  summary?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
  collapsible?: boolean;
  expanded?: boolean;
  onExpandedChange?: (expanded: boolean) => void;
}) {
  const id = useId();
  const [localExpanded, setLocalExpanded] = useState(false);
  const open = !collapsible || (expanded ?? localExpanded);
  return (
    <section aria-labelledby={`${id}-title`} className={cn("min-w-0 overflow-hidden rounded-xl border bg-card shadow-sm", className)}>
      <div className="flex flex-wrap items-center justify-between gap-3 bg-muted/20 px-4 py-3">
        <div className="min-w-0">
          <h2 id={`${id}-title`} className="text-sm font-semibold">
            {collapsible ? (
              <button type="button" aria-expanded={open} aria-controls={`${id}-content`}
                className="flex items-center gap-2 rounded-sm text-left focus-visible:outline-2 focus-visible:outline-ring"
                onClick={() => (onExpandedChange ?? setLocalExpanded)(!open)}>
                <ChevronDown className={cn("size-4 shrink-0 transition-transform motion-reduce:transition-none", open && "rotate-180")} aria-hidden="true" />
                {title}
              </button>
            ) : title}
          </h2>
          {summary != null && <div className="mt-1 text-xs tabular-nums text-muted-foreground">{summary}</div>}
        </div>
        {actions != null && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
      </div>
      <div id={`${id}-content`} hidden={!open} className="min-w-0 border-t p-4">{children}</div>
    </section>
  );
}
