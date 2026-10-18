import { useTranslation } from "react-i18next";

import { Badge } from "@/components/ui/badge";

const DEFAULT_USER_ID = "default_user_id";

interface DefaultProxyAdminTagProps {
  userId: string | null | undefined;
}

export default function DefaultProxyAdminTag({ userId }: DefaultProxyAdminTagProps) {
  const { t } = useTranslation();
  if (userId === DEFAULT_USER_ID) {
    return <Badge variant="secondary">{t("localization.Default Proxy Admin")}</Badge>;
  }

  return <span>{userId}</span>;
}
