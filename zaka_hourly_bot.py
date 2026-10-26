#!/usr/bin/env python3
"""
Zaka HOURLY demo bot - fast test of the Night-profile idea on small timeframes.

 * Every hour the high and low of that hour (the 1-hour profile) become two lines. During the NEXT hour only one kind of signal is taken:
   S3 = BUY  when a candle closes above the top line,   S4 = SELL when a candle closes below the bottom line.
   (no rejection / reversal / range entries - they are removed on purpose.)
 * Same rules as the Night algo for these two signals: the candle needs >= line_close_percent of its range beyond the line, the next candle must have
   the same colour and not touch the line again (clean entry; a wick-only touch variant is the "W" one), stop at the crossing candle's far end / the line,
   nine targets 1:4 ... 1:55, 40% closed at the first and 7.5% at each of the other eight, stop to entry after target 1, to target 1 after target 3.
 * Fake money only, MEXC public candles, Python standard library only.

Run:   python zaka_hourly_bot.py          (local, shows http://127.0.0.1:8767)
Cloud: python zaka_hourly_bot.py --once   (one check then exit - used by the GitHub workflow)
Reset: python zaka_hourly_bot.py --reset
"""
import json, os, sys, time, threading, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
CONF = os.path.join(HERE, 'zaka_hourly_config.json')
STATE = os.path.join(HERE, 'zaka_hourly_state.json')
PUBLIC = os.path.join(HERE, 'public_state.json')
DEFAULT = {
    "symbols": ["BTCUSDT", "ETHUSDT", "SOLUSDT"],
    "timeframe": "5m",                 # candles the signals run on: 1m 3m... only 1m 5m 15m 30m
    "profile_hours": 1,                # length of the profile window (hours)
    "entry_hours": 1,                  # how long after the profile window the signals are taken (hours)
    "balance": 1000.0,
    "risk_percent": 1.0,
    "fee_percent": 0.05,
    "line_close_percent": 50,
    "start_from": "now",               # "now" = only new signals, "history" = also the last days
    "history_days": 2,
    "poll_seconds": 30,
    "port": 8767
}
TF = {'1m': 60, '5m': 300, '15m': 900, '30m': 1800}
IV = {'1m': '1m', '5m': '5m', '15m': '15m', '30m': '30m'}
LAD = [4, 6, 8, 12, 16, 20, 28, 40, 55]
LADW = [0.4] + [0.075] * 8
API = 'https://api.mexc.com'
RULES = 'hourly-2026-10-26.1'     # change when a rule changes: the old demo account is then erased

def net_r(g):
    """net result in R of a finished trade (None while it is open)"""
    if g['res'] == 'open': return None
    if not g.get('ladder'): return g['rr'] if g['res'] == 'win' else -1.0
    n = len(g['tps']); hit = n if g['res'] == 'win' else g['hits']
    v = sum(g['mult'][k] * LADW[k] for k in range(hit)); rem = sum(LADW[hit:])
    er = g['mult'][0] if hit >= 3 else (0.0 if hit >= 1 else -1.0)
    return v + rem * er

def line_share(k, line, d):
    """share (0..1) of a candle's full range (high to low) that lies beyond a line"""
    r = k['high'] - k['low']
    if not r > 0: return 1.0
    return max(0.0, (k['high'] - max(k['low'], line)) if d > 0 else (min(k['high'], line) - k['low'])) / r

def build(c, step, prof_h, entry_h, lpct=50.0):
    """c: closed candles. Entry block = entry_h hours; its profile = high/low of the prof_h hours just before it. Only S3 (close above top) and S4 (close below bottom)."""
    N = len(c); eb = entry_h * 3600; ph = prof_h * 3600; out = []; sess_at = [None] * N; cur = None
    for i, k in enumerate(c):
        E = k['time'] // eb * eb
        if cur is None or cur['D'] != E:
            cur = {'D': E, 'i0': i, 'i1': i, 'prof': None, 'sigs': [], 'setups': []}; out.append(cur)
        else: cur['i1'] = i
        sess_at[i] = cur
    for s in out:
        w = [k for k in c if s['D'] - ph <= k['time'] < s['D']]
        if len(w) >= 0.8 * ph / step:
            hi = max(k['high'] for k in w); lo = min(k['low'] for k in w)
            if hi > lo:
                s['prof'] = {'vah': hi, 'val': lo}
                s['setups'] = [{'n': 3, 'dir': 1, 'lvl': hi}, {'n': 4, 'dir': -1, 'lvl': lo}]
    def cross_kind(st, i):
        if i < 1 or i >= N: return 0
        k = c[i]; pv = c[i - 1]['close']
        if (pv <= st['lvl'] and k['close'] > st['lvl']) if st['dir'] > 0 else (pv >= st['lvl'] and k['close'] < st['lvl']):
            return 1 if line_share(k, st['lvl'], st['dir']) >= lpct / 100.0 - 1e-9 else 0
        pierce = (pv <= st['lvl'] and k['high'] > st['lvl'] and k['close'] <= st['lvl']) if st['dir'] > 0 else (pv >= st['lvl'] and k['low'] < st['lvl'] and k['close'] >= st['lvl'])
        if not pierce or i + 1 >= N: return 0
        b = c[i + 1]; edge = max(k['open'], k['close']) if st['dir'] > 0 else min(k['open'], k['close'])
        ok = (b['high'] <= k['high'] and b['low'] >= edge) if st['dir'] > 0 else (b['low'] >= k['low'] and b['high'] <= edge)
        return 2 if ok else 0
    def entry_at(s, t):
        q = c[t]
        for kind in (2, 1):
            i = t - 2 if kind == 2 else t - 1
            if i < s['i0']: continue
            for st in s['setups']:
                if cross_kind(st, i) != kind: continue
                if (q['low'] <= st['lvl']) if st['dir'] > 0 else (q['high'] >= st['lvl']): continue
                if (not q['close'] > q['open']) if st['dir'] > 0 else (not q['close'] < q['open']): continue
                k = c[i]; slv = min(st['lvl'], k['low']) if st['dir'] > 0 else max(st['lvl'], k['high'])
                dist = abs(q['close'] - slv)
                if not dist > 0: continue
                return {'st': st, 'slv': slv, 'dist': dist, 'wick': kind == 2}
        return None
    def mk_trade(e, z, s, t):
        entry = z['close']; d = e['st']['dir']
        T = {'i': t, 'time': z['time'], 'n': e['st']['n'], 'dir': d, 'entry': entry, 'sl': e['slv'], 'risk': e['dist'], 'rr': LAD[2], 'res': 'open', 'end': None, 'D': s['D'],
             'ladder': True, 'mult': LAD, 'tps': [entry + d * m * e['dist'] for m in LAD], 'hits': 0, 'cur': e['slv'], 'var': 'W' if e.get('wick') else ''}
        T['tp'] = T['tps'][2]; s['sigs'].append(T); return T
    L = None; last_s = None; carries = []
    for t in range(N):
        z = c[t]; s = sess_at[t]
        if s is not last_s:
            if L is not None: carries.append(L); L = None
            last_s = s
        ended = []
        for tr in ([L] if L is not None else []) + carries:
            if tr['res'] != 'open' or t <= tr['i']: continue
            hs = (z['low'] <= tr['cur']) if tr['dir'] > 0 else (z['high'] >= tr['cur'])
            if hs: tr['res'] = 'partial' if tr['hits'] > 0 else 'loss'; tr['end'] = t; ended.append(tr); continue
            while tr['hits'] < len(tr['tps']) and ((z['high'] >= tr['tps'][tr['hits']]) if tr['dir'] > 0 else (z['low'] <= tr['tps'][tr['hits']])): tr['hits'] += 1
            if tr['hits'] >= len(tr['tps']): tr['res'] = 'win'; tr['end'] = t; ended.append(tr); continue
            if tr['hits'] >= 3: tr['cur'] = tr['tps'][0]
            elif tr['hits'] >= 1: tr['cur'] = tr['entry']
        for tr in ended:
            if tr is L: L = None
        carries = [x for x in carries if x['res'] == 'open']
        if L is None and s['prof']:
            e = entry_at(s, t)
            if e: L = mk_trade(e, z, s, t)
    return out

def http_json(url, tries=4):
    err = None
    for k in range(tries):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'zaka-demo-bot'})
            with urllib.request.urlopen(req, timeout=20) as r: return json.loads(r.read().decode())
        except Exception as e:
            err = e; time.sleep(0.6 * (k + 1) ** 2)
    raise err

def fetch_range(sym, interval, start_ms, end_ms, step_s):
    W = 500 * step_s * 1000; seen = {}; a = start_ms
    while a <= end_ms:
        b = min(end_ms, a + W - 1)
        j = http_json('%s/api/v3/klines?symbol=%s&interval=%s&startTime=%d&endTime=%d&limit=500' % (API, sym, interval, a, b))
        if isinstance(j, list):
            for k in j:
                if start_ms <= k[0] <= end_ms: seen[k[0]] = k
        a += W
    return [{'time': t // 1000, 'open': float(seen[t][1]), 'high': float(seen[t][2]), 'low': float(seen[t][3]), 'close': float(seen[t][4]), 'volume': float(seen[t][5])} for t in sorted(seen)]

def load_json(path, default):
    try:
        with open(path, 'r', encoding='utf-8') as f: return json.load(f)
    except Exception: return default

def save_json(path, obj):
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f: json.dump(obj, f, indent=1)
    os.replace(tmp, path)

def calc(D):
    """balance, equity, closed and open trades (same maths as the Demo account in ZakaDesk)"""
    ev = []; bal = D['bal0']; peak = bal; dd = 0.0; wins = 0; eq = [[0, bal]]; closed = []; opened = []
    for r in D['led'].values():
        ev.append((r['t'], 1, r['key']))
        if r['net'] is not None: ev.append(((r['ct'] or r['t']), 0, r['key']))
        else: opened.append(r)
    ev.sort(key=lambda e: (e[0], e[1]))
    for t, kind, key in ev:
        r = D['led'][key]
        if kind == 1: r['amt'] = bal * D['risk'] / 100; continue
        fee_r = 2 * D['fee'] / 100 * r['entry'] / r['risk'] if r['risk'] > 0 else 0
        r['feeR'] = fee_r; r['pnl'] = (r['net'] - fee_r) * r['amt']; bal += r['pnl']
        if r['pnl'] > 0: wins += 1
        peak = max(peak, bal); dd = max(dd, (peak - bal) / peak * 100); eq.append([t, bal]); closed.append(r)
    return {'rules': D.get('rules'), 'bal0': D['bal0'], 'balance': bal, 'wins': wins, 'maxDD': dd, 'equity': eq, 'closed': closed, 'open': opened,
            'risk': D['risk'], 'fee': D['fee'], 'start': D['start']}

class Bot:
    def __init__(self, conf):
        self.c = conf; self.lock = threading.Lock(); self.status = 'starting'; self.last_poll = 0; self.errors = {}
        self.D = load_json(STATE, None)
        if self.D and self.D.get('rules') != RULES:
            print('Rules changed: the old demo account is erased.', flush=True); self.D = None
        if not self.D:
            self.D = {'bal0': conf['balance'], 'risk': conf['risk_percent'], 'fee': conf['fee_percent'], 'rules': RULES,
                      'start': 0 if conf['start_from'] == 'history' else int(time.time()), 'led': {}}
            save_json(STATE, self.D)
        self.D['risk'] = conf['risk_percent']; self.D['fee'] = conf['fee_percent']

    def poll_symbol(self, sym):
        cf = self.c; tf = cf['timeframe']; step = TF[tf]; now = time.time()
        w0 = now - cf['history_days'] * 86400
        with self.lock: olds = [r['t'] for r in self.D['led'].values() if r['sym'] == sym and r['net'] is None]
        if olds: w0 = min(w0, min(olds) - (cf['profile_hours'] + cf['entry_hours'] + 1) * 3600)
        cs = fetch_range(sym, IV[tf], int(w0 * 1000), int(now * 1000), step)
        cs = [k for k in cs if k['time'] + step <= now]
        if len(cs) < 30: return 0
        sessions = build(cs, step, cf['profile_hours'], cf['entry_hours'], cf['line_close_percent']); changed = 0
        with self.lock:
            for s in sessions:
                for g in s['sigs']:
                    if g['time'] < self.D['start']: continue
                    key = '%s|%d|%d' % (sym, g['time'], g['n']); net = net_r(g)
                    ct = cs[g['end']]['time'] if g['end'] is not None else None
                    r = self.D['led'].get(key)
                    new = {'key': key, 'sym': sym, 'dir': g['dir'], 'n': g['n'], 't': g['time'], 'entry': g['entry'], 'sl': g['sl'], 'risk': g['risk'],
                           'res': g['res'], 'hits': g.get('hits', 0), 'net': net, 'ct': ct, 'var': g.get('var', '')}
                    if r is None or any(r.get(k) != new[k] for k in ('res', 'hits', 'net', 'ct')):
                        if r is not None: new['amt'] = r.get('amt')
                        self.D['led'][key] = new; changed += 1
            if changed: save_json(STATE, self.D)
        return changed

    def loop(self):
        while True:
            t0 = time.time(); msgs = self.poll_all()
            if msgs: print(time.strftime('%d/%m %H:%M:%S'), ' | '.join(msgs), flush=True)
            time.sleep(max(1, self.c['poll_seconds'] - (time.time() - t0)))

    def poll_all(self):
        msgs = []; self.errors = {}
        for sym in self.c['symbols']:
            try:
                n = self.poll_symbol(sym)
                if n: msgs.append('%s: %d change(s)' % (sym, n))
            except Exception as e:
                msgs.append('%s: %s' % (sym, e)); self.errors[sym] = str(e)[:120]
            time.sleep(0.5)
        self.last_poll = time.time(); self.status = 'ok'
        return msgs

    def write_public(self):
        S = self.snapshot(); S['errors'] = self.errors
        old = load_json(PUBLIC, None)
        def core(x): return json.dumps({k: x.get(k) for k in ('balance', 'closed', 'open', 'errors', 'symbols', 'timeframe', 'bal0')}, sort_keys=True)
        if old and core(old) == core(S) and time.time() - old.get('lastPoll', 0) < 3600: return False
        save_json(PUBLIC, S); return True

    def snapshot(self):
        with self.lock: S = calc(json.loads(json.dumps(self.D)))
        S['symbols'] = self.c['symbols']; S['strategies'] = ['hourly']; S['rules'] = RULES; S['timeframe'] = self.c['timeframe']; S['lastPoll'] = self.last_poll or time.time()
        return S

PAGE = """<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1"><title>Zaka hourly bot</title>
<style>body{font:14px system-ui;background:#0a0e13;color:#d3dde8;margin:16px}table{border-collapse:collapse;width:100%%;max-width:760px}td,th{padding:4px 8px;border-top:1px solid #212c38;text-align:right}th:first-child,td:first-child{text-align:left}
.up{color:#22c3a0}.dn{color:#ef5a6a}h1{font-size:20px}.big{font-size:34px;font-weight:600}</style>
<h1>Zaka hourly bot</h1><div id=o>loading...</div>
<script>
const f=v=>(v>=0?'+':'-')+Math.abs(v).toFixed(2), c=v=>v>0?'up':v<0?'dn':'';
function pk(t){const d=new Date((t+18000)*1000),p=n=>String(n).padStart(2,'0');return p(d.getUTCDate())+'/'+p(d.getUTCMonth()+1)+' '+p(d.getUTCHours())+':'+p(d.getUTCMinutes())}
async function go(){try{const S=await (await fetch('/state')).json(),pl=S.balance-S.bal0;
let h='<div class=big><span class='+c(pl)+'>'+S.balance.toFixed(2)+' USDT</span></div><div>'+f(pl)+' USDT from '+S.bal0+' | '+S.closed.length+' closed, '+S.wins+' won | max drawdown '+S.maxDD.toFixed(1)+'% | open '+S.open.length+'</div><div>Pairs: '+S.symbols.join(', ')+' ('+S.timeframe+'). Rules version '+S.rules+'. Last check '+new Date(S.lastPoll*1000).toLocaleTimeString()+'</div>';
if(S.open.length)h+='<h3>Open</h3><table><tr><th>Entry (PKT)</th><th>Pair</th><th>Side</th><th>Targets hit</th></tr>'+S.open.map(r=>'<tr><td>'+pk(r.t)+'</td><td>'+r.sym+'</td><td class='+(r.dir>0?'up':'dn')+'>'+(r.dir>0?'BUY':'SELL')+' '+(r.n>=11?'VOL':'S'+r.n+(r.var?'-'+r.var:''))+'</td><td>'+r.hits+'</td></tr>').join('')+'</table>';
h+='<h3>Closed (latest 30)</h3><table><tr><th>Entry (PKT)</th><th>Pair</th><th>Side</th><th>R</th><th>P&amp;L</th></tr>'+S.closed.slice(-30).reverse().map(r=>'<tr><td>'+pk(r.t)+'</td><td>'+r.sym+'</td><td class='+(r.dir>0?'up':'dn')+'>'+(r.dir>0?'BUY':'SELL')+' '+(r.n>=11?'VOL':'S'+r.n+(r.var?'-'+r.var:''))+'</td><td class='+c(r.net-r.feeR)+'>'+(r.net-r.feeR).toFixed(2)+'</td><td class='+c(r.pnl)+'>'+f(r.pnl)+'</td></tr>').join('')+'</table>';
document.getElementById('o').innerHTML=h}catch(e){document.getElementById('o').textContent='bot not answering'}}
go();setInterval(go,10000);
</script>"""

def make_handler(bot):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def _send(self, code, body, ctype):
            b = body.encode('utf-8') if isinstance(body, str) else body
            self.send_response(code); self.send_header('Content-Type', ctype); self.send_header('Access-Control-Allow-Origin', '*')
            self.send_header('Content-Length', str(len(b))); self.end_headers(); self.wfile.write(b)
        def do_GET(self):
            if self.path.startswith('/state'): self._send(200, json.dumps(bot.snapshot()), 'application/json')
            else: self._send(200, PAGE, 'text/html; charset=utf-8')
    return H

def main():
    if '--reset' in sys.argv:
        if os.path.exists(STATE): os.remove(STATE)
        print('Demo account deleted.'); return
    conf = json.loads(json.dumps(DEFAULT))
    if os.path.exists(CONF):
        try:
            with open(CONF, encoding='utf-8-sig') as f: conf.update(json.load(f))
        except Exception as e:
            sys.exit('zaka_hourly_config.json is not valid JSON: %s' % e)
    else: save_json(CONF, DEFAULT)
    if conf['timeframe'] not in TF: sys.exit('timeframe must be one of ' + ', '.join(TF))
    bot = Bot(conf)
    if '--once' in sys.argv:
        for m in bot.poll_all(): print(m)
        print('public_state.json', 'updated' if bot.write_public() else 'unchanged'); return
    threading.Thread(target=bot.loop, daemon=True).start()
    srv = ThreadingHTTPServer(('127.0.0.1', int(conf['port'])), make_handler(bot))
    print('Zaka hourly bot is running. Open http://127.0.0.1:%d  (Ctrl+C to stop)' % conf['port'])
    try: srv.serve_forever()
    except KeyboardInterrupt: print('stopped')

if __name__ == '__main__':
    main()
