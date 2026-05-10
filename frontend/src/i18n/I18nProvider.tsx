import type { PropsWithChildren } from "react";
import { IntlProvider } from "react-intl";
import { type AppLocale, messages } from "./messages";

function getDefaultLocale(): AppLocale {
  const saved = localStorage.getItem("locale");
  if (saved === "en" || saved === "zh") return saved;
  const browser = navigator.language.toLowerCase();
  if (browser.startsWith("zh")) return "zh";
  // Fallback to "en" for any non-zh locale (including unsupported ones)
  return "en";
}

export function I18nProvider({ children }: PropsWithChildren) {
  const locale = getDefaultLocale();
  return (
    <IntlProvider locale={locale} messages={messages[locale]} defaultLocale="en">
      {children}
    </IntlProvider>
  );
}
