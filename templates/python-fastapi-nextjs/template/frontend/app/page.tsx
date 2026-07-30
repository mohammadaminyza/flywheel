"use client";

import { useHealth } from "@/lib/hooks/useHealth";
import { t } from "@/lib/i18n";

export default function HomePage() {
  const { data, isPending, isError } = useHealth();

  return (
    <main className="page">
      <h1>{t("app.title")}</h1>
      <p>{t("app.subtitle")}</p>

      {isPending && <p className="muted">{t("health.loading")}</p>}
      {isError && <p className="error">{t("health.error")}</p>}
      {data && (
        <dl className="health">
          <dt>{t("health.ok")}</dt>
          <dd>{data.status}</dd>
          <dt>{t("health.version")}</dt>
          <dd>{data.version}</dd>
          <dt>{t("health.environment")}</dt>
          <dd>{data.environment}</dd>
        </dl>
      )}
    </main>
  );
}
