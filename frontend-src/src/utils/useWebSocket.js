import { useState, useEffect, useRef, useCallback } from 'react';

export function useWebSocket(token) {
  const ws = useRef(null);
  const listeners = useRef({});
  const subscribeQueue = useRef([]);
  const [connected, setConnected] = useState(false);
  const reconnectTimer = useRef(null);
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    return () => { mountedRef.current = false; };
  }, []);

  const connect = useCallback(() => {
    if (!token || !mountedRef.current) return;

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const url = `${protocol}//${window.location.host}/dashboard/ws?token=${encodeURIComponent(token)}`;
    const socket = new WebSocket(url);

    socket.onopen = () => {
      if (!mountedRef.current) { socket.close(); return; }
      setConnected(true);
      // Re-subscribe all active channels
      const chs = subscribeQueue.current;
      if (chs.length > 0) {
        socket.send(JSON.stringify({ type: 'subscribe', channels: chs }));
      }
    };

    socket.onmessage = (e) => {
      try {
        const msg = JSON.parse(e.data);
        const channel = msg.channel;
        if (channel) {
          const cbs = listeners.current[channel] || [];
          cbs.forEach(fn => fn(msg));
        }
        // Also dispatch to wildcard listeners
        const wildcard = listeners.current['*'] || [];
        wildcard.forEach(fn => fn(msg));
      } catch (err) {
        // ignore parse errors
      }
    };

    socket.onclose = () => {
      if (!mountedRef.current) return;
      setConnected(false);
      reconnectTimer.current = setTimeout(connect, 3000);
    };

    socket.onerror = () => {
      socket.close();
    };

    ws.current = socket;
  }, [token]);

  useEffect(() => {
    connect();
    return () => {
      if (reconnectTimer.current) clearTimeout(reconnectTimer.current);
      if (ws.current) {
        ws.current.onclose = null; // prevent reconnect
        ws.current.close();
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

  return { connected, subscribe, unsubscribe, ws: ws.current };
}
