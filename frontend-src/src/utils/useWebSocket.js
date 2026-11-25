import { useState, useEffect, useRef, useCallback, useMemo } from 'react';

/* Reconnect pacing: 0.5 s, 1, 2, 4, 8, then a 15 s ceiling, with up to 25 %
   jitter so a server restart does not bring every open tab back in the same
   millisecond. */
const BASE_DELAY_MS = 500;
const MAX_DELAY_MS = 15000;

const backoffDelay = (attempt) => {
  const exp = Math.min(MAX_DELAY_MS, BASE_DELAY_MS * 2 ** attempt);
  return Math.round(exp * (1 + Math.random() * 0.25));
};

export function useWebSocket(token) {
  const ws = useRef(null);
  const listeners = useRef({});
  const subscribeQueue = useRef([]);
  const [connected, setConnected] = useState(false);
  const reconnectTimer = useRef(null);
  const attemptRef = useRef(0);
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    return () => { mountedRef.current = false; };
  }, []);

  // The reconnect path needs to call this from a timer, so it is reached
  // through a ref: the closure captured at connect time is always the current
  // token's socket factory, never a stale one.
  const connectRef = useRef(null);
  const connect = useCallback(() => {
    if (!token || !mountedRef.current) return;
    clearTimeout(reconnectTimer.current);

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const url = `${protocol}//${window.location.host}/dashboard/ws?token=${encodeURIComponent(token)}`;
    const socket = new WebSocket(url);

    socket.onopen = () => {
      if (!mountedRef.current) { socket.close(); return; }
      attemptRef.current = 0;
      setConnected(true);
      const chs = subscribeQueue.current;
      if (chs.length > 0) {
        socket.send(JSON.stringify({ type: 'subscribe', channels: chs }));
      }
    };

    socket.onmessage = (e) => {
      let msg;
      try {
        msg = JSON.parse(e.data);
      } catch {
        return;
      }
      const channel = msg.channel;
      if (channel) {
        (listeners.current[channel] || []).forEach(fn => fn(msg));
      }
      (listeners.current['*'] || []).forEach(fn => fn(msg));
    };

    socket.onclose = () => {
      if (!mountedRef.current) return;
      setConnected(false);
      const delay = backoffDelay(attemptRef.current);
      attemptRef.current = Math.min(attemptRef.current + 1, 8);
      reconnectTimer.current = setTimeout(() => connectRef.current?.(), delay);
    };

    socket.onerror = () => {
      socket.close();
    };

    ws.current = socket;
  }, [token]);

  useEffect(() => { connectRef.current = connect; }, [connect]);

  useEffect(() => {
    attemptRef.current = 0;
    connect();
    return () => {
      clearTimeout(reconnectTimer.current);
      const socket = ws.current;
      if (socket) {
        // Detach first: assigning onclose = null is what stops a closing
        // socket from scheduling another reconnect into an unmounted tree.
        socket.onclose = null;
        socket.onerror = null;
        socket.onmessage = null;
        socket.close();
        ws.current = null;
      }
      setConnected(false);
    };
  }, [connect]);

  const subscribe = useCallback((channel, callback) => {
    if (!subscribeQueue.current.includes(channel)) {
      subscribeQueue.current.push(channel);
    }
    listeners.current[channel] = [...(listeners.current[channel] || []), callback];

    if (ws.current && ws.current.readyState === WebSocket.OPEN) {
      ws.current.send(JSON.stringify({ type: 'subscribe', channels: [channel] }));
    }

    return () => {
      const cbs = listeners.current[channel] || [];
      listeners.current[channel] = cbs.filter(fn => fn !== callback);
      if (listeners.current[channel].length === 0) {
        subscribeQueue.current = subscribeQueue.current.filter(ch => ch !== channel);
        if (ws.current && ws.current.readyState === WebSocket.OPEN) {
          ws.current.send(JSON.stringify({ type: 'unsubscribe', channels: [channel] }));
        }
      }
    };
  }, []);

  const unsubscribe = useCallback((channel, callback) => {
    if (callback) {
      const cbs = listeners.current[channel] || [];
      listeners.current[channel] = cbs.filter(fn => fn !== callback);
    } else {
      delete listeners.current[channel];
    }
    subscribeQueue.current = subscribeQueue.current.filter(ch => ch !== channel);
    if (ws.current && ws.current.readyState === WebSocket.OPEN) {
      ws.current.send(JSON.stringify({ type: 'unsubscribe', channels: [channel] }));
    }
  }, []);

  // Memoised so consumers that list `wsHook` in an effect's deps do not
  // re-subscribe on every render. `ws` is the ref, not `ws.current`: the old
  // snapshot was always the socket from whichever render created it.
  return useMemo(
    () => ({ connected, subscribe, unsubscribe, ws }),
    [connected, subscribe, unsubscribe]
  );
}