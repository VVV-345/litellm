/** 本文件展示紧凑的 ISP 档案卡片，并管理导入、编辑和删除动作。 */

import { Pencil, Trash2 } from "lucide-react";
import { useState, type ChangeEvent } from "react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";

import type { AccountPoolISPProxy, AccountPoolISPProxyUpdateRequest } from "../../utils/AccountPoolTypes";

interface ISPProxyCatalogPanelProps {
  profiles: AccountPoolISPProxy[];
  loading: boolean;
  failed: boolean;
  busy: boolean;
  mutationFailed: boolean;
  onImport: (content: string) => Promise<void>;
  onUpdate: (profileId: string, request: AccountPoolISPProxyUpdateRequest) => Promise<void>;
  onDelete: (profileId: string) => void;
}

interface ISPProxyDraft {
  name: string;
  protocol: AccountPoolISPProxy["protocol"];
  host: string;
  port: string;
  expires_on: string;
  username: string;
  password: string;
  enabled: boolean;
}

const draftFrom = (profile: AccountPoolISPProxy): ISPProxyDraft => ({
  name: profile.name,
  protocol: profile.protocol,
  host: profile.host,
  port: String(profile.port),
  expires_on: profile.expires_on ?? "",
  username: "",
  password: "",
  enabled: profile.enabled,
});

export const ISPProxyCatalogPanel = ({
  profiles,
  loading,
  failed,
  busy,
  mutationFailed,
  onImport,
  onUpdate,
  onDelete,
}: ISPProxyCatalogPanelProps) => {
  const { t } = useTranslation();
  const [source, setSource] = useState("");
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draft, setDraft] = useState<ISPProxyDraft | null>(null);
  const [imported, setImported] = useState(false);
  const [saving, setSaving] = useState(false);
  const [savedProfileId, setSavedProfileId] = useState<string | null>(null);

  const handleFile = async (event: ChangeEvent<HTMLInputElement>) => {
    const file: File | undefined = event.target.files?.[0];
    if (file) setSource(await file.text());
  };

  const importProfiles = async () => {
    setSaving(true);
    setImported(false);
    try {
      await onImport(source.trim());
      setSource("");
      setImported(true);
    } catch {
      setImported(false);
    } finally {
      setSaving(false);
    }
  };

  const saveProfile = async (profileId: string, request: AccountPoolISPProxyUpdateRequest) => {
    setSaving(true);
    try {
      await onUpdate(profileId, request);
      setSavedProfileId(profileId);
      setEditingId(null);
    } catch {
      setSavedProfileId(null);
    } finally {
      setSaving(false);
    }
  };

  return (
    <section className="mt-5 border-t border-border/60 pt-4" aria-labelledby="isp-proxy-catalog-title">
      <div className="flex items-center justify-between gap-3">
        <h3 id="isp-proxy-catalog-title" className="text-sm font-medium">
          {t("accountPool.proxyGateways.ispsTitle")}
        </h3>
        <span className="text-xs tabular-nums text-muted-foreground">{profiles.length}</span>
      </div>
      <div className="mt-3 grid gap-2">
        <Input
          type="file"
          accept=".txt,.csv,.yaml,.yml,.json,.conf"
          aria-label={t("accountPool.proxyGateways.ispFile")}
          onChange={(event) => void handleFile(event)}
          disabled={busy || saving}
        />
        <Textarea
          value={source}
          onChange={(event) => {
            setSource(event.target.value);
            setImported(false);
          }}
          placeholder={t("accountPool.proxyGateways.ispImportPlaceholder")}
          rows={3}
          aria-label={t("accountPool.proxyGateways.ispImport")}
          disabled={busy || saving}
        />
        <div className="flex items-center gap-2">
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={busy || saving || !source.trim()}
            onClick={() => void importProfiles()}
          >
            {t("accountPool.proxyGateways.importIsps")}
          </Button>
          {imported && (
            <span className="text-xs text-muted-foreground" role="status">
              {t("accountPool.proxyGateways.ispImported")}
            </span>
          )}
          {mutationFailed && (
            <span className="text-xs text-destructive" role="alert">
              {t("accountPool.proxyGateways.ispMutationFailed")}
            </span>
          )}
        </div>
      </div>
      {failed && (
        <p className="mt-3 text-xs text-destructive" role="alert">
          {t("accountPool.proxyGateways.ispsUnavailable")}
        </p>
      )}
      {!loading && !failed && profiles.length === 0 && (
        <p className="mt-3 text-xs text-muted-foreground">{t("accountPool.proxyGateways.ispsEmpty")}</p>
      )}
      <div className="mt-3 grid gap-2 sm:grid-cols-2">
        {profiles.map((profile) => (
          <article key={profile.id} className="min-w-0 rounded-md border border-border/70 p-3">
            <div className="flex min-w-0 items-start justify-between gap-2">
              <div className="min-w-0">
                <p className="truncate text-sm font-medium">{profile.name}</p>
                <p className="mt-0.5 truncate text-xs text-muted-foreground">
                  {profile.protocol}://{profile.host}:{profile.port}
                </p>
              </div>
              <span className={`shrink-0 text-xs ${profile.enabled ? "text-emerald-700" : "text-muted-foreground"}`}>
                {t(profile.enabled ? "accountPool.proxyGateways.ispEnabled" : "accountPool.proxyGateways.ispDisabled")}
              </span>
            </div>
            <p className="mt-2 text-xs text-muted-foreground">
              {t("accountPool.proxyGateways.ispExpires", {
                date: profile.expires_on ?? t("accountPool.proxyGateways.ispNoExpiry"),
              })}
              {profile.bound_ports.length > 0 &&
                ` · ${t("accountPool.proxyGateways.ispPorts", { ports: profile.bound_ports.join(", ") })}`}
            </p>
            {savedProfileId === profile.id && (
              <p className="mt-1 text-xs text-muted-foreground" role="status">
                {t("accountPool.proxyGateways.ispSaved")}
              </p>
            )}
            <div className="mt-2 flex justify-end gap-1">
              <Button
                type="button"
                variant="ghost"
                size="icon"
                aria-label={t("accountPool.proxyGateways.editIsp", { name: profile.name })}
                title={t("accountPool.proxyGateways.editIsp", { name: profile.name })}
                disabled={busy || saving}
                onClick={() => {
                  setEditingId(profile.id);
                  setDraft(draftFrom(profile));
                }}
              >
                <Pencil className="size-4" />
              </Button>
              <Button
                type="button"
                variant="ghost"
                size="icon"
                aria-label={t("accountPool.proxyGateways.deleteIsp", { name: profile.name })}
                title={t("accountPool.proxyGateways.deleteIsp", { name: profile.name })}
                disabled={busy || saving || profile.bound_ports.length > 0}
                onClick={() => {
                  if (window.confirm(t("accountPool.proxyGateways.deleteIspConfirm", { name: profile.name }))) {
                    onDelete(profile.id);
                  }
                }}
              >
                <Trash2 className="size-4" />
              </Button>
            </div>
            {editingId === profile.id && draft && (
              <div className="mt-2 grid grid-cols-2 gap-2 border-t border-border/60 pt-3">
                <Input
                  value={draft.name}
                  onChange={(event) => setDraft({ ...draft, name: event.target.value })}
                  aria-label={t("accountPool.proxyGateways.ispName")}
                  disabled={busy || saving}
                />
                <Select
                  value={draft.protocol}
                  onValueChange={(value) => {
                    if (value === "http" || value === "https" || value === "socks5") {
                      setDraft({ ...draft, protocol: value });
                    }
                  }}
                  disabled={busy || saving}
                >
                  <SelectTrigger aria-label={t("accountPool.proxyGateways.ispProtocol")}>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="http">HTTP</SelectItem>
                    <SelectItem value="https">HTTPS</SelectItem>
                    <SelectItem value="socks5">SOCKS5</SelectItem>
                  </SelectContent>
                </Select>
                <Input
                  value={draft.host}
                  onChange={(event) => setDraft({ ...draft, host: event.target.value })}
                  aria-label={t("accountPool.proxyGateways.ispHost")}
                  disabled={busy || saving}
                />
                <Input
                  type="number"
                  min={1}
                  max={65535}
                  value={draft.port}
                  onChange={(event) => setDraft({ ...draft, port: event.target.value })}
                  aria-label={t("accountPool.proxyGateways.ispPort")}
                  disabled={busy || saving}
                />
                <Input
                  type="date"
                  value={draft.expires_on}
                  onChange={(event) => setDraft({ ...draft, expires_on: event.target.value })}
                  aria-label={t("accountPool.proxyGateways.ispExpiration")}
                  disabled={busy || saving}
                />
                <label className="flex items-center gap-2 text-xs">
                  <input
                    type="checkbox"
                    checked={draft.enabled}
                    onChange={(event) => setDraft({ ...draft, enabled: event.target.checked })}
                    disabled={busy || saving}
                  />
                  {t("accountPool.proxyGateways.ispEnabled")}
                </label>
                <Input
                  value={draft.username}
                  onChange={(event) => setDraft({ ...draft, username: event.target.value })}
                  placeholder={t("accountPool.proxyGateways.ispUsername")}
                  aria-label={t("accountPool.proxyGateways.ispUsername")}
                  disabled={busy || saving}
                />
                <Input
                  type="password"
                  value={draft.password}
                  onChange={(event) => setDraft({ ...draft, password: event.target.value })}
                  placeholder={t("accountPool.proxyGateways.ispPassword")}
                  aria-label={t("accountPool.proxyGateways.ispPassword")}
                  disabled={busy || saving}
                />
                <div className="col-span-2 flex justify-end gap-2">
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    onClick={() => setEditingId(null)}
                    disabled={busy || saving}
                  >
                    {t("accountPool.cancel")}
                  </Button>
                  <Button
                    type="button"
                    size="sm"
                    disabled={busy || saving || !draft.name.trim() || !draft.host.trim() || !draft.port}
                    onClick={() =>
                      void saveProfile(profile.id, {
                        name: draft.name.trim(),
                        protocol: draft.protocol,
                        host: draft.host.trim(),
                        port: Number(draft.port),
                        expires_on: draft.expires_on || null,
                        enabled: draft.enabled,
                        username: draft.username || null,
                        password: draft.password || null,
                      })
                    }
                  >
                    {t("accountPool.save")}
                  </Button>
                </div>
              </div>
            )}
          </article>
        ))}
      </div>
    </section>
  );
};
