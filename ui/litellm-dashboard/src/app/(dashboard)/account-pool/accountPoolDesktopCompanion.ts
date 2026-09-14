import { z } from "zod";

import type { DesktopTicketCreated } from "./AccountPoolManagementApi";

const desktopInstanceShape = {
  id: z.string(),
  name: z.string(),
  userDataDir: z.string(),
  running: z.boolean(),
  initialized: z.boolean(),
  isDefault: z.boolean(),
};

export const desktopInstanceSchema = z.object(desktopInstanceShape);

export type DesktopInstance = z.infer<typeof desktopInstanceSchema>;

export const desktopStatusSchema = z.object({
  codex_instances: z.array(desktopInstanceSchema),
  cursor_instances: z.array(desktopInstanceSchema),
  codex_wsl: z.object({
    enabled: z.boolean(),
    config_dir: z.string(),
  }),
});

export type DesktopStatus = z.infer<typeof desktopStatusSchema>;

export const desktopWslResultSchema = z.object({
  enabled: z.boolean(),
  config_dir: z.string(),
  synced_current_account: z.boolean(),
});

export const buildDesktopCompanionUrl = (origin: string, ticket: DesktopTicketCreated): string => {
  const parsedOrigin = new URL(origin);
  const loopbackHost = ["localhost", "127.0.0.1", "::1"].includes(parsedOrigin.hostname);
  const loopbackHttp = parsedOrigin.protocol === "http:" && loopbackHost;
  if (parsedOrigin.protocol !== "https:" && !loopbackHttp) {
    throw new Error("Desktop companion requires HTTPS or a loopback HTTP origin");
  }
  return `cockpit-tools://account-pool/execute?server=${encodeURIComponent(parsedOrigin.origin)}&ticket_id=${encodeURIComponent(ticket.ticket_id)}#${ticket.secret}`;
};

export const openDesktopCompanion = (url: string): void => {
  const link = document.createElement("a");
  link.href = url;
  link.rel = "noreferrer";
  link.style.display = "none";
  document.body.appendChild(link);
  link.click();
  link.remove();
};
