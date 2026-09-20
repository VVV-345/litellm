/** 本文件展示回退检查的分类结论、功能影响与经过检查的备选版本。 */
import { CircleCheck, CircleHelp, ShieldCheck, ShieldX } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import type { ReleaseConfirmation } from "../../api/AccountPoolReleasesApi";

type Report = NonNullable<ReleaseConfirmation["rollback"]>;
const statuses = {
  compatible: {
    label: "检查通过",
    icon: CircleCheck,
    style:
      "border-emerald-200 bg-emerald-50 text-emerald-900 dark:border-emerald-900 dark:bg-emerald-950 dark:text-emerald-100",
  },
  blocked: {
    label: "存在风险",
    icon: ShieldX,
    style: "border-red-200 bg-red-50 text-red-900 dark:border-red-900 dark:bg-red-950 dark:text-red-100",
  },
  unverified: {
    label: "尚未验证",
    icon: CircleHelp,
    style: "border-amber-200 bg-amber-50 text-amber-900 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-100",
  },
};

export function AccountPoolRollbackReport({
  report,
  busy,
  onSelect,
}: {
  report: Report;
  busy: boolean;
  onSelect: (id: string) => void;
}) {
  const status = statuses[report.status];
  const Icon = status.icon;
  return (
    <div className="space-y-5">
      <div className={`rounded-xl border p-4 ${status.style}`}>
        <div className="flex items-center gap-2 text-base font-semibold">
          <Icon className="size-5 shrink-0" />
          {status.label}
        </div>
        <p className="mt-2 text-sm leading-6">
          {report.status === "compatible"
            ? "已通过本次静态检查。请阅读功能影响，再确认回退。"
            : "本次不能直接回退。请查看下方原因，或选择已通过检查的版本。"}
        </p>
        <div className="mt-3 grid grid-cols-2 gap-3 border-t border-current/15 pt-3 text-sm">
          <div>
            <p className="text-xs opacity-75">当前版本</p>
            <code className="mt-1 block">{report.current_commit.slice(0, 10)}</code>
          </div>
          <div>
            <p className="text-xs opacity-75">目标版本</p>
            <code className="mt-1 block">{report.target_commit.slice(0, 10)}</code>
          </div>
        </div>
      </div>
      <div className="flex items-start gap-3 rounded-xl border bg-muted/30 p-4">
        <ShieldCheck className="mt-0.5 size-5 shrink-0 text-muted-foreground" />
        <div>
          <p className="font-medium">保留当前业务数据</p>
          <p className="mt-1 text-sm leading-6 text-muted-foreground">
            密钥、认证文件、日志和费用记录继续保留。此次只切换应用镜像与已核对的部署配置，不恢复历史数据库。
          </p>
        </div>
      </div>
      <section aria-label="分类检查结果">
        <h3 className="mb-3 font-semibold">分类检查</h3>
        <div className="grid gap-3 sm:grid-cols-2">
          {report.checks.map((check) => {
            const category = statuses[check.status];
            return (
              <div key={check.key} className="min-w-0 rounded-xl border p-3.5">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <h4 className="text-sm font-medium">{check.title}</h4>
                  <Badge variant="outline" className={`shrink-0 ${category.style}`}>
                    {category.label}
                  </Badge>
                </div>
                <p className="mt-2 break-words text-sm leading-6 text-muted-foreground">{check.detail}</p>
              </div>
            );
          })}
        </div>
      </section>
      <section aria-label="回退后的功能影响" className="rounded-xl border p-4">
        <h3 className="font-semibold">回退后的功能影响</h3>
        <ul className="mt-3 space-y-2 pl-4 text-sm leading-6 text-muted-foreground">
          {report.impacts.map((impact) => (
            <li className="list-disc break-words" key={impact}>
              {impact}
            </li>
          ))}
        </ul>
      </section>
      <section aria-label="可选回退版本">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <h3 className="font-semibold">可选回退版本</h3>
          <span className="text-xs text-muted-foreground">
            另有 {report.alternatives_total ?? 0} 个备份 · 本次检查 {report.alternatives_checked ?? 0} 个
          </span>
        </div>
        {report.alternatives?.length ? (
          <div className="space-y-2">
            {report.alternatives.map((version) => (
              <div
                key={version.version_id}
                className="flex flex-wrap items-center justify-between gap-3 rounded-xl border p-3"
              >
                <div className="min-w-0 flex-1">
                  <code className="text-sm font-medium">{version.commit.slice(0, 10)}</code>
                  <p className="mt-1 break-words text-sm text-muted-foreground">{version.note || "无版本备注"}</p>
                </div>
                <Button size="sm" variant="outline" disabled={busy} onClick={() => onSelect(version.version_id)}>
                  选择并复查
                </Button>
              </div>
            ))}
          </div>
        ) : (
          <p className="rounded-xl border border-dashed p-4 text-sm leading-6 text-muted-foreground">
            本次未找到通过检查的其他备份版本。可以取消回退，继续使用当前版本。
          </p>
        )}
      </section>
      <p className="text-xs leading-5 text-muted-foreground">检查范围：{report.scope}</p>
    </div>
  );
}
