import { useState, useEffect, useRef } from "react";
import { checkHealth } from "../api/client";

const HEALTH_CHECK_INTERVAL = 30_000; // 30 seconds

export function useHealthCheck() {
  const [isOnline, setIsOnline] = useState(true);
  const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    let active = true;

    const ping = async () => {
      if (!active) return;
      try {
        await checkHealth();
        if (active) setIsOnline(true);
      } catch {
        if (active) setIsOnline(false);
      }
      if (active) {
        timeoutRef.current = setTimeout(ping, HEALTH_CHECK_INTERVAL);
      }
    };

    ping();

    return () => {
      active = false;
      if (timeoutRef.current) {
        clearTimeout(timeoutRef.current);
        timeoutRef.current = null;
      }
    };
  }, []);

  return isOnline;
}
