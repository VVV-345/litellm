import { ExternalLink, Loader2 } from "lucide-react";
import i18n from "@/i18n";

import { Alert, AlertDescription } from "@/components/shared/Alert";
import { Button } from "@/components/ui/button";

interface PaginationStatusAlertsProps {
  isFetchingMore: boolean;
  cancelled: boolean;
  progress: { currentPage: number; totalPages: number };
  cancel: () => void;
  subject?: string;
}

const PaginationStatusAlerts = ({
  isFetchingMore,
  cancelled,
  progress,
  cancel,
  subject = "spend data",
}: PaginationStatusAlertsProps) => (
  <>
    {isFetchingMore && (
      <Alert variant="warning" className="mb-2">
        <AlertDescription className="flex items-center justify-between text-inherit">
          <span>
            <Loader2 className="mr-2 inline size-4 animate-spin align-text-bottom" />
            {`${i18n.t("ui.Currently fetching")} ${subject}: fetched ${progress.currentPage} / ${progress.totalPages} ${i18n.t("ui.pages. Charts will update periodically as data loads. Moving off of this page will stop and reset this. To continue using the UI in the meantime,")}`}{" "}
            <a href={window.location.href} target="_blank" rel="noopener noreferrer">
              {i18n.t("ui.open a new tab")}<ExternalLink className="inline size-3.5 align-text-bottom" />
            </a>
            .
          </span>
          <Button variant="destructive" onClick={cancel}>
            {i18n.t("ui.Stop")}</Button>
        </AlertDescription>
      </Alert>
    )}
    {cancelled && (
      <Alert variant="info" className="mb-2">
        <AlertDescription className="text-inherit">
          {`${i18n.t("ui.Showing partial")} ${subject} (${progress.currentPage}/${progress.totalPages} pages loaded)`}
        </AlertDescription>
      </Alert>
    )}
  </>
);

export default PaginationStatusAlerts;
