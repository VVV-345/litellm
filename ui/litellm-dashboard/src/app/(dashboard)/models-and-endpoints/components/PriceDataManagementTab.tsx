import PriceDataReload from "@/components/price_data_reload";
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import useAuthorized from "@/app/(dashboard)/hooks/useAuthorized";
import { useModelCostMap } from "../../hooks/models/useModelCostMap";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { modelPriceCatalog, officialPricePages } from "../utils/modelPriceCatalog";

const PriceDataManagementTab = ({ initialSearch = "" }: { initialSearch?: string }) => {
  const { t } = useTranslation();
  const { accessToken } = useAuthorized();
  const { data, isError, refetch: refetchModelCostMap } = useModelCostMap();
  const [search, setSearch] = useState(initialSearch);
  const [limit, setLimit] = useState(30);
  const rows = useMemo(() => modelPriceCatalog(data), [data]);
  const filtered = useMemo(() => {
    const query = search.trim().toLowerCase();
    return rows.filter((row) =>
      [row.model, row.region, ...row.names].some((name) => name.toLowerCase().includes(query)),
    );
  }, [rows, search]);

  return (
    <div>
      <div className="p-6">
        <div className="mb-6">
          <h2 className="text-lg font-semibold">{t("ui.Price Data Management")}</h2>
          <p className="text-sm text-muted-foreground">
            {t("ui.Manage model pricing data and configure automatic reload schedules")}
          </p>
        </div>
        <PriceDataReload
          accessToken={accessToken}
          onReloadSuccess={() => {
            refetchModelCostMap();
          }}
          buttonText={t("ui.Reload Price Data")}
          size="middle"
          type="primary"
          className="w-full"
        />
        <section className="mt-6 space-y-3" aria-label={t("models.catalog.title")}>
          <h3 className="font-semibold">{t("models.catalog.title")}</h3>
          <p className="text-sm text-muted-foreground">{t("models.catalog.note")}</p>
          <Input
            aria-label={t("models.catalog.search")}
            placeholder={t("models.catalog.search")}
            value={search}
            onChange={(event) => {
              setSearch(event.target.value);
              setLimit(30);
            }}
          />
          {isError && <p role="alert">{t("models.catalog.loadFailed")}</p>}
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead>
                <tr>
                  {["model", "source", "unit", "input", "cache", "output", "tier"].map((key) => (
                    <th key={key} className="p-2">
                      {t(`models.catalog.${key}`)}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {filtered.slice(0, limit).map((row, index) => (
                  <tr key={row.model + ":" + index} className="border-t">
                    <td className="p-2">
                      {row.model}
                      {row.names.some((name) => name !== row.model) && (
                        <small className="block text-muted-foreground">
                          {row.names.filter((name) => name !== row.model).join(", ")}
                        </small>
                      )}
                    </td>
                    <td className="p-2">
                      {t(`models.catalog.${row.source}`)} {row.region}
                    </td>
                    <td className="p-2">{row.currency} / 1M</td>
                    {[row.input, row.cache, row.output].map((price, i) => (
                      <td key={i} className="p-2 tabular-nums">
                        {price == null ? t("models.catalog.unknown") : Number(price.toPrecision(12)).toString()}
                      </td>
                    ))}
                    <td className="p-2">
                      {row.tier}
                      {row.details && (
                        <details>
                          <summary>{t("models.catalog.details")}</summary>
                          <code className="break-all">{row.details}</code>
                        </details>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {!filtered.length && <p role="status">{t("models.catalog.noMatches")}</p>}
          {filtered.length > limit && (
            <Button variant="outline" onClick={() => setLimit(limit + 30)}>
              {t("models.catalog.more")}
            </Button>
          )}
          <p className="flex gap-4 text-sm">
            <a href={officialPricePages.CN} target="_blank" rel="noreferrer">
              BigModel CN (CNY)
            </a>
            <a href={officialPricePages.International} target="_blank" rel="noreferrer">
              Z.AI International (USD)
            </a>
          </p>
        </section>
      </div>
    </div>
  );
};

export default PriceDataManagementTab;
