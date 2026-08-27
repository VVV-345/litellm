import { AlertTriangle, Inbox, Loader2 } from "lucide-react";
import type { ReactNode } from "react";

import { cn } from "@/lib/cva.config";

interface AccountPoolPanelHeaderProps {
  title: string;
  description: string;
  action?: ReactNode;
}

export function AccountPoolPanelHeader({ title, description, action }: AccountPoolPanelHeaderProps) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 border-b px-4 py-3">
      <div className="min-w-0">
        <h2 className="text-sm font-semibold">{title}</h2>
        <p className="mt-0.5 text-xs text-muted-foreground">{description}</p>
      </div>
      {action}
    </div>
  );
}

interface AccountPoolPanelProps extends AccountPoolPanelHeaderProps {
  children: ReactNode;
  className?: string;
}

export function AccountPoolPanel({ title, description, action, children, className }: AccountPoolPanelProps) {
  return (
    <section className={cn("min-w-0 overflow-hidden rounded-md border bg-background", className)}>
      <AccountPoolPanelHeader title={title} description={description} action={action} />
      {children}
    </section>
  );
}

interface AccountPoolQueryStateProps {
  kind: "loading" | "error" | "empty";
  message: string;
  className?: string;
}

const queryStateIcon = (kind: AccountPoolQueryStateProps["kind"]): ReactNode => {
  if (kind === "loading") return <Loader2 className="size-6 animate-spin text-muted-foreground" />;
  if (kind === "error") return <AlertTriangle className="size-7 text-destructive" />;
  return <Inbox className="size-7 text-muted-foreground" />;
};

const queryStateRole = (kind: AccountPoolQueryStateProps["kind"]): "status" | "alert" | undefined => {
  if (kind === "loading") return "status";
  if (kind === "error") return "alert";
  return undefined;
};

export function AccountPoolQueryState({ kind, message, className }: AccountPoolQueryStateProps) {
  return (
    <div
      className={cn(
        "flex min-h-72 flex-col items-center justify-center gap-2 px-4 text-center text-sm",
        kind === "error" ? "text-destructive" : "text-muted-foreground",
        className,
      )}
      role={queryStateRole(kind)}
    >
      {queryStateIcon(kind)}
      <p>{message}</p>
    </div>
  );
}
