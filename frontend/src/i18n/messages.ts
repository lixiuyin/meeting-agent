import en from "./locales/en";
import zh from "./locales/zh";

export type AppLocale = "en" | "zh";

// Derive the message key type from the English locale (source of truth).
export type Messages = { [K in keyof typeof en]: string };

export const messages: Record<AppLocale, Messages> = { en, zh };
export { en, zh };
