// Capture public market WebSocket frames for one inspected BTC 5m interval.
// No authentication, predictions, orders or training. Node.js 22+.
import fs from 'node:fs';
import path from 'node:path';

const [snapshotPath, outputPath, durationText = '30'] = process.argv.slice(2);
const duration = Number(durationText);
if (!snapshotPath || !outputPath || !Number.isFinite(duration) || duration < 1 || duration > 3600) {
  throw new Error('Uso: node collect_market.mjs snapshot.json captura.jsonl segundos(1..3600)');
}
const snapshot = JSON.parse(fs.readFileSync(snapshotPath, 'utf8'));
const market = snapshot.market;
if (!market || !/^btc-updown-5m-\d+$/.test(market.slug)) throw new Error('Snapshot BTC 5m inválido');
const ids = [market.outcome_ids?.Up, market.outcome_ids?.Down];
if (ids.some(id => typeof id !== 'string' || !/^\d+$/.test(id)) || new Set(ids).size !== 2) {
  throw new Error('Faltan identificadores Up/Down válidos');
}
const start = Date.parse(market.window_start_utc);
const end = Date.parse(market.window_end_utc);
if (!Number.isFinite(start) || !Number.isFinite(end) || end - start !== 300000 || Date.now() >= end) {
  throw new Error('El intervalo expiró o no es de cinco minutos; obtener un snapshot nuevo');
}
fs.mkdirSync(path.dirname(path.resolve(outputPath)), {recursive: true});
const fd = fs.openSync(outputPath, 'wx');
const write = record => fs.writeSync(fd, JSON.stringify({received_at_utc: new Date().toISOString(), ...record}) + '\n');
write({kind: 'metadata', mode: 'read_only', slug: market.slug, outcome_ids: market.outcome_ids,
       duration_seconds: duration, note: 'Raw frames; not a validated order book or training dataset'});

const counts = {};
let frames = 0;
let connected = false;
let completed = false;
let failed = false;
let heartbeat;
let timeout;
const ws = new WebSocket('wss://ws-subscriptions-clob.polymarket.com/ws/market');
await new Promise(resolve => {
  const finish = reason => {
    if (completed) return;
    completed = true;
    clearInterval(heartbeat);
    clearTimeout(timeout);
    write({kind: 'end', reason, connected, frames, counts});
    fs.closeSync(fd);
    if (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING) ws.close();
    console.log(JSON.stringify({connected, frames, counts, reason, output: path.resolve(outputPath)}));
    resolve();
  };
  timeout = setTimeout(() => { failed = true; finish('connection_timeout'); }, 12000);
  ws.addEventListener('open', () => {
    if (completed) return;
    connected = true;
    clearTimeout(timeout);
    ws.send(JSON.stringify({assets_ids: ids, type: 'market', custom_feature_enabled: true}));
    heartbeat = setInterval(() => { if (ws.readyState === WebSocket.OPEN) ws.send('PING'); }, 10000);
    timeout = setTimeout(() => finish('duration_reached'), duration * 1000);
  });
  ws.addEventListener('message', event => {
    if (completed) return;
    if (typeof event.data !== 'string') { failed = true; finish('unexpected_binary_frame'); return; }
    if (event.data === 'PONG') { write({kind: 'heartbeat', data: 'PONG'}); return; }
    // Preserve the raw frame to avoid losing fields or decimal precision.
    write({kind: 'frame', raw: event.data});
    try {
      const data = JSON.parse(event.data);
      const items = Array.isArray(data) ? data : [data];
      for (const item of items) {
        const kind = item?.event_type || 'unknown';
        counts[kind] = (counts[kind] || 0) + 1;
      }
      frames++;
    } catch {
      failed = true;
      finish('invalid_json');
    }
  });
  ws.addEventListener('error', () => { failed = true; finish('websocket_error'); });
  ws.addEventListener('close', event => {
    if (!completed) { failed = true; finish(`remote_close_${event.code}`); }
  });
  process.once('SIGINT', () => finish('user_stop'));
});
process.exitCode = failed || frames === 0 ? 1 : 0;
