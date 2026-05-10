import { useEffect, useRef, useState, useCallback } from "react";
import { reportNonCriticalError } from "../utils/monitoring";

export interface WSMessage {
  type: string;
  [key: string]: unknown;
}

export interface UseWebSocketOptions {
  onMessage?: (message: WSMessage) => void;
}

const INITIAL_RECONNECT_DELAY = 1000;
const MAX_RECONNECT_DELAY = 10000;
const MAX_RECONNECT_ATTEMPTS = 60;

// Close codes that indicate permanent rejection — no point retrying.
const PERMANENT_CLOSE_CODES = new Set([4003, 4004, 4008, 4010, 4013, 4014]);

export function useWebSocket(url: string | null, options: UseWebSocketOptions = {}) {
  const { onMessage } = options;
  const [connected, setConnected] = useState(false);
  const [lastMessage, setLastMessage] = useState<WSMessage | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const reconnectDelayRef = useRef(INITIAL_RECONNECT_DELAY);
  const activeRef = useRef(true);
  const onMessageRef = useRef(onMessage);
  const generationRef = useRef(0);
  const attemptsRef = useRef(0);
  const connectRef = useRef<() => void>(() => {});

  useEffect(() => {
    onMessageRef.current = onMessage;
  }, [onMessage]);

  const connect = useCallback(() => {
    if (!url || !activeRef.current) return;
    if (wsRef.current?.readyState === WebSocket.OPEN) return;

    const generation = ++generationRef.current;

    try {
      const ws = new WebSocket(url);
      wsRef.current = ws;

      ws.onopen = () => {
        if (!activeRef.current || generation !== generationRef.current) {
          ws.close();
          return;
        }
        setConnected(true);
        reconnectDelayRef.current = INITIAL_RECONNECT_DELAY;
        attemptsRef.current = 0;
      };

      ws.onmessage = (event) => {
        if (!activeRef.current || generation !== generationRef.current) return;
        try {
          const parsed = JSON.parse(event.data) as WSMessage;
          // Respond to server heartbeat pings to prevent idle disconnect.
          if (parsed.type === "ping") {
            ws.send("pong");
            return;
          }
          setLastMessage(parsed);
          onMessageRef.current?.(parsed);
        } catch (err) {
          reportNonCriticalError("parse websocket message", err, { raw: event.data });
        }
      };

      ws.onclose = (event) => {
        setConnected(false);
        wsRef.current = null;
        if (!activeRef.current || generation !== generationRef.current) return;

        // Permanent close — stop retrying
        if (PERMANENT_CLOSE_CODES.has(event.code)) {
          reportNonCriticalError(
            "ws_closed_permanently",
            new Error(`WebSocket closed permanently: code=${event.code}`),
            {
              code: event.code,
              reason: event.reason,
            },
          );
          return;
        }

        attemptsRef.current += 1;
        if (attemptsRef.current >= MAX_RECONNECT_ATTEMPTS) {
          reportNonCriticalError(
            "ws_max_reconnect",
            new Error(`WebSocket max reconnect attempts (${MAX_RECONNECT_ATTEMPTS}) reached`),
            {
              attempts: attemptsRef.current,
            },
          );
          return;
        }

        // Reconnect with capped exponential backoff + jitter
        reconnectDelayRef.current = Math.min(reconnectDelayRef.current * 2, MAX_RECONNECT_DELAY);
        const jitter = Math.random() * 0.3 * reconnectDelayRef.current;
        reconnectTimeoutRef.current = setTimeout(() => {
          connectRef.current();
        }, reconnectDelayRef.current + jitter);
      };

      ws.onerror = () => {
        // Let onclose handle reconnection
      };
    } catch (err) {
      reportNonCriticalError("create websocket connection", err, { url });
      if (!activeRef.current || generation !== generationRef.current) return;
      // Fallback to reconnection
      reconnectDelayRef.current = Math.min(reconnectDelayRef.current * 2, MAX_RECONNECT_DELAY);
      reconnectTimeoutRef.current = setTimeout(() => {
        connectRef.current();
      }, reconnectDelayRef.current);
    }
  }, [url]);

  useEffect(() => {
    connectRef.current = connect;
  }, [connect]);

  useEffect(() => {
    activeRef.current = true;
    connect();
    return () => {
      activeRef.current = false;
      if (reconnectTimeoutRef.current) {
        clearTimeout(reconnectTimeoutRef.current);
        reconnectTimeoutRef.current = null;
      }
      if (wsRef.current && wsRef.current.readyState !== WebSocket.CLOSED) {
        wsRef.current.close();
        wsRef.current = null;
      }
    };
  }, [connect]);

  const sendMessage = useCallback((message: unknown) => {
    const ws = wsRef.current;
    if (ws?.readyState === WebSocket.OPEN) {
      ws.send(typeof message === "string" ? message : JSON.stringify(message));
    }
  }, []);

  return { connected, lastMessage, sendMessage };
}
