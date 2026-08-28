#!/usr/bin/env python3
"""Nota tassi settimanale. Genera nota.html dai dati, non dal ricordo.

   Legge:  dati.json      posizioni e serie del portafoglio (stesso file del report)
           tassi.json     storico dei tassi, una riga per venerdi
           commento.json  i paragrafi di prosa della settimana
           sq.py          per le funzioni di sconto esatte (ytm, val_accrual, _cedole)
   Scrive: nota.html

   I NUMERI li calcola questo script. La PROSA sta in commento.json e la scrive
   l'agente ogni settimana. Non invertire i due: un numero riscritto a mano e un
   numero sbagliato."""
import json, os, sys, importlib.util
from datetime import date

BASE = os.path.dirname(os.path.abspath(__file__))
P = lambda f: os.path.join(BASE, f)
load = lambda f, d=None: json.load(open(P(f), encoding='utf-8')) if os.path.exists(P(f)) else d

spec = importlib.util.spec_from_file_location("sq", P("sq.py"))
sq = importlib.util.module_from_spec(spec); spec.loader.exec_module(sq)

oggi = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else date.today()
D   = load('dati.json'); T = load('tassi.json', {}); C = load('commento.json', {})
pos = D['posizioni']

gg   = sorted(T)
cur  = T[gg[-1]]                      # lettura di oggi
prev = T[gg[-2]] if len(gg) > 1 else None   # venerdi precedente
FXR  = {'EUR': 1.0, 'CHF': cur['fx_chf'], 'USD': cur['fx_usd']}

# ── curve di mercato per valuta, interpolate fra i punti a 2 e 10 anni ──
def curva(a2, a10):
    return lambda t: a2 + (a10 - a2) * (max(t, .25) - 2) / 8
CUR = {'EUR': dict(s=1, f=curva(cur['DE2'],  cur['DE10'])),
       'CHF': dict(s=2, f=curva(cur['CH2'],  cur['CH10'])),
       'USD': dict(s=3, f=curva(cur['US2'],  cur['US10']))}
SPR = cur['spread']                   # spread creditizio per emittente, in bp

# ── book obbligazionario: carico contro mercato, flussi esatti ──
B = []; tot_amm = tot_mkt = 0.0; dur_w = val_w = ced_w = ytm_w = 0.0
agg = {}
for b in pos['bond']:
    nom = sum(x['nom'] for x in b['tranche']); pag = sum(x['pagato'] for x in b['tranche'])
    mat = date.fromisoformat(b['mat']); acq = date.fromisoformat(min(x['d'] for x in b['tranche']))
    t   = (mat - oggi).days / 365
    y_c = sq.ytm(pag / nom * 100, acq, mat, b['cedola'], b['freq'])
    k   = b['nome'].split()[0]
    y_m = (CUR[b['val']]['f'](t) + SPR.get(k, 30) / 100) / 100
    v_amm = sq.val_accrual(pag, nom, y_c, oggi, mat, b['cedola'], b['freq'])
    fl    = [(x, cf) for x, cf in sq._cedole(oggi, mat, b['cedola'], b['freq']) if x > oggi]
    v_mkt = sum(cf / (1 + y_m) ** ((x - oggi).days / 365) for x, cf in fl) * nom / 100
    fx    = FXR[b['val']]
    B.append(dict(n=b['nome'], k=k, v=b['val'], t=round(t, 2), yc=round(y_c * 100, 2),
                  ym=round(y_m * 100, 2), d=round((v_mkt - v_amm) * fx)))
    tot_amm += v_amm * fx; tot_mkt += v_mkt * fx
    # duration modificata, sui flussi reali
    pv  = sum(cf / (1 + y_c) ** ((x - oggi).days / 365) for x, cf in fl)
    mac = sum(((x - oggi).days / 365) * cf / (1 + y_c) ** ((x - oggi).days / 365) for x, cf in fl) / pv
    w   = pv * nom / 100 * fx
    dur_w += mac / (1 + y_c) * w; val_w += w; ced_w += b['cedola'] * w; ytm_w += y_c * w
    a = agg.setdefault(b['val'], [0.0, 0.0]); a[0] += w; a[1] += w * y_c
B.sort(key=lambda o: o['d'])
DUR = dur_w / val_w; CED = ced_w / val_w; YTM = ytm_w / val_w
delta = tot_mkt - tot_amm
chf_y = agg['CHF'][1] / agg['CHF'][0]; eur_y = agg['EUR'][1] / agg['EUR'][0]
costo_chf = agg['CHF'][0] * (eur_y - chf_y)

n  = lambda v, d=0: f"{v:,.{d}f}".replace(',', "'")
sg = lambda v, d=0: ('+' if v > 0 else '−') + n(abs(v), d)
bp = lambda a, b_: f"{(a-b_)*100:+.0f} bp" if b_ is not None else '—'

# ══════════════════ cambi ══════════════════
# I valori di blocco vengono dalla sera archiviata in dati.json, non da prezzi2.json:
# la nota gira dopo il report giornaliero e quel file appartiene all'altra sessione.
S_ser = D['serie']; gs = sorted(S_ser)
pz = {k: v for k, v in S_ser[gs[-1]].items() if k != '_tot'}
if len(gs) > 1: pz['_fx_prev'] = S_ser[gs[-2]].get('_fx', pz.get('_fx'))
# La serie archivia i titoli e i certificati per ticker, ma Invest Easy per NOME
# (il suo record non porta un 'tk'). build() cerca 'AMBTSQ': senza questo ponte la
# posizione sparisce e il patrimonio esce corto di 56'000 EUR senza dire niente.
ie = pos.get('invest_easy')
if ie and 'AMBTSQ' not in pz and ie['nome'] in pz: pz['AMBTSQ'] = pz[ie['nome']]
BK, _FX, _ = sq.build(pos, pz, oggi, D['costo_eur'])
patr = sum(x['val_eur'] for x in BK.values())
# rete: se il ricalcolo non ritrova il patrimonio archiviato, un prezzo non e stato
# agganciato e ogni percentuale di questa sezione sarebbe sbagliata
_tot_arch = S_ser[gs[-1]].get('_tot')
if _tot_arch and abs(patr / _tot_arch - 1) > 0.005:
    print(f"!! patrimonio ricalcolato {patr:,.0f} contro {_tot_arch:,.0f} archiviato "
          f"({(patr/_tot_arch-1)*100:+.2f}%): un prezzo non e stato agganciato, "
          f"controlla le chiavi della serie prima di pubblicare")
ESP  = {v: BK[v]['val_eur'] for v in ('CHF', 'USD')}
esp_tot = sum(ESP.values())

# tassi a breve: proxy di politica monetaria, salvo tassi monetari espliciti in tassi.json
def fed_mid(x):
    if isinstance(x, (int, float)): return float(x)
    a, _, b = str(x).partition('-')
    return (float(a) + float(b)) / 2 if b else float(a)
RB = {'EUR': cur.get('estr', cur['BCE']) / 100,
      'CHF': cur.get('saron', cur['SNB']) / 100,
      'USD': cur.get('sofr', fed_mid(cur['FED'])) / 100}
SPOT = {'CHF': 1 / cur['fx_chf'], 'USD': 1 / cur['fx_usd']}   # unita di valuta per 1 EUR

def fwd(v, mesi):
    """Cambio a termine per parita coperta. Non e una previsione: e il prezzo che
    rende indifferente comprare valuta oggi o alla scadenza."""
    T = mesi / 12
    return SPOT[v] * (1 + RB[v] * T) / (1 + RB['EUR'] * T)
ORIZ = [(3, '3 mesi'), (6, '6 mesi'), (12, '12 mesi')]
FWD  = [(lab, {v: fwd(v, m) for v in SPOT},
              {v: SPOT[v] / fwd(v, m) - 1 for v in SPOT}, m) for m, lab in ORIZ]

# lo stesso numero visto da due lati: la rinuncia di rendimento sul book in franchi
# e il premio a termine che il differenziale dei tassi paga sullo stesso nominale
prem_chf = agg['CHF'][0] * (SPOT['CHF'] / fwd('CHF', 12) - 1)

# spot: oggi, settimana precedente, inizio anno (dagli indici loc/eur dei riferimenti)
rs = D['riferimenti']['serie']; d0 = sorted(rs['CHF_loc'])[0]
base = {'CHF': rs['CHF_eur'][d0]['val'] / rs['CHF_loc'][d0]['val'],
        'USD': rs['USD_eur'][d0]['val'] / rs['USD_loc'][d0]['val']}
oggi_eur = {'CHF': cur['fx_chf'], 'USD': cur['fx_usd']}
prev_eur = {'CHF': prev['fx_chf'], 'USD': prev['fx_usd']} if prev else None

# ══════════════════ grafico 1 · book contro la curva ══════════════════
W, H = 760, 420; M = dict(t=24, r=20, b=52, l=52)
PW, PH = W - M['l'] - M['r'], H - M['t'] - M['b']
XMAX = max(4.2, max(o['t'] for o in B) + .4)
YMAX = max(5.6, max(max(o['yc'], o['ym']) for o in B) + .6)
sx = lambda v: M['l'] + v / XMAX * PW
sy = lambda v: M['t'] + (YMAX - v) / YMAX * PH
g = []
for yv in range(0, int(YMAX) + 1):
    g.append(f'<line class="grid" x1="{M["l"]}" y1="{sy(yv):.1f}" x2="{W-M["r"]}" y2="{sy(yv):.1f}"/>')
    g.append(f'<text class="tick" x="{M["l"]-9}" y="{sy(yv)+4:.1f}" text-anchor="end">{yv}%</text>')
for xv in range(0, int(XMAX) + 1):
    g.append(f'<text class="tick" x="{sx(xv):.1f}" y="{H-M["b"]+20}" text-anchor="middle">{xv}a</text>')
g.append(f'<line class="axis" x1="{M["l"]}" y1="{sy(0):.1f}" x2="{W-M["r"]}" y2="{sy(0):.1f}"/>')
for c_, c in CUR.items():
    pts = ' '.join(f'{sx(t/10):.1f},{sy(c["f"](t/10)):.1f}' for t in range(3, int(XMAX * 10) + 1))
    g.append(f'<polyline class="curve s{c["s"]}" points="{pts}"/>')
    tx = XMAX - .1
    g.append(f'<text class="curvelab s{c["s"]}t" x="{sx(tx):.1f}" y="{sy(c["f"](tx))-8:.1f}" text-anchor="end">{c_}</text>')
# etichette dirette: i tre gambi piu lunghi in valore assoluto, piu ogni titolo
# oltre 500 EUR di scarto. Scostamenti alternati per non farle collidere.
notevoli = sorted(B, key=lambda o: -abs(o['d']))[:3]
for i, o in enumerate(B):
    c = CUR[o['v']]; x, y, ym = sx(o['t']), sy(o['yc']), sy(o['ym'])
    g.append(f'<line class="stem s{c["s"]}" x1="{x:.1f}" y1="{y:.1f}" x2="{x:.1f}" y2="{ym:.1f}"/>')
    g.append(f'<circle class="dot s{c["s"]}f" cx="{x:.1f}" cy="{y:.1f}" r="5.5" tabindex="0"'
             f' data-n="{o["n"]}" data-yc="{o["yc"]}" data-ym="{o["ym"]}" data-d="{o["d"]}">'
             f'<title>{o["n"]}: carico {o["yc"]}%, mercato {o["ym"]}%</title></circle>')
    if o in notevoli:
        et = o['n'].split()[0] + ' ' + o['n'].split()[-1][-4:]
        dy = 17 if o['yc'] < o['ym'] else -13
        dx = 14 if i % 2 else -6
        g.append(f'<text class="ptlab" x="{x+dx:.1f}" y="{y+dy:.1f}" text-anchor="middle">{et}</text>')
CH1 = '\n'.join(g)

# ══════════════════ grafico 2 · storico delle curve ══════════════════
SER = [('DE10', 1, 'Bund 10a'), ('IT10', 1, 'BTP 10a'), ('US10', 3, 'UST 10a'), ('CH10', 2, 'CH 10a')]
if len(gg) >= 3:
    W2, H2 = 760, 300; M2 = dict(t=20, r=64, b=44, l=52)
    PW2, PH2 = W2 - M2['l'] - M2['r'], H2 - M2['t'] - M2['b']
    vals = [T[d][k] for d in gg for k, _, _ in SER if k in T[d]]
    lo, hi = min(vals) - .2, max(vals) + .2
    tx_ = lambda i: M2['l'] + (i / max(len(gg) - 1, 1)) * PW2
    ty_ = lambda v: M2['t'] + (hi - v) / (hi - lo) * PH2
    h = []
    for st in range(int(lo), int(hi) + 2):
        if lo <= st <= hi:
            h.append(f'<line class="grid" x1="{M2["l"]}" y1="{ty_(st):.1f}" x2="{W2-M2["r"]}" y2="{ty_(st):.1f}"/>')
            h.append(f'<text class="tick" x="{M2["l"]-9}" y="{ty_(st)+4:.1f}" text-anchor="end">{st}%</text>')
    for i, d in enumerate(gg):
        if i == 0 or i == len(gg) - 1 or len(gg) <= 8:
            h.append(f'<text class="tick" x="{tx_(i):.1f}" y="{H2-M2["b"]+19}" text-anchor="middle">{d[8:10]}.{d[5:7]}</text>')
    for k, s, lab in SER:
        pts = ' '.join(f'{tx_(i):.1f},{ty_(T[d][k]):.1f}' for i, d in enumerate(gg) if k in T[d])
        dash = ' hist2' if k == 'IT10' else ''
        h.append(f'<polyline class="hist s{s}{dash}" points="{pts}"/>')
        h.append(f'<circle class="dot s{s}f" cx="{tx_(len(gg)-1):.1f}" cy="{ty_(cur[k]):.1f}" r="4"/>')
        h.append(f'<text class="histlab s{s}t" x="{W2-M2["r"]+7}" y="{ty_(cur[k])+4:.1f}">{lab}</text>')
    CH2 = (f'<figure><div class="chartwrap"><svg viewBox="0 0 {W2} {H2}" role="img" '
           f'aria-label="Andamento dei rendimenti a dieci anni nelle settimane rilevate">'
           + '\n'.join(h) + '</svg></div>'
           '<figcaption>Un punto per venerdi. Serve a vedere se il movimento e una tendenza '
           'o rumore di una settimana.</figcaption></figure>')
else:
    CH2 = (f'<p class="nota-storico">Storico in costruzione: {len(gg)} rilevazione'
           f'{"i" if len(gg)>1 else ""} su tre necessarie per il grafico. '
           'La tabella qui sopra le riporta tutte.</p>')

# ══════════════════ grafico 3 · spot realizzato e curva a termine ══════════════════
# Valore in euro di un'unita di valuta, base 100 oggi. A sinistra dello zero cio che
# e successo, a destra cio che il differenziale dei tassi prezza. Man mano che le
# rilevazioni si accumulano il tratto pieno invade il territorio che il tratteggio
# aveva previsto: e li che si legge se la parita coperta sta reggendo.
W3, H3 = 760, 300; M3 = dict(t=20, r=70, b=44, l=52)
PW3, PH3 = W3 - M3['l'] - M3['r'], H3 - M3['t'] - M3['b']
mesi_di = lambda d: (date.fromisoformat(d) - oggi).days / 30.44
X0 = min([mesi_di(d) for d in gg] + [0]) - .3
X1 = 12.3
serie_fx = {v: [(mesi_di(d), T[d]['fx_' + v.lower()] / oggi_eur[v] * 100) for d in gg]
            for v in ('CHF', 'USD')}
serie_fw = {v: [(m, SPOT[v] / fwd(v, m) * 100) for m in range(0, 13)] for v in ('CHF', 'USD')}
tutti = [y for v in serie_fx for _, y in serie_fx[v] + serie_fw[v]]
L0, L1 = min(tutti) - .4, max(tutti) + .4
ax = lambda m: M3['l'] + (m - X0) / (X1 - X0) * PW3
ay = lambda y: M3['t'] + (L1 - y) / (L1 - L0) * PH3
k = []
for st in [x / 2 for x in range(int(L0 * 2), int(L1 * 2) + 2)]:
    if L0 <= st <= L1:
        k.append(f'<line class="grid" x1="{M3["l"]}" y1="{ay(st):.1f}" x2="{W3-M3["r"]}" y2="{ay(st):.1f}"/>')
        k.append(f'<text class="tick" x="{M3["l"]-9}" y="{ay(st)+4:.1f}" text-anchor="end">{st:.1f}</text>')
for m in range(0, 13, 3):
    k.append(f'<text class="tick" x="{ax(m):.1f}" y="{H3-M3["b"]+19}" text-anchor="middle">{"oggi" if m==0 else f"+{m}m"}</text>')
k.append(f'<line class="axis" x1="{ax(0):.1f}" y1="{M3["t"]}" x2="{ax(0):.1f}" y2="{H3-M3["b"]:.1f}"/>')
for v, s in (('CHF', 2), ('USD', 3)):
    if len(serie_fx[v]) > 1:
        k.append(f'<polyline class="hist s{s}" points="'
                 + ' '.join(f'{ax(m):.1f},{ay(y):.1f}' for m, y in serie_fx[v]) + '"/>')
    for m, y in serie_fx[v]:
        k.append(f'<circle class="dot s{s}f" cx="{ax(m):.1f}" cy="{ay(y):.1f}" r="3.5"/>')
    k.append(f'<polyline class="hist s{s} hist2" points="'
             + ' '.join(f'{ax(m):.1f},{ay(y):.1f}' for m, y in serie_fw[v]) + '"/>')
    yf = serie_fw[v][-1][1]
    k.append(f'<text class="histlab s{s}t" x="{W3-M3["r"]+7}" y="{ay(yf)+4:.1f}">{v} {yf-100:+.1f}%</text>')
CH3 = '\n'.join(k)

# ── tabella storico, sempre presente: e anche la vista tabellare del grafico ──
righe_st = ''.join(
    '<tr><td class="nm">' + d[8:10] + '.' + d[5:7] + '.' + d[:4] + '</td>'
    + ''.join(f'<td>{T[d].get(k, "—") if isinstance(T[d].get(k), str) else format(T[d][k], ".2f") + "%" if k in T[d] else "—"}</td>'
              for k in ('BCE', 'DE2', 'DE10', 'IT10', 'US2', 'US10', 'CH10'))
    + f'<td>{(T[d]["IT10"]-T[d]["DE10"])*100:.0f} bp</td></tr>'
    for d in reversed(gg))

righe_b = ''.join(
    f'<tr><td class="nm">{o["n"]}</td><td class="cur s{CUR[o["v"]]["s"]}t">{o["v"]}</td>'
    f'<td>{o["t"]:.2f}</td><td>{o["yc"]:.2f}%</td><td>{o["ym"]:.2f}%</td>'
    f'<td class="{"neg" if o["d"]<0 else "pos"}">{sg(o["d"])}</td></tr>' for o in B)

par = lambda k: ''.join(f'<p>{x}</p>' for x in C.get(k, []))
IT = ['gennaio','febbraio','marzo','aprile','maggio','giugno','luglio','agosto',
      'settembre','ottobre','novembre','dicembre']
datalunga = f"{oggi.day} {IT[oggi.month-1]} {oggi.year}"

HTML = f'''<title>Il Book Contro la Curva</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Newsreader:ital,opsz,wght@0,6..72,400;0,6..72,600;1,6..72,400&family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap">
<style>
:root{{
  --ground:#F2F3F5; --surface:#FBFBFC; --ink:#14181F; --ink2:#4C5563; --muted:#7B8492;
  --rule:#DEE1E6; --hair:#E8EAEE; --grid:#E4E7EB; --axis:#C6CBD3;
  --s1:#2a78d6; --s2:#eb6834; --s3:#1baf7a; --neg:#d03b3b; --pos:#0ca30c;
  color-scheme:light;
}}
@media (prefers-color-scheme:dark){{:root:not([data-theme="light"]){{
  --ground:#0E1116; --surface:#171B22; --ink:#E9ECF1; --ink2:#A7B0BC; --muted:#79828F;
  --rule:#262C35; --hair:#1F242C; --grid:#232932; --axis:#333B45;
  --s1:#3987e5; --s2:#d95926; --s3:#199e70; --neg:#e66767; --pos:#0ca30c;
  color-scheme:dark;
}}}}
:root[data-theme="dark"]{{
  --ground:#0E1116; --surface:#171B22; --ink:#E9ECF1; --ink2:#A7B0BC; --muted:#79828F;
  --rule:#262C35; --hair:#1F242C; --grid:#232932; --axis:#333B45;
  --s1:#3987e5; --s2:#d95926; --s3:#199e70; --neg:#e66767; --pos:#0ca30c;
  color-scheme:dark;
}}
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:var(--ground);color:var(--ink);
  font:400 16.5px/1.6 "IBM Plex Sans",system-ui,-apple-system,sans-serif;
  -webkit-font-smoothing:antialiased;padding:40px 20px 72px}}
.wrap{{max-width:820px;margin:0 auto;display:flex;flex-direction:column;gap:38px}}
.prose{{max-width:64ch;display:flex;flex-direction:column;gap:15px}}
.eyebrow{{font:500 11px/1 "IBM Plex Mono",monospace;letter-spacing:.13em;
  text-transform:uppercase;color:var(--muted)}}
h1{{font:400 clamp(34px,5.2vw,52px)/1.08 Newsreader,Georgia,serif;letter-spacing:-.015em;
  text-wrap:balance;margin:14px 0 0}}
.standfirst{{font:400 19px/1.55 Newsreader,Georgia,serif;color:var(--ink2);max-width:56ch;margin-top:14px}}
h2{{font:600 13px/1.3 "IBM Plex Mono",monospace;letter-spacing:.1em;text-transform:uppercase;
  color:var(--muted);padding-bottom:9px;border-bottom:1px solid var(--rule);margin-bottom:20px}}
section{{display:flex;flex-direction:column;gap:16px}}
p{{max-width:64ch}}
strong{{font-weight:600}}
.num{{font-family:"IBM Plex Mono",monospace;font-variant-numeric:tabular-nums}}
.hero{{display:flex;flex-wrap:wrap;gap:34px;padding:22px 24px;background:var(--surface);
  border:1px solid var(--hair);border-radius:3px}}
.hero div{{display:flex;flex-direction:column;gap:3px}}
.hero .k{{font:500 10.5px/1 "IBM Plex Mono",monospace;letter-spacing:.1em;
  text-transform:uppercase;color:var(--muted)}}
.hero .v{{font:500 25px/1.15 "IBM Plex Mono",monospace;letter-spacing:-.02em}}
.hero .n{{font-size:12.5px;color:var(--muted)}}
.tw{{overflow-x:auto;background:var(--surface);border:1px solid var(--hair);border-radius:3px}}
table{{width:100%;border-collapse:collapse;min-width:520px}}
th,td{{text-align:right;padding:9px 14px;font-size:13.5px;
  font-family:"IBM Plex Mono",monospace;font-variant-numeric:tabular-nums;white-space:nowrap}}
th{{font-size:10px;letter-spacing:.09em;text-transform:uppercase;color:var(--muted);
  font-weight:500;border-bottom:1px solid var(--rule);padding-top:13px}}
td{{border-bottom:1px solid var(--hair)}}
tr:last-child td{{border-bottom:0}}
th:first-child,td:first-child{{text-align:left}}
td.nm{{font-family:"IBM Plex Sans",sans-serif;white-space:normal;min-width:170px}}
.cur{{font-weight:500}}
.neg{{color:var(--neg)}} .pos{{color:var(--pos)}}
figure{{background:var(--surface);border:1px solid var(--hair);border-radius:3px;padding:18px 6px 10px}}
figcaption{{font-size:12.5px;color:var(--muted);padding:12px 16px 4px;max-width:66ch;line-height:1.5}}
.chartwrap{{overflow-x:auto}}
svg{{display:block;min-width:660px;margin:0 auto}}
.grid{{stroke:var(--grid);stroke-width:1}}
.axis{{stroke:var(--axis);stroke-width:1}}
.tick{{font:400 11px "IBM Plex Mono",monospace;fill:var(--muted)}}
.curve{{fill:none;stroke-width:2;stroke-linecap:round;opacity:.62;stroke-dasharray:5 4}}
.hist{{fill:none;stroke-width:2;stroke-linecap:round;stroke-linejoin:round}}
.hist2{{stroke-dasharray:5 4}}
.curvelab,.histlab{{font:500 11.5px "IBM Plex Mono",monospace;letter-spacing:.04em}}
.ptlab{{font:400 11px "IBM Plex Sans",sans-serif;fill:var(--ink2)}}
.stem{{stroke-width:1.5;opacity:.42}}
.dot{{stroke:var(--surface);stroke-width:2;cursor:pointer}}
.dot:hover{{stroke-width:3}}
.s1{{stroke:var(--s1)}} .s2{{stroke:var(--s2)}} .s3{{stroke:var(--s3)}}
.s1f{{fill:var(--s1)}} .s2f{{fill:var(--s2)}} .s3f{{fill:var(--s3)}}
.s1t{{fill:var(--s1);color:var(--s1)}} .s2t{{fill:var(--s2);color:var(--s2)}} .s3t{{fill:var(--s3);color:var(--s3)}}
.legend{{display:flex;flex-wrap:wrap;gap:18px;padding:4px 16px 0}}
.legend span{{display:flex;align-items:center;gap:7px;font-size:12.5px;color:var(--ink2)}}
.sw{{width:11px;height:11px;border-radius:50%;flex:none}}
.nota-storico{{font-size:13px;color:var(--muted);padding:14px 16px;background:var(--surface);
  border:1px solid var(--hair);border-radius:3px;max-width:none}}
#tip{{position:fixed;pointer-events:none;opacity:0;transition:opacity .12s;
  background:var(--ink);color:var(--ground);padding:8px 11px;border-radius:3px;
  font:400 12px/1.5 "IBM Plex Mono",monospace;z-index:9;white-space:nowrap}}
.foot{{font-size:12.5px;color:var(--muted);line-height:1.6;max-width:66ch;
  border-top:1px solid var(--rule);padding-top:16px}}
a{{color:inherit}}
:focus-visible{{outline:2px solid var(--s1);outline-offset:2px}}
@media (prefers-reduced-motion:reduce){{*{{transition:none!important}}}}
</style>
<div class="wrap">
<header class="prose">
  <div class="eyebrow">Nota tassi · {datalunga}</div>
  <h1>Il book contro la curva</h1>
  <p class="standfirst">{C.get('standfirst','')}</p>
</header>

<section>
  <h2>Il numero</h2>
  <div class="hero">
    <div><span class="k">Costo ammortizzato</span><span class="v">{n(tot_amm)}</span><span class="n">EUR · come lo espone il report</span></div>
    <div><span class="k">Stima a mercato</span><span class="v">{n(tot_mkt)}</span><span class="n">EUR · curva del {oggi.strftime('%d.%m')}</span></div>
    <div><span class="k">Differenza</span><span class="v {'neg' if delta<0 else 'pos'}">{sg(delta)}</span><span class="n">{delta/tot_amm*100:+.2f}% · non contabilizzata</span></div>
  </div>
  <p>Il book e corto — <strong>duration modificata {DUR:.2f} anni</strong> — e questo lo protegge.
  Un rialzo parallelo di 50 punti base vale <span class="num neg">{sg(-val_w*DUR*0.005)} EUR</span>,
  uno di 100 vale <span class="num neg">{sg(-val_w*DUR*0.01)} EUR</span>. Non recuperi con la
  cedola: la cedola media ponderata e <strong>{CED:.2f}%</strong> contro un rendimento a
  scadenza del {YTM*100:.2f}%. E un book di zero-coupon e quasi-zero comprati a sconto,
  rende a scadenza e non paga quasi nulla per strada.</p>
</section>

<section>
  <h2>Il regime</h2>
  {par('regime')}
  <div class="tw"><table>
    <tr><th>Curva</th><th>2 anni</th><th>10 anni</th><th>10−2</th><th>su settimana</th></tr>
    <tr><td class="nm">Germania</td><td>{cur['DE2']:.2f}%</td><td>{cur['DE10']:.2f}%</td>
        <td>{(cur['DE10']-cur['DE2'])*100:+.0f} bp</td>
        <td class="{'neg' if prev and cur['DE10']>prev['DE10'] else 'pos' if prev else ''}">{bp(cur['DE10'], prev['DE10'] if prev else None)}</td></tr>
    <tr><td class="nm">Italia</td><td>—</td><td>{cur['IT10']:.2f}%</td><td>—</td>
        <td class="{'neg' if prev and cur['IT10']>prev['IT10'] else 'pos' if prev else ''}">{bp(cur['IT10'], prev['IT10'] if prev else None)}</td></tr>
    <tr><td class="nm">Stati Uniti</td><td>{cur['US2']:.2f}%</td><td>{cur['US10']:.2f}%</td>
        <td>{(cur['US10']-cur['US2'])*100:+.0f} bp</td>
        <td class="{'neg' if prev and cur['US10']>prev['US10'] else 'pos' if prev else ''}">{bp(cur['US10'], prev['US10'] if prev else None)}</td></tr>
    <tr><td class="nm">Svizzera</td><td>{cur['CH2']:.2f}%</td><td>{cur['CH10']:.2f}%</td>
        <td>{(cur['CH10']-cur['CH2'])*100:+.0f} bp</td>
        <td class="{'neg' if prev and cur['CH10']>prev['CH10'] else 'pos' if prev else ''}">{bp(cur['CH10'], prev['CH10'] if prev else None)}</td></tr>
    <tr><td class="nm">Spread BTP-Bund</td><td>—</td><td>{(cur['IT10']-cur['DE10'])*100:.0f} bp</td><td>—</td>
        <td>{f"{((cur['IT10']-cur['DE10'])-(prev['IT10']-prev['DE10']))*100:+.0f} bp" if prev else '—'}</td></tr>
  </table></div>
  {par('curve_dopo')}
</section>

<section>
  <h2>Cambi</h2>
  <div class="hero">
    <div><span class="k">Esposizione non-EUR</span><span class="v">{esp_tot/patr*100:.1f}%</span><span class="n">{n(esp_tot)} EUR su {n(patr)}</span></div>
    <div><span class="k">±1% sul franco</span><span class="v">±{n(ESP['CHF']*.01)}</span><span class="n">EUR · {ESP['CHF']/patr*100:.1f}% del patrimonio</span></div>
    <div><span class="k">±1% sul dollaro</span><span class="v">±{n(ESP['USD']*.01)}</span><span class="n">EUR · {ESP['USD']/patr*100:.1f}% del patrimonio</span></div>
  </div>
  <p>Il rischio di cambio pesa <strong>{ESP['CHF']/ESP['USD']:.1f} volte di piu sul franco che sul
  dollaro</strong>. Quasi meta del patrimonio si muove con l'EUR/CHF; il blocco in dollari,
  a {ESP['USD']/patr*100:.1f}%, sposta {n(ESP['USD']*.01)} euro per ogni punto percentuale ed e
  quasi rumore. Si tende a guardare il dollaro perche fa notizia: qui conta il franco.</p>
  <div class="tw"><table>
    <tr><th>Spot</th><th>Oggi</th><th>Settimana prec.</th><th>Var.</th><th>Dal {d0[8:10]}.{d0[5:7]}.{d0[:4]}</th></tr>
    <tr><td class="nm">EUR/CHF</td><td>{SPOT['CHF']:.4f}</td>
        <td>{f"{1/prev['fx_chf']:.4f}" if prev else '—'}</td>
        <td class="{'pos' if prev and oggi_eur['CHF']>prev_eur['CHF'] else 'neg' if prev else ''}">{f"{(oggi_eur['CHF']/prev_eur['CHF']-1)*100:+.2f}%" if prev else '—'}</td>
        <td class="{'pos' if oggi_eur['CHF']>base['CHF'] else 'neg'}">{(oggi_eur['CHF']/base['CHF']-1)*100:+.2f}%</td></tr>
    <tr><td class="nm">EUR/USD</td><td>{SPOT['USD']:.4f}</td>
        <td>{f"{1/prev['fx_usd']:.4f}" if prev else '—'}</td>
        <td class="{'pos' if prev and oggi_eur['USD']>prev_eur['USD'] else 'neg' if prev else ''}">{f"{(oggi_eur['USD']/prev_eur['USD']-1)*100:+.2f}%" if prev else '—'}</td>
        <td class="{'pos' if oggi_eur['USD']>base['USD'] else 'neg'}">{(oggi_eur['USD']/base['USD']-1)*100:+.2f}%</td></tr>
  </table></div>
  <p class="nota-storico">Le variazioni sono lette dal lato del portafoglio: positivo = la
  valuta vale piu euro, quindi il blocco sale. Base {d0[8:10]}.{d0[5:7]}.{d0[:4]}, la stessa
  dell'indice di rendimento.</p>

  <h2 style="margin-top:14px">Il cambio a termine</h2>
  <div class="tw"><table>
    <tr><th>Orizzonte</th><th>EUR/CHF</th><th>Effetto</th><th>EUR/USD</th><th>Effetto</th><th>Sui blocchi</th></tr>
    {''.join(f"""<tr><td class="nm">{lab}</td><td>{fw['CHF']:.4f}</td>
       <td class="{'pos' if ef['CHF']>0 else 'neg'}">{ef['CHF']*100:+.2f}%</td><td>{fw['USD']:.4f}</td>
       <td class="{'pos' if ef['USD']>0 else 'neg'}">{ef['USD']*100:+.2f}%</td>
       <td class="{'pos' if (ESP['CHF']*ef['CHF']+ESP['USD']*ef['USD'])>0 else 'neg'}">{sg(ESP['CHF']*ef['CHF']+ESP['USD']*ef['USD'])}</td></tr>"""
       for lab, fw, ef, _ in FWD)}
  </table></div>
  <p>Calcolato per parita coperta dai tassi a breve — ESTR {RB['EUR']*100:.2f}%,
  SARON {RB['CHF']*100:.2f}%, SOFR {RB['USD']*100:.2f}%, presi come proxy dai tassi di politica
  monetaria. <strong>Non e una previsione</strong>: e il prezzo che rende indifferente comprare
  valuta oggi o alla scadenza. Storicamente il forward e un pessimo predittore dello spot.
  Resta pero un numero e non un'opinione, ed e quello che il mercato ti paga per aspettare.</p>

  <div class="hero">
    <div><span class="k">Book CHF · rinuncia di rendimento</span><span class="v neg">{sg(-costo_chf)}</span><span class="n">EUR/anno contro il book in euro</span></div>
    <div><span class="k">Premio a termine, 12 mesi</span><span class="v pos">{sg(prem_chf)}</span><span class="n">EUR/anno sullo stesso nominale</span></div>
    <div><span class="k">Differenza</span><span class="v">{sg(prem_chf-costo_chf)}</span><span class="n">EUR · lo spread creditizio</span></div>
  </div>
  <p>Sono lo stesso numero visto da due lati. Il franco rende meno <em>perche</em> il forward lo
  prezza in apprezzamento del {(SPOT['CHF']/fwd('CHF',12)-1)*100:.2f}% l'anno, ed e esattamente
  la parita coperta. Quindi la posizione in franchi <strong>non costa nulla in attesa</strong>:
  costa solo se lo spot non segue il forward, cioe se il franco non si rafforza come il
  differenziale implica. E una scommessa diversa da "pago {n(costo_chf)} euro l'anno di
  assicurazione", e va detta bene.</p>
  {par('cambi')}
  {par('chf')}

  <figure>
    <div class="chartwrap">
    <svg viewBox="0 0 {W3} {H3}" role="img" aria-label="Valore in euro di un'unita di franco e di dollaro, base 100 oggi: spot realizzato a sinistra, curva a termine a destra">
    {CH3}
    </svg></div>
    <div class="legend">
      <span><i class="sw" style="background:var(--s2)"></i>CHF</span>
      <span><i class="sw" style="background:var(--s3)"></i>USD</span>
      <span style="color:var(--muted)">pieno = spot realizzato · tratteggio = curva a termine · base 100 oggi</span>
    </div>
    <figcaption>Valore in euro di un'unita di valuta. A sinistra dello zero quello che e
    successo, a destra quello che il differenziale dei tassi prezza. Settimana dopo settimana
    il tratto pieno avanza dentro il territorio che il tratteggio aveva previsto: e li che si
    legge se la parita coperta sta reggendo.</figcaption>
  </figure>
</section>

<section>
  <h2>Storico</h2>
  <div class="tw"><table>
    <tr><th>Venerdi</th><th>BCE</th><th>DE 2a</th><th>DE 10a</th><th>IT 10a</th>
        <th>US 2a</th><th>US 10a</th><th>CH 10a</th><th>Spread</th></tr>
    {righe_st}
  </table></div>
  {CH2}
</section>

<section>
  <h2>Dove sta ogni titolo</h2>
  <figure>
    <div class="chartwrap">
    <svg viewBox="0 0 {W} {H}" role="img" aria-label="Rendimento di carico di ogni obbligazione contro la curva di mercato stimata, per anni a scadenza">
    {CH1}
    </svg></div>
    <div class="legend">
      <span><i class="sw" style="background:var(--s1)"></i>EUR</span>
      <span><i class="sw" style="background:var(--s2)"></i>CHF</span>
      <span><i class="sw" style="background:var(--s3)"></i>USD</span>
      <span style="color:var(--muted)">tratteggio = curva di mercato · punto = rendimento di carico · gambo = distanza</span>
    </div>
    <figcaption>Un punto sotto la sua curva rende meno di quanto oggi paghi il mercato su
    quella scadenza: e li che si nasconde la minusvalenza non contabilizzata.</figcaption>
  </figure>
</section>

<section>
  <h2>Titolo per titolo</h2>
  <div class="tw"><table>
    <tr><th>Titolo</th><th>Val.</th><th>Anni</th><th>YTM carico</th><th>YTM mercato</th><th>Delta EUR</th></tr>
    {righe_b}
  </table></div>
  {par('titoli')}
</section>

<section>
  <h2>Azionario e certificati, in breve</h2>
  {par('azionario')}
</section>

<p class="foot">Rendimenti di carico calcolati sui flussi esatti dal prezzo dirty pagato.
La curva di mercato e ricostruita per interpolazione fra i punti a 2 e 10 anni osservati il
giorno della nota, piu uno spread creditizio indicativo per emittente: sono stime, non prezzi
eseguibili, e vanno lette come ordine di grandezza. Il valore a costo ammortizzato e lo
stesso che espone il report giornaliero.<br><br>
{C.get('fonti','')}</p>
</div>
<div id="tip"></div>
<script>
const tip=document.getElementById('tip');
document.querySelectorAll('.dot[data-n]').forEach(el=>{{
  const show=()=>{{
    const d=el.dataset, s=+d.d;
    tip.innerHTML=`${{d.n}}<br>carico ${{d.yc}}% · mercato ${{d.ym}}%<br>delta ${{s>0?'+':''}}${{s.toLocaleString('de-CH')}} EUR`;
    tip.style.opacity=1;
    const r=el.getBoundingClientRect();
    tip.style.left=Math.min(innerWidth-tip.offsetWidth-12,Math.max(8,r.left+r.width/2-tip.offsetWidth/2))+'px';
    tip.style.top=(r.top-tip.offsetHeight-10)+'px';
  }};
  const hide=()=>tip.style.opacity=0;
  el.addEventListener('mouseenter',show); el.addEventListener('focus',show);
  el.addEventListener('mouseleave',hide); el.addEventListener('blur',hide);
}});
</script>'''

open(P('nota.html'), 'w').write(HTML)
print(f"nota {oggi} · book {tot_amm:,.0f} a costo, {tot_mkt:,.0f} a mercato, "
      f"delta {delta:+,.0f} EUR ({delta/tot_amm*100:+.2f}%)")
print(f"duration mod. {DUR:.2f} · cedola {CED:.2f}% · YTM {YTM*100:.2f}% · "
      f"costo blocco CHF {costo_chf:,.0f} EUR/anno")
print(f"storico: {len(gg)} rilevazion{'e' if len(gg)==1 else 'i'}, "
      f"{'del ' + gg[0] if len(gg)==1 else 'dalla ' + gg[0] + ' alla ' + gg[-1]}")
mancanti = [k for k in ('standfirst','regime','titoli','azionario','fonti') if not C.get(k)]
if mancanti: print('!! commento.json incompleto, mancano:', ', '.join(mancanti))
