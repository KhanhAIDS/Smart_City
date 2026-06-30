import { useEffect, useRef, useState } from "react";
import type { WsMessage } from "../types";

export function useLiveChannel(onMessage: (msg: WsMessage) => void) {
  const [connected, setConnected] = useState(false);
  const onMessageRef = useRef(onMessage);
  onMessageRef.current = onMessage;

  useEffect(() => {
    let ws: WebSocket | null = null;
    let timer: number | undefined;
    let delay = 1000;
    let closed = false;

    const connect = () => {
      const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
      ws = new WebSocket(`${proto}//${window.location.host}/ws`);
      ws.onopen = () => {
        setConnected(true);
        delay = 1000;
      };
      ws.onclose = () => {
        setConnected(false);
        if (!closed) {
          timer = window.setTimeout(connect, delay);
          delay = Math.min(delay * 2, 15000);
        }
      };
      ws.onmessage = (e) => {
        try {
          onMessageRef.current(JSON.parse(e.data) as WsMessage);
        } catch {
          /* ignore malformed frames */
        }
      };
    };

    connect();
    return () => {
      closed = true;
      if (timer) clearTimeout(timer);
      ws?.close();
    };
  }, []);

  return { connected };
}
