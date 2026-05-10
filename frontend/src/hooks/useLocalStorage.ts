import { useState, useEffect, useCallback, useRef } from "react";
import type { ZodSchema } from "zod";

const SENSITIVE_STORAGE_KEY_RE =
  /(^|[^a-z0-9])(api[-_.:]?key|apikey|access[-_.:]?token|refresh[-_.:]?token|token|secret|password|credential|authorization|session[-_.:]?id|sessionid|auth)([^a-z0-9]|$)/i;

export function looksSensitiveStorageKey(key: string): boolean {
  return SENSITIVE_STORAGE_KEY_RE.test(key);
}

// MEDIUM-13: Debounced variant for non-critical keys (topK, dateFrom, etc.)
// to reduce localStorage writes during rapid slider/input changes.
export function useDebouncedLocalStorage<T>(
  key: string,
  initialValue: T,
  debounceMs: number = 300,
): [T, (value: T | ((val: T) => T)) => void] {
  const [storedValue, setStoredValue] = useLocalStorage<T>(key, initialValue);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const debouncedSetValue = useCallback(
    (value: T | ((val: T) => T)) => {
      if (timerRef.current) clearTimeout(timerRef.current);
      timerRef.current = setTimeout(() => setStoredValue(value), debounceMs);
    },
    [setStoredValue, debounceMs],
  );

  useEffect(() => {
    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, []);

  return [storedValue, debouncedSetValue];
}

export function useLocalStorage<T>(
  key: string,
  initialValue: T,
  schema?: ZodSchema<T>,
): [T, (value: T | ((val: T) => T)) => void, boolean] {
  // L-4: Warn in dev when storing sensitive-looking keys.
  if (import.meta.env.DEV && looksSensitiveStorageKey(key)) {
    console.warn(
      `useLocalStorage: key "${key}" looks sensitive. Consider using sessionStorage or an in-memory store instead.`,
    );
  }

  // Get stored value or use initial
  const readValue = useCallback((): T => {
    if (typeof window === "undefined") {
      return initialValue;
    }
    try {
      const item = window.localStorage.getItem(key);
      let parsed: T;
      if (item !== null) {
        parsed = JSON.parse(item) as T;
        if (schema) {
          const result = schema.safeParse(parsed);
          if (!result.success) {
            if (import.meta.env.DEV) {
              console.warn(
                `useLocalStorage: schema validation failed for key "${key}":`,
                result.error,
              );
            }
            return initialValue;
          }
          parsed = result.data;
        }
      } else {
        return initialValue;
      }
      return parsed;
    } catch (err) {
      if (import.meta.env.DEV) {
        console.warn("useLocalStorage read failed for key %s:", key, err);
      }
      return initialValue;
    }
  }, [initialValue, key, schema]);

  const [storedValue, setStoredValue] = useState<T>(readValue);

  // Return a wrapped version of useState's setter function that persists to localStorage
  const setValue = useCallback(
    (value: T | ((val: T) => T)) => {
      try {
        setStoredValue((currentValue) => {
          const valueToStore = value instanceof Function ? value(currentValue) : value;
          if (typeof window !== "undefined") {
            window.localStorage.setItem(key, JSON.stringify(valueToStore));
          }
          return valueToStore;
        });
      } catch (err) {
        if (import.meta.env.DEV) {
          console.warn("useLocalStorage write failed for key %s:", key, err);
        }
      }
    },
    [key],
  );

  // Listen for changes from other tabs/windows
  useEffect(() => {
    const handleStorageChange = (event: StorageEvent) => {
      if (event.key === key) {
        if (event.newValue === null) {
          setStoredValue(initialValue);
        } else {
          try {
            const parsed = JSON.parse(event.newValue) as T;
            if (schema) {
              const result = schema.safeParse(parsed);
              if (!result.success) {
                if (import.meta.env.DEV) {
                  console.warn(
                    `useLocalStorage: cross-tab schema validation failed for key "${key}":`,
                    result.error,
                  );
                }
                return;
              }
              setStoredValue(result.data);
              return;
            }
            setStoredValue(parsed);
          } catch (err) {
            if (import.meta.env.DEV) {
              console.warn("useLocalStorage storage event parse failed for key %s:", key, err);
            }
          }
        }
      }
    };

    window.addEventListener("storage", handleStorageChange);
    return () => window.removeEventListener("storage", handleStorageChange);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- initialValue is intentionally only read on mount
  }, [key]);

  const [isPersisted] = useState(() => {
    try {
      return typeof window !== "undefined" && window.localStorage.getItem(key) !== null;
    } catch {
      return false;
    }
  });

  return [storedValue, setValue, isPersisted];
}
