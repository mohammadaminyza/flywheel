import type { Metadata } from "next";
import type { ReactNode } from "react";

import { Providers } from "@/app/providers";
import { t } from "@/lib/i18n";

import "./globals.css";

export const metadata: Metadata = {
  title: t("app.title"),
  description: t("app.subtitle"),
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
