/** Auto-detected browser timezone (e.g. "Asia/Hong_Kong", "America/New_York"). */
const LOCAL_TZ = Intl.DateTimeFormat().resolvedOptions().timeZone;

/**
 * Format an ISO datetime string to the user's local timezone.
 */
export function formatLocalTime(
  iso: string | null | undefined,
  opts?: { dateOnly?: boolean; timeOnly?: boolean },
): string {
  if (!iso) return "-";
  try {
    const date = new Date(iso);
    if (isNaN(date.getTime())) return iso;

    const formatter = new Intl.DateTimeFormat("zh-CN", {
      timeZone: LOCAL_TZ,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: opts?.dateOnly ? undefined : "2-digit",
      minute: opts?.dateOnly ? undefined : "2-digit",
      second: opts?.dateOnly || opts?.timeOnly ? undefined : "2-digit",
      hour12: false,
    });

    const parts = formatter.formatToParts(date);
    const get = (type: string) => parts.find((p) => p.type === type)?.value ?? "";

    if (opts?.dateOnly) {
      return `${get("year")}-${get("month")}-${get("day")}`;
    }
    if (opts?.timeOnly) {
      return `${get("hour")}:${get("minute")}`;
    }
    return `${get("year")}-${get("month")}-${get("day")} ${get("hour")}:${get("minute")}:${get("second")}`;
  } catch {
    return iso;
  }
}

/**
 * Format a relative time string (Today / Yesterday / etc.) in local timezone.
 */
export function formatRelativeLocalTime(iso: string | null | undefined): string {
  if (!iso) return "-";
  try {
    const date = new Date(iso);
    const now = new Date();

    const offsetMinutes = -now.getTimezoneOffset(); // browser offset in minutes
    const msPerDay = 24 * 60 * 60 * 1000;

    const localMidnight = (d: Date) => {
      const utc = d.getTime() + d.getTimezoneOffset() * 60 * 1000;
      return Math.floor((utc + offsetMinutes * 60 * 1000) / msPerDay);
    };

    const diffDays = localMidnight(now) - localMidnight(date);
    const timeStr = formatLocalTime(iso, { timeOnly: true });

    if (diffDays === 0) return `Today at ${timeStr}`;
    if (diffDays === 1) return `Yesterday at ${timeStr}`;
    if (diffDays < 7) {
      const weekday = new Intl.DateTimeFormat("en-US", {
        timeZone: LOCAL_TZ,
        weekday: "long",
      }).format(date);
      return `${weekday} at ${timeStr}`;
    }
    return formatLocalTime(iso);
  } catch {
    return iso;
  }
}
