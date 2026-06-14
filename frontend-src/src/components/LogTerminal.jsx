import { useEffect, useRef, useCallback } from 'react';
import { Terminal } from '@xterm/xterm';
import { FitAddon } from '@xterm/addon-fit';
import '@xterm/xterm/css/xterm.css';
import { useWebSocket } from '../utils/useWebSocket';

function colorize(line) {
  let result = line;
  if (/ERROR|FATAL|CRITICAL/i.test(result)) result = `\x1b[31m${result}\x1b[0m`;
  else if (/WARN|WARNING/i.test(result)) result = `\x1b[33m${result}\x1b[0m`;
  else if (/INFO/i.test(result)) result = `\x1b[36m${result}\x1b[0m`;
  else if (/DEBUG/i.test(result)) result = `\x1b[2m${result}\x1b[0m`;
  return result;
}

export default function LogTerminal({
  endpoint,
  token,
  logFile = 'proxy',
  height = '400px',
  onReady,
}) {
  const terminalRef = useRef(null);
  const termInstance = useRef(null);
  const fitAddonRef = useRef(null);
  const { connected, subscribe } = useWebSocket(token);

  const writeLine = useCallback((line) => {
    const term = termInstance.current;
    if (!term) return;
    try {
      term.writeln(colorize(line));
    } catch {
      // ignore write errors after dispose
    }
  }, []);

  // Init xterm.js
  useEffect(() => {
    if (!terminalRef.current) return;

    const term = new Terminal({
      theme: {
        background: '#0a0a0a',
        foreground: '#4af626',
        cursor: '#4af626',
        selectionBackground: '#4af62644',
        black: '#0a0a0a',
        red: '#f92672',
        green: '#4af626',
        yellow: '#f9f926',
        blue: '#268bd2',
        magenta: '#fd5ff0',
        cyan: '#26f9f9',
        white: '#d0d0d0',
        brightBlack: '#555555',
        brightRed: '#f92672',
        brightGreen: '#4af626',
        brightYellow: '#f9f926',
        brightBlue: '#268bd2',
        brightMagenta: '#fd5ff0',
        brightCyan: '#26f9f9',
        brightWhite: '#ffffff',
      },
      fontSize: 12,
      fontFamily: "'JetBrains Mono', 'Cascadia Code', 'Fira Code', monospace",
      cursorBlink: false,
      cursorStyle: 'bar',
      scrollback: 50000,
      allowTransparency: false,
      cols: 120,
      rows: 20,
    });

    const fitAddon = new FitAddon();
    term.loadAddon(fitAddon);
    fitAddonRef.current = fitAddon;

    term.open(terminalRef.current);
    termInstance.current = term;

    // Fit to container after render
    const fitTimer = setTimeout(() => {
      try { fitAddon.fit(); } catch {}
    }, 100);

    // Resize observer
    const ro = new ResizeObserver(() => {
      try { fitAddon.fit(); } catch {}
    });
    ro.observe(terminalRef.current);

    if (onReady) onReady(term);

    return () => {
      clearTimeout(fitTimer);
      ro.disconnect();
      term.dispose();
      termInstance.current = null;
    };
  }, [onReady]);

  // Subscribe to log channel
  useEffect(() => {
    if (!connected || !token) return;

    const channel = endpoint
      ? `log:${logFile}:${endpoint}`
      : `log:${logFile}`;

    const unsub = subscribe(channel, (msg) => {
      if (msg.type === 'log') {
        // If filtering by endpoint, check the line contains our endpoint
        if (endpoint && !msg.line.toLowerCase().includes(endpoint.toLowerCase())) return;
        writeLine(msg.line);
      }
    });

    // Load history
    const params = new URLSearchParams({ file: `${logFile}.log`, lines: '200' });
    if (endpoint) params.set('endpoint', endpoint);
    fetch(`/dashboard/logs/history?${params}`)
      .then(r => r.json())
      .then(data => {
        if (data.lines) {
          data.lines.forEach(line => writeLine(line));
        }
      })
      .catch(() => {});

    return unsub;
  }, [connected, token, endpoint, logFile, writeLine, subscribe]);

  // Fit on window resize
  useEffect(() => {
    const handler = () => {
      try { fitAddonRef.current?.fit(); } catch {}
    };
    window.addEventListener('resize', handler);
    return () => window.removeEventListener('resize', handler);
  }, []);

  return (
    <div
      ref={terminalRef}
      style={{
        height,
        width: '100%',
        borderRadius: '12px',
        overflow: 'hidden',
        background: '#0a0a0a',
      }}
    />
  );
}
