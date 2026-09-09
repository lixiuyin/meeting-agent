import { useEffect, useState, type PropsWithChildren } from "react";
import { IntlProvider } from "react-intl";
import type { AppLocale, Messages } from "./messages";

function getDefaultLocale(): AppLocale {
  try {
    const saved = window.localStorage?.getItem("locale");
    if (saved === "en" || saved === "zh") return saved;
  } catch {
    // Storage can be unavailable in privacy-restricted/embedded contexts.
  }
  const browser = globalThis.navigator?.language?.toLowerCase() ?? "en";
  if (browser.startsWith("zh")) return "zh";
  // Fallback to "en" for any non-zh locale (including unsupported ones)
  return "en";
}

export function I18nProvider({ children }: PropsWithChildren) {
  const locale = getDefaultLocale();
  const [messages, setMessages] = useState<Messages | null>(null);
  const [failed, setFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let cancelled = false;
    (locale === "zh" ? import("./locales/zh") : import("./locales/en"))
      .then((module) => {
        if (!cancelled) setMessages(module.default);
      })
      .catch(() => {
        if (!cancelled) setFailed(true);
      });
    return () => {
      cancelled = true;
    };
  }, [locale, attempt]);
  if (!messages)
    return (
      <main style={{ padding: 24 }}>
        {failed ? (
          <button
            onClick={() => {
              setFailed(false);
              setAttempt((n) => n + 1);
            }}
          >
            {locale === "zh"
              ? "语言资源加载失败，点击重试"
              : "Language resources failed to load. Retry"}
          </button>
        ) : (
          <span role="status">{locale === "zh" ? "正在加载…" : "Loading…"}</span>
        )}
      </main>
    );
  return (
    <IntlProvider locale={locale} messages={messages} defaultLocale="en">
      {children}
    </IntlProvider>
  );
}
