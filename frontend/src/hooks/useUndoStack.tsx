import { Button, message } from "antd";
import { useCallback, useEffect, useRef } from "react";

interface UndoItem {
  key: string;
  timerId: number;
}

interface EnqueueUndoOptions {
  key: string;
  content: string;
  durationMs?: number;
  onUndo: () => Promise<void>;
}

export function useUndoStack() {
  const pendingRef = useRef<Map<string, UndoItem>>(new Map());

  useEffect(() => {
    const pending = pendingRef.current;
    return () => {
      for (const item of pending.values()) {
        window.clearTimeout(item.timerId);
        message.destroy(`undo-${item.key}`);
      }
      pending.clear();
    };
  }, []);

  const enqueueUndo = useCallback((options: EnqueueUndoOptions) => {
    const durationMs = options.durationMs ?? 5000;
    const messageKey = `undo-${options.key}`;

    const timerId = window.setTimeout(() => {
      pendingRef.current.delete(options.key);
      message.destroy(messageKey);
    }, durationMs);

    pendingRef.current.set(options.key, { key: options.key, timerId });

    message.open({
      key: messageKey,
      duration: 0,
      content: (
        <span>
          {options.content}
          <Button
            type="link"
            size="small"
            style={{ marginLeft: 8, paddingInline: 4 }}
            onClick={async () => {
              const item = pendingRef.current.get(options.key);
              if (!item) return;
              window.clearTimeout(item.timerId);
              pendingRef.current.delete(options.key);
              message.destroy(messageKey);
              try {
                await options.onUndo();
              } catch {
                message.error("Undo failed — please try again.");
              }
            }}
          >
            Undo
          </Button>
        </span>
      ),
    });
  }, []);

  return { enqueueUndo };
}
