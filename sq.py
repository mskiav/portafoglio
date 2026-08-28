#!/usr/bin/env python3
"""Report portafoglio Swissquote — tutto in un file.
   Uso:  python3 sq.py [AAAA-MM-GG] [ora]
   Generato da build.py: non modificare qui, modificare i sorgenti."""
import json, os, sys, subprocess
from datetime import date, datetime, timedelta

# ═════════════════════ MOTORE ═════════════════════
"""Motore di calcolo del report portafoglio — struttura a blocchi valuta."""
import json, os
from datetime import date, timedelta

BASE = os.path.dirname(os.path.abspath(__file__))
P = lambda f: os.path.join(BASE, f)

def load(f, d=None):
    try: return json.load(open(P(f), encoding='utf-8'))
    except Exception: return d

# ───────────────────────── accrual ─────────────────────────
def _cedole(sett, mat, ced, f):
    if f == 0: return [(mat, 100.0)]
    d, ds = mat, []
    while d > sett:
        ds.append(d)
        y, m = d.year, d.month - 12 // f
        while m <= 0: m += 12; y -= 1
        d = date(y, m, d.day)
    ds.sort()
    return [(x, ced / f + (100.0 if x == mat else 0.0)) for x in ds]

def ytm(dirty, sett, mat, ced, f):
    fl = _cedole(sett, mat, ced, f); lo, hi = -0.9, 2.0
    for _ in range(160):
        mid = (lo + hi) / 2
        v = sum(cf / (1 + mid) ** ((d - sett).days / 365) for d, cf in fl) - dirty
        lo, hi = (mid, hi) if v > 0 else (lo, mid)
    return (lo + hi) / 2

def val_accrual(pagato, nom, y, oggi, mat, ced, f):
    """Costo ammortizzato: valore attuale dei flussi ANCORA DA INCASSARE, scontati
    al rendimento di carico. Equivale a pagato*(1+y)^t solo se non ci sono cedole
    intermedie: capitalizzare e basta conterebbe due volte le cedole gia staccate."""
    fl = _cedole(oggi, mat, ced, f)
    v = sum(cf / (1 + y) ** ((d - oggi).days / 365) for d, cf in fl if d > oggi)
    return v * nom / 100

def rateo(sett, mat, ced, f, nom):
    if f == 0: return 0.0
    d = mat
    while d > sett:
        y, m = d.year, d.month - 12 // f
        while m <= 0: m += 12; y -= 1
        d = date(y, m, d.day)
    return nom * ced / 100 * (sett - d).days / 365

# ───────────────────────── costruzione ─────────────────────────
def build(pos, prezzi, oggi, costo_eur=None):
    FX   = prezzi.get('_fx', {})           # 1 unita valuta = X EUR
    FXP  = prezzi.get('_fx_prev', FX)
    fx   = lambda c, f=None: 1.0 if c == 'EUR' else (f or FX).get(c, 0)

    B = {v: {'cat': {}, 'titoli': [], 'val': 0.0, 'prev': 0.0, 'costo': 0.0,
             'costo_cash': 0.0, 'val_cash': 0.0} for v in pos['blocchi']}

    def add(v, cat, nome, val, prev, costo, extra=None):
        b = B[v]
        c = b['cat'].setdefault(cat, {'val': 0.0, 'prev': 0.0, 'costo': 0.0, 'it': []})
        c['val'] += val; c['prev'] += prev; c['costo'] += costo
        c['incassato'] = c.get('incassato', 0) + ((extra or {}).get('incassato') or 0)
        r = {'nome': nome, 'val': val, 'prev': prev, 'costo': costo,
             'd': (val / prev - 1) if prev else 0.0}
        if extra: r.update(extra)
        # le cedole staccate escono dal valore del titolo ma restano in cassa:
        # senza rimetterle nel conto, un titolo che ha pagato risulta in perdita
        r['inizio'] = ((val + r.get('incassato', 0)) / costo - 1) if costo else 0.0
        c['it'].append(r)
        b['val'] += val; b['prev'] += prev; b['costo'] += costo
        return r

    # bond ad accrual
    ytm_w = {v: 0.0 for v in pos['blocchi']}
    for b in pos['bond']:
        mat = date.fromisoformat(b['mat']); v = b['val']
        sv = sp = sc = 0.0; sw = 0.0; nom = 0; inc_b = 0.0
        for t in b['tranche']:
            d = date.fromisoformat(t['d']); pag = t['pagato']; nom += t['nom']
            # cedole staccate DOPO l'acquisto di questa tranche: sono uscite dal
            # valore del titolo ma sono entrate in cassa, quindi restano tue
            for dc, cf in _cedole(d, mat, b['cedola'], b['freq']):
                if d < dc <= oggi: inc_b += (cf - (100.0 if dc == mat else 0.0)) * t['nom'] / 100
            y = ytm(pag / t['nom'] * 100, d, mat, b['cedola'], b['freq'])
            val = val_accrual(pag, t['nom'], y, max(oggi, d), mat, b['cedola'], b['freq'])
            ieri = max(oggi - timedelta(days=1), d)
            prv = val_accrual(pag, t['nom'], y, ieri, mat, b['cedola'], b['freq'])
            sv += val; sp += prv; sc += pag; sw += y * val
        add(v, 'Obbligazionario', b['nome'], sv, sp, sc,
            {'ytm': sw / sv if sv else 0, 'nom': nom, 'mat': b['mat'],
             'incassato': inc_b,
             'rateo': rateo(oggi, mat, b['cedola'], b['freq'], nom)})
        ytm_w[v] += sw

    # deposito
    dp = pos.get('deposito')
    if dp:
        gg = (oggi - date.fromisoformat(dp['da'])).days
        val = dp['importo'] * (1 + dp['tasso'] * gg / 365)
        prv = dp['importo'] * (1 + dp['tasso'] * max(gg - 1, 0) / 365)
        add(dp['val'], 'Obbligazionario', dp['nome'], val, prv, dp['importo'],
            {'ytm': dp['tasso'], 'scad': dp['a']})
        ytm_w[dp['val']] += dp['tasso'] * val

    # titoli
    for t in pos['titoli']:
        p = prezzi.get(t['tk'])
        if not p or p.get('px') is None:
            add(t['val'], t['cat'], t['tk'], 0, 0, t['costo'], {'manca': True}); continue
        px, prev = p['px'], p.get('prev', p['px'])
        add(t['val'], t['cat'], t['tk'], px * t['q'], prev * t['q'], t['costo'], {'px': px})

    # invest easy
    ie = pos.get('invest_easy'); pie = prezzi.get('AMBTSQ', {})
    if ie and pie.get('px'):
        nav, nprev = pie['px'], pie.get('prev', pie['px'])
        add(ie['val'], ie['cat'], ie['nome'],
            ie['quote'] * nav + ie['cash'], ie['quote'] * nprev + ie['cash'], ie['costo'],
            {'nav': nav})

    # certificati
    for c in pos['certificati']:
        p = prezzi.get(c['tk'])
        if not p or p.get('px') is None:
            add(c['val'], 'Certificati', c['nome'], 0, 0, c['costo'], {'manca': True}); continue
        px, prev = p['px'], p.get('prev', p['px'])
        sub = prezzi.get(c['sottostante'], {})
        scad = date.fromisoformat(c['scad']); gg = max((scad - oggi).days, 0)
        pc = px * 100                      # prezzo in percentuale del nominale
        # cedole effettivamente ancora da incassare, dalle date del termsheet
        ced_u = c['cedola'] / c.get('freq_ced', 1)          # cedola per periodo, 30/360
        fut = [d for d in c.get('cedole', []) if date.fromisoformat(d) > oggi]
        inc = ced_u * len(fut) if fut else c['cedola'] * gg / 365
        base = c['costo'] / c['q'] * 100                     # prezzo di carico, commissioni incluse
        # rimborso a 100 piu le cedole incassate nel periodo, contro il prezzo di carico
        res = lambda n, gio: ((100 + ced_u * n) / base) ** (365 / gio) - 1 if gio > 0 else 0
        ytm_c = res(len(fut), gg)
        # rendimento alla prima data di richiamo ancora aperta
        ytc = None; d_call = None
        for k in c.get('call', []):
            dr = date.fromisoformat(k['rimborso'])
            if dr > oggi:
                n = len([d for d in c.get('cedole', []) if oggi < date.fromisoformat(d) <= dr])
                ytc = res(n, (dr - oggi).days); d_call = k['rimborso']; break
        d_acq = date.fromisoformat(c['da']) if c.get('da') else None
        inc_c = sum(ced_u * c['q'] / 100 for x in c.get('cedole', [])
                    if d_acq and d_acq < date.fromisoformat(x) <= oggi)
        s_px = sub.get('px')
        add(c['val'], 'Certificati', c['nome'], px * c['q'], prev * c['q'], c['costo'],
            {'px': px, 'px_pct': pc, 'cedola': c['cedola'], 'barriera': c['barriera'],
             'strike': c.get('cap'), 'sotto': s_px, 'tk': c['tk'],
             'sottostante': c['sottostante'],
             'cuscino': (1 - c['barriera'] / s_px) if s_px else None,
             'margine_strike': (s_px / c['cap'] - 1) if (s_px and c.get('cap')) else None,
             'barriera_pct_strike': (c['barriera'] / c['cap']) if c.get('cap') else None,
             'gg': gg, 'ytm_cert': ytm_c, 'scad': c['scad'],
             'ytc': ytc, 'd_call': d_call, 'callable': c.get('callable'),
             'barriera_tipo': c.get('barriera_tipo'), 'freq_ced': c.get('freq_ced'),
             'azioni': c.get('azioni'), 'cedole_res': len(fut), 'cedola_unit': ced_u,
             'nominale': c['q'], 'incassato': inc_c,
             'break_even': (c['cap'] * (base - inc) / 100) if c.get('cap') else None})

    # liquidita
    for v, L in pos['liquidita'].items():
        b = B[v]
        b['val_cash'] = L['saldo']; b['costo_cash'] = L['costo_eur']
        c = b['cat'].setdefault('Cash', {'val': 0.0, 'prev': 0.0, 'costo': 0.0, 'it': []})
        c['val'] = L['saldo']; c['prev'] = L['saldo']; c['costo'] = L['saldo']
        c['carico'] = L['carico']
        c['pl_eur'] = L['saldo'] * fx(v) - L['costo_eur']
        c['inizio_eur'] = (L['saldo'] * fx(v) / L['costo_eur'] - 1) if L['costo_eur'] else 0
        b['val'] += L['saldo']; b['prev'] += L['saldo']

    # totali blocco
    for v, b in B.items():
        for cat, c in b['cat'].items():
            c['d'] = (c['val'] / c['prev'] - 1) if c['prev'] else 0.0
            c['inizio'] = ((c['val'] + c.get('incassato', 0)) / c['costo'] - 1) if c['costo'] else 0.0
            c['peso'] = c['val'] / b['val'] if b['val'] else 0
        tit = b['val'] - b['val_cash']
        cos = b['costo']
        real = pos['realizzi'].get(v, 0)
        b['tit_val'] = tit; b['tit_costo'] = cos
        inc_tot = sum(x.get('incassato', 0) for x in b['cat'].values())
        b['incassato'] = inc_tot
        b['inizio_tit'] = ((tit + real + inc_tot) / cos - 1) if cos else 0
        cash_cost_loc = b['costo_cash'] / fx(v) if fx(v) else 0
        b['inizio_tot'] = ((b['val'] + real + inc_tot) / (cos + cash_cost_loc) - 1) if (cos + cash_cost_loc) else 0
        b['cash_cost_loc'] = cash_cost_loc
        b['val_eur'] = b['val'] * fx(v)
        b['d_loc'] = (b['val'] / b['prev'] - 1) if b['prev'] else 0
        b['prev_eur'] = b['prev'] * fx(v, FXP)
        b['d_eur'] = (b['val_eur'] / b['prev_eur'] - 1) if b['prev_eur'] else 0
        b['fx_eff'] = b['d_eur'] - b['d_loc']
        ce = (costo_eur or {}).get(v)
        b['costo_eur_storico'] = ce
        b['inizio_eur'] = ((b['val_eur'] + real * fx(v)) / ce - 1) if ce else None
        b['ytm'] = ytm_w[v] / b['cat'].get('Obbligazionario', {}).get('val', 1) \
                   if b['cat'].get('Obbligazionario', {}).get('val') else 0
        # cassa attesa 12m: bond + cedole certificati
        cassa = ytm_w[v]
        for c in pos['certificati']:
            if c['val'] == v: cassa += c['q'] * c['cedola'] / 100
        b['cassa12'] = cassa
    return B, FX, FXP


# ───────────────────── rendimenti di periodo ─────────────────────
def periodi(B, tot_eur, rif):
    """MTD/QTD/YTD da indice TWR base 100. Le celle di categoria restano vuote:
    dentro un blocco le categorie si scambiano capitale di continuo e un rapporto
    di valori non sarebbe un rendimento."""
    S = rif['serie']
    def calc(key, val_oggi):
        s = S.get(key)
        if not s: return {}
        dd = sorted(s)
        ult = dd[-1]
        idx = s[ult]['idx'] * (val_oggi / s[ult]['val'])
        def da(d):
            return (idx / s[d]['idx'] - 1) if d in s else None
        # ultimo fine mese, ultimo fine trimestre, fine anno precedente
        return {'MTD': da(dd[-1]), 'QTD': da(dd[-2]) if len(dd) > 2 else da(dd[0]),
                'YTD': da(dd[0]), 'idx': idx}
    out = {'TOT': calc('TOT', tot_eur)}
    for v, b in B.items():
        out[v] = {'loc': calc(f'{v}_loc', b['val']), 'eur': calc(f'{v}_eur', b['val_eur'])}
        b['per_loc'] = out[v]['loc']; b['per_eur'] = out[v]['eur']
    return out


# ───────────────────── memoria giornaliera ─────────────────────
def serie(B, oggi, FX=None, S=None, tot=None):
    """Registra prezzi e cuscini di ogni sera. Serve al delta del cuscino e,
    dopo qualche settimana, alla volatilita dei sottostanti calcolata sui
    propri dati invece che comprata fuori."""
    S = dict(S or {})
    g = oggi.isoformat()
    riga = {}
    for v, b in B.items():
        for cat, c in b['cat'].items():
            if cat == 'Cash':
                riga['CASH_' + v] = {'px': round(c['val'], 2)}; continue
            for it in c['it']:
                k = it.get('tk') or it['nome']
                r = {'px': round(it.get('px') or it.get('nav') or it.get('px_pct') or it['val'], 4)}
                if it.get('cuscino') is not None:
                    r.update({'sotto': it['sotto'], 'cuscino': round(it['cuscino'], 5)})
                riga[k] = r
    riga['_fx'] = {k: round(x, 6) for k, x in (FX or {}).items()}
    if tot is not None: riga['_tot'] = round(tot, 2)
    S[g] = riga
    # variazione del cuscino rispetto all'ultima sera registrata
    prec = [d for d in sorted(S) if d < g]
    if not prec: return S, {}
    ieri = S[prec[-1]]
    return S, {k: (riga[k]['cuscino'] - ieri[k]['cuscino'])
               for k in riga
               if not k.startswith('_') and k in ieri
               and isinstance(riga[k], dict) and isinstance(ieri[k], dict)
               and 'cuscino' in riga[k] and 'cuscino' in ieri[k]}


# ═════════════════════ RENDER ═════════════════════
"""Rendering report a blocchi valuta — mobile first, tema scuro."""
CSS = """
:root{--plane:#0d0d0d;--surface:#1a1a19;--surface-2:#212120;--ink:#fff;--ink-2:#c3c2b7;
--muted:#898781;--grid:#2c2c2a;--rule:#383835;--s1:#3987e5;--s2:#d95926;--s3:#199e70;
--s4:#c98500;--s5:#d55181;--s6:#008300;--s7:#9085e9;--good:#0ca30c;--crit:#d03b3b;--warn:#fab219}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--plane);color:var(--ink);font:400 15px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;-webkit-font-smoothing:antialiased;padding:12px 12px 32px}
.wrap{max-width:440px;margin:0 auto;display:flex;flex-direction:column;gap:10px}
.card{background:var(--surface);border-radius:14px;padding:14px}
.num{font-variant-numeric:tabular-nums;font-feature-settings:"tnum"}
.up{color:var(--good)}.dn{color:var(--crit)}.ze{color:var(--muted)}
.nd{color:var(--muted);font-size:10.5px}
.lbl{font-size:10.5px;letter-spacing:.07em;text-transform:uppercase;color:var(--muted);font-weight:600}
.top{display:flex;justify-content:space-between;align-items:baseline;margin-bottom:2px}
.stamp{font-size:11px;color:var(--muted)}
.hero{font-size:36px;font-weight:650;letter-spacing:-1.1px;line-height:1.1;margin:6px 0 3px}
.hero small{font-size:16px;font-weight:500;color:var(--ink-2);letter-spacing:0}
.delta{display:flex;align-items:center;gap:7px;font-size:14.5px;font-weight:600;flex-wrap:wrap}
.chip{font-size:11px;font-weight:600;padding:2.5px 7px;border-radius:6px;background:var(--surface-2);color:var(--ink-2)}
.accr{display:flex;justify-content:space-between;margin-top:9px;padding-top:9px;border-top:1px solid var(--grid);font-size:11.5px;color:var(--muted)}
.accr b{color:var(--ink-2);font-weight:650}
.vhead{display:flex;justify-content:space-between;align-items:baseline;margin-bottom:3px}
.vcur{font-size:18px;font-weight:700;letter-spacing:.5px}
.vtot{font-size:18px;font-weight:650}
.vsub{display:flex;justify-content:space-between;font-size:11px;color:var(--muted);
      padding-bottom:9px;margin-bottom:8px;border-bottom:1px solid var(--grid)}
table{width:100%;border-collapse:collapse}
th,td{text-align:right;padding:4.5px 0 4.5px 5px;font-size:11.5px;font-variant-numeric:tabular-nums;white-space:nowrap}
th{font-size:9px;letter-spacing:.04em;text-transform:uppercase;color:var(--muted);font-weight:600;padding-bottom:6px}
td:first-child,th:first-child{text-align:left;padding-left:0;white-space:normal;width:34%}
.cat td{font-weight:600;font-size:12px}
.cat td:first-child{line-height:1.25}
.cat .dot{display:inline-block;margin-right:5px;vertical-align:middle}
.dot{width:8px;height:8px;border-radius:2px}
.it td{color:var(--ink-2);font-size:10.5px;padding-top:2px;padding-bottom:2px}
.it td:first-child{padding-left:13px;position:relative}
.it td:first-child::before{content:"";position:absolute;left:2px;top:50%;width:4px;height:1px;background:var(--rule)}
.sep td{border-top:1px solid var(--grid);padding-top:7px}
.tot td{border-top:1px solid var(--rule);padding-top:8px;font-weight:650;font-size:12px}
.eur td{font-size:11px;color:var(--ink-2);padding-top:2px}
.eur td:first-child{padding-left:13px;position:relative}
.eur td:first-child::before{content:"";position:absolute;left:2px;top:50%;width:4px;height:1px;background:var(--rule)}
.w{font-size:9px;color:var(--muted)}
.foot{display:flex;justify-content:space-between;font-size:10px;color:var(--muted);
      margin-top:9px;padding-top:8px;border-top:1px solid var(--grid)}
.bar{display:flex;height:24px;border-radius:6px;overflow:hidden;gap:2px;margin:9px 0 8px}
.seg{display:flex;align-items:center;justify-content:center;font-size:10.5px;font-weight:700;color:#fff;text-shadow:0 1px 2px rgba(0,0,0,.45)}
.leg{display:flex;flex-wrap:wrap;gap:10px}
.leg span{display:flex;align-items:center;gap:5px;font-size:11px;color:var(--ink-2)}
h2{font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:var(--muted);font-weight:650;margin-bottom:9px}
.note{font-size:10.5px;color:var(--muted);line-height:1.5;padding:0 3px}
.note b{color:var(--ink-2)}
.cmt{font-size:11px;color:var(--ink-2);line-height:1.45;margin:8px 0 2px;padding-top:8px;border-top:1px solid var(--grid)}
.cmt0{margin-top:10px;padding-top:10px}
.crt{padding:10px 0;border-top:1px solid var(--grid)}
.crt:first-of-type{padding-top:0;border-top:0}
.crthead{display:flex;justify-content:space-between;align-items:flex-start;gap:8px;margin-bottom:7px}
.crthead>span:first-child{flex:1;min-width:0}
.crthead>span:last-child{white-space:nowrap;flex:none}
.crtn{font-size:13px;font-weight:650}
.scale{position:relative;height:7px;border-radius:4px;background:var(--surface-2);overflow:visible;margin-bottom:8px}
.sfill{position:absolute;left:0;top:0;height:7px;border-radius:4px;opacity:.85}
.smark{position:absolute;top:-3px;width:2px;height:13px;background:var(--ink-2);border-radius:1px}
.crtg{display:grid;grid-template-columns:1fr 1fr;gap:3px 10px;font-size:10.5px;color:var(--muted)}
.crtg b{color:var(--ink);font-weight:650}
.crtnote{font-size:9.5px;color:var(--muted);margin-top:7px;padding-top:6px;border-top:1px solid var(--grid)}
.tagc{font-size:8.5px;font-weight:700;letter-spacing:.04em;padding:1.5px 5px;border-radius:4px;
      background:var(--s4);color:#1a1a19;margin-left:5px;vertical-align:1px}
.tw{overflow-x:auto;margin:0 -14px;padding:0 14px;-webkit-overflow-scrolling:touch}
.tw table{min-width:430px}
.alr{background:var(--surface-2)}
.al{display:flex;align-items:flex-start;gap:7px;font-size:11.5px;color:var(--ink-2);padding:3px 0}
.adot{width:7px;height:7px;border-radius:50%;flex:none;margin-top:5px}
"""
COL = {'Obbligazionario':'--s1','Azionario':'--s5','Certificati':'--s4',
       'Cripto':'--s7','Materie prime':'--s2','Cash':'--s3'}
ORD = ['Obbligazionario','Azionario','Certificati','Cripto','Materie prime','Cash']
SIM = {'CHF':"CHF",'EUR':"EUR",'USD':"USD"}

def breve(n):
    import re
    return re.sub(r'(\d{2})\.(\d{2})\.(\d{2})(\d{2})', r'\2/\4', n)

def f(v, s=''):
    if v is None: return 'n.d.'
    t = f"{abs(v):,.0f}".replace(',', "'") if abs(v) >= 1000 else f"{abs(v):,.2f}"
    return ('−' if v < 0 else ('+' if s == '+' else '')) + t

def p(v):
    return 'n.d.' if v is None else ('−' if v < 0 else '+') + f"{abs(v)*100:.2f}"

def k(v):
    return 'nd' if v is None else ('dn' if v < 0 else ('up' if v > 0 else 'ze'))

def scheda(v, b, tot_eur, per, cmt=''):
    h = [f'''<div class="card"><div class="vhead"><span class="vcur">{SIM[v]}</span>
<span class="vtot num">{f(b['val_eur'])} <span style="font-size:11px;color:var(--ink-2);font-weight:500">EUR</span></span></div>
<div class="vsub"><span class="num">{f(b['val'])} {v} · {b['val_eur']/tot_eur*100:.1f}% del patrimonio</span>
<span class="num {k(b['d_loc'])}">{'▲' if b['d_loc']>=0 else '▼'} {p(b['d_loc'])}%</span></div>
<div class="tw"><table class="num"><tr><th></th><th>Valore {v}</th><th>Inizio</th><th>1G</th><th>MTD</th><th>QTD</th><th>YTD</th></tr>''']
    first = True
    for cat in ORD:
        c = b['cat'].get(cat)
        if not c or not c['val']: continue
        sub = f"{c['peso']*100:.1f}%"
        if cat == 'Obbligazionario' and b.get('ytm'): sub += f"&nbsp;·&nbsp;YTM&nbsp;{b['ytm']*100:.2f}%"
        if cat == 'Cash' and c.get('carico'): sub += f"&nbsp;·&nbsp;carico&nbsp;{c['carico']:.4f}"
        ini = c.get('inizio_eur') if cat == 'Cash' else c['inizio']
        sep = '' if first else ' sep'; first = False
        h.append(f'''<tr class="cat{sep}"><td><span class="dot" style="background:var({COL[cat]})"></span>{cat}<br><span class="w">{sub}</span></td>
<td>{f(c['val'])}</td><td class="{k(ini)}">{p(ini)}</td><td class="{k(c['d'])}">{p(c['d'])}</td>
<td colspan="3"></td></tr>''')
        for it in sorted(c['it'], key=lambda x: -x['val'])[:6]:
            if it.get('manca'):
                h.append(f'''<tr class="it"><td>{it['nome']}</td><td colspan="6" style="text-align:right;color:var(--warn)">prezzo non recuperato</td></tr>'''); continue
            extra = ''
            if it.get('ytm') is not None and cat == 'Obbligazionario':
                extra = f'<span class="w"> {it["ytm"]*100:.2f}%</span>'
                if it.get('incassato'): extra += f'<span class="w"> · incassate {f(it["incassato"])}</span>'
            if it.get('nav'): extra = f'<span class="w"> NAV {it["nav"]:.2f}</span>'
            if it.get('cuscino') is not None: extra = f'<span class="w"> barriera −{it["cuscino"]*100:.0f}%</span>'
            h.append(f'''<tr class="it"><td>{breve(it['nome'])[:24]}{extra}</td><td>{f(it['val'])}</td>
<td class="{k(it['inizio'])}">{p(it['inizio'])}</td><td class="{k(it['d'])}">{p(it['d'])}</td><td colspan="3"></td></tr>''')
    pl, pe = b.get('per_loc') or {}, b.get('per_eur') or {}
    cel = lambda d: ''.join(f'<td class="{k(d.get(x))}">{p(d.get(x))}</td>' for x in ('MTD','QTD','YTD'))
    h.append(f'''<tr class="tot"><td>Totale in {v}</td><td>{f(b['val'])}</td>
<td class="{k(b['inizio_tot'])}">{p(b['inizio_tot'])}</td><td class="{k(b['d_loc'])}">{p(b['d_loc'])}</td>
{cel(pl)}</tr>''')
    if v != 'EUR':
        fxp = {x: (pe.get(x) - pl.get(x)) if (pe.get(x) is not None and pl.get(x) is not None) else None
               for x in ('MTD','QTD','YTD')}
        h.append(f'''<tr class="eur"><td>letto in EUR</td><td>{f(b['val_eur'])}</td>
<td class="{k(b.get('inizio_eur'))}">{p(b.get('inizio_eur'))}</td><td class="{k(b['d_eur'])}">{p(b['d_eur'])}</td>
{cel(pe)}</tr>
<tr class="eur"><td>di cui effetto cambio</td><td></td>
<td class="{k((b.get('inizio_eur') or 0)-b['inizio_tot']) if b.get('inizio_eur') is not None else 'nd'}">{p((b['inizio_eur']-b['inizio_tot']) if b.get('inizio_eur') is not None else None)}</td>
<td class="{k(b['fx_eff'])}">{p(b['fx_eff'])}</td>{cel(fxp)}</tr>''')
    h.append('</table></div>')
    if cmt: h.append(f'<div class="cmt">{cmt}</div>')
    if b.get('cassa12'):
        h.append(f'''<div class="foot"><span>Cassa attesa 12 mesi</span>
<span class="num">{f(b['cassa12'])} {v} · {b['cassa12']/b['val']*100:.2f}% del blocco</span></div>''')
    h.append('</div>')
    return ''.join(h)

def render(B, FX, FXP, oggi, ora, pos, per, storico, fs=None, dcus=None):
    dcus = dcus or {}
    tot_eur  = sum(b['val_eur'] for b in B.values())
    prev_eur = sum(b['prev_eur'] for b in B.values())
    d_eur    = tot_eur - prev_eur
    costo    = sum(b['costo'] + b.get('cash_cost_loc', 0) for b in B.values())
    real     = sum(pos['realizzi'].get(v, 0) for v in B)
    h = [f'''<!DOCTYPE html><html lang="it"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>Portafoglio — {oggi.strftime("%d.%m.%Y")}</title><style>{CSS}</style></head><body><div class="wrap">''']
    # rendimento complessivo in EUR
    costo_eur_tot = sum(b.get('costo_eur_storico') or 0 for b in B.values())
    real_eur = sum(pos['realizzi'].get(v, 0) * (b['val_eur'] / b['val'] if b['val'] else 1)
                   for v, b in B.items())
    if fs:
        netto = fs['netto']; ini_tot = tot_eur / netto - 1; pl_eur = tot_eur - netto
    else:
        ini_tot = ((tot_eur + real_eur) / costo_eur_tot - 1) if costo_eur_tot else None
        netto = costo_eur_tot; pl_eur = tot_eur + real_eur - costo_eur_tot
    d_pct = d_eur / prev_eur if prev_eur else 0
    h.append(f'''<div class="card"><div class="top"><span class="lbl">Patrimonio</span>
<span class="stamp num">{oggi.strftime("%d.%m")} · {ora}</span></div>
<div class="hero num">{f(tot_eur)} <small>EUR</small></div>
<div class="delta"><span class="{k(d_eur)} num">{'▲' if d_eur>=0 else '▼'} {f(abs(d_eur))} EUR</span>
<span class="{k(d_eur)} num">{p(d_pct)}%</span>
<span class="chip num">da inizio {f(pl_eur, '+')} EUR</span>{f'<span class="chip num">IRR {p(fs["irr"])}% ann.</span>' if fs else ''}</div>
<table class="num" style="margin-top:11px">
<tr><th>Rendimento EUR</th><th>Inizio</th><th>1G</th><th>MTD</th><th>QTD</th><th>YTD</th></tr>
<tr class="tot" style="border-top:1px solid var(--grid)"><td>Totale portafoglio</td>
<td class="{k(ini_tot)}">{p(ini_tot)}</td><td class="{k(d_pct)}">{p(d_pct)}</td>
<td class="{k(per.get('MTD'))}">{p(per.get('MTD'))}</td>
<td class="{k(per.get('QTD'))}">{p(per.get('QTD'))}</td>
<td class="{k(per.get('YTD'))}">{p(per.get('YTD'))}</td></tr></table>
<div class="cmt cmt0">{attribuzione(B, FX)}</div>
<div class="accr"><span>CHF/EUR <b class="num">{FX['CHF']:.4f}</b> <span class="w">EUR/CHF {1/FX['CHF']:.4f}</span> · USD/EUR <b class="num">{FX['USD']:.4f}</b></span>
<span class="num">su {f(netto)} conferiti{f" · {fs['giorni']} giorni" if fs else ""}</span></div></div>''')
    al = allerta(B, FX, FXP, dcus, pos)
    if al: h.append(al)
    for v in ['CHF', 'EUR', 'USD']:
        if v in B and B[v]['val']:
            h.append(scheda(v, B[v], tot_eur, per, attrib_blocco(B[v], v)))
            if v == 'CHF': h.append(certificati(B, dcus))
    # sintesi categorie
    from collections import defaultdict
    S = defaultdict(float)
    for v, b in B.items():
        fxv = b['val_eur'] / b['val'] if b['val'] else 1
        for cat, c in b['cat'].items(): S[cat] += c['val'] * fxv
    h.append('<div class="card"><h2>Categorie · totale portafoglio</h2><div class="bar">')
    for cat in ORD:
        if not S.get(cat): continue
        pc = S[cat] / tot_eur * 100
        h.append(f'<div class="seg" style="background:var({COL[cat]});flex:{pc:.2f}">{pc:.0f}%</div>')
    h.append('</div><div class="leg">')
    for cat in ORD:
        if not S.get(cat): continue
        h.append(f'<span><span class="dot" style="background:var({COL[cat]})"></span>{cat} {f(S[cat])}</span>')
    h.append('</div></div>')
    h.append(f'''<p class="note">
<b>Inizio</b>: P&amp;L sul capitale investito, cassa inclusa e realizzi compresi ({f(real,'+')} EUR dalle chiusure del 21.08).<br>
<b>Obbligazionario</b> a costo ammortizzato sul rendimento di carico, mai a mercato.<br>
<b>Cash</b>: il rendimento è solo effetto cambio dal carico medio; in valuta locale è zero per costruzione.<br>
<b>Cassa attesa</b>: cedole dei bond al loro YTM più cedole dei certificati, sui prossimi 12 mesi.<br>
<b>MTD, QTD e YTD</b> sono TWR sulla composizione storica del portafoglio, ricostruita dagli estratti conto:
partono dal 31.12.2025 e includono gli strumenti nel frattempo usciti. Non compaiono sulle categorie perché
dentro un blocco si scambiano capitale di continuo e un rapporto di valori non sarebbe un rendimento.
</p></div></body></html>''')
    return ''.join(h)


# ───────────────────── commenti generati dai numeri ─────────────────────
def attribuzione(B, FX, n=3):
    """Chi ha mosso il portafoglio oggi. Due grandezze diverse, quindi due unita
    esplicite: l'euro dice quanto ha spostato il patrimonio, la percentuale
    quanto si e mosso il titolo. Senza unita i due numeri si confondono."""
    r = []
    for v, b in B.items():
        fx = FX.get(v, 1.0) if v != 'EUR' else 1.0
        for cat, c in b['cat'].items():
            for it in c['it']:
                d = (it['val'] - it['prev']) * fx
                if abs(d) >= 30: r.append((d, it['nome'], it.get('d', 0)))
    su = sorted([x for x in r if x[0] > 0], key=lambda x: -x[0])[:n]
    gi = sorted([x for x in r if x[0] < 0], key=lambda x:  x[0])[:n]
    def fr(L):
        out = []
        for i, (d, nm, pc) in enumerate(L):
            eur = f"{abs(d):,.0f}".replace(',', "'")
            out.append(f"{breve(nm)[:18]} {'+' if d >= 0 else '−'}{eur}"
                       f"{' EUR' if i == 0 else ''} ({p(pc)}%)")
        return ', '.join(out)
    p1 = fr(su) if su else ''
    p2 = fr(gi) if gi else ''
    if p1 and p2: return f"Spingono {p1}. Frenano {p2}."
    return f"Spingono {p1}." if p1 else (f"Frenano {p2}." if p2 else "Nessun movimento di rilievo.")

def attrib_blocco(b, v, n=2):
    r = [((it['val'] - it['prev']), it['nome'], it.get('d', 0))
         for cat, c in b['cat'].items() for it in c['it']
         if abs(it['val'] - it['prev']) >= 20]
    if not r: return ''
    r.sort(key=lambda x: -abs(x[0]))
    imp = lambda x: ('+' if x >= 0 else '−') + f"{abs(x):,.0f}".replace(',', "'")
    return ' · '.join(f"{breve(nm)[:16]} {p(dd)}% ({imp(d)} {v})" for d, nm, dd in r[:n])

FASCIA = [(.35, 'good', '●'), (.20, 'warn', '●'), (.10, 's2', '●'), (0, 'crit', '●')]
def fascia(c):
    for soglia, cls, dot in FASCIA:
        if c >= soglia: return cls, dot
    return 'crit', '●'

SCALA = 0.60          # fondo scala della barra: un cuscino del 60% la riempie

def certificati(B, dcus):
    """Blocco di monitoraggio: barriera, strike, cuscino e rendimento se tiene."""
    righe = []
    for v, b in B.items():
        for it in b['cat'].get('Certificati', {}).get('it', []):
            if it.get('cuscino') is None: continue
            righe.append((v, it))
    if not righe: return ''
    h = ['<div class="card"><h2>Certificati · monitoraggio barriere</h2>']
    for v, it in righe:
        cls, dot = fascia(it['cuscino'])
        dc = dcus.get(it.get('tk'))
        var = f'<span class="w"> {p(dc)} pt</span>' if dc is not None else ''
        nome = it['nome'].replace('BRC ', '')
        h.append(f'''<div class="crt">
<div class="crthead"><span class="crtn">{nome} <span class="w">ced. {it['cedola']}% · {it['sottostante']}</span>{'<span class="tagc">CALLABLE</span>' if it.get('callable') else ''}</span>
<span class="num" style="color:var(--{cls});font-weight:700">{dot} {it['cuscino']*100:.1f}%{var}</span></div>
<div class="scale"><div class="sfill" style="width:{min(it['cuscino']/SCALA,1)*100:.1f}%;background:var(--{cls})"></div>
<div class="smark" style="left:{min(max(1-it['strike']/it['sotto'],0)/SCALA,1)*100:.1f}%" title="strike"></div></div>
<div class="crtg">
<span>sottostante <b class="num">{it['sotto']:.2f}</b></span>
<span>strike <b class="num">{it['strike']:.2f}</b> <span class="w">{p(it['margine_strike'])}%</span></span>
<span>barriera <b class="num">{it['barriera']:.2f}</b> <span class="w">{it['barriera_pct_strike']*100:.0f}% strike</span></span>
<span>break-even <b class="num">{it['break_even']:.2f}</b> <span class="w">{p(it['break_even']/it['sotto']-1)}%</span></span>
<span>prezzo <b class="num">{it['px_pct']:.2f}</b> <span class="w">carico {it['costo']/it['nominale']*100:.2f}</span></span>
<span>{it['cedole_res']} cedole <span class="w">da {it['cedola_unit']:.3f}% trim.</span></span>
<span>a scadenza <b class="num up">{p(it['ytm_cert'])}%</b> <span class="w">{it['gg']} gg, al {it['scad'][8:10]}.{it['scad'][5:7]}.{it['scad'][2:4]}</span></span>
<span>{'al 1° call <b class="num up">' + p(it['ytc']) + '%</b> <span class="w">' + it['d_call'][8:10]+'.'+it['d_call'][5:7]+'.'+it['d_call'][2:4] + '</span>' if it.get('ytc') is not None else 'non richiamabile'}</span>
</div>
<div class="crtnote">barriera {it['barriera_tipo']}, osservata a ogni seduta · se scatta e chiude sotto strike: {it['azioni']:.0f} azioni {it['sottostante']}</div>
</div>''')
    h.append('</div>')
    return ''.join(h)

def allerta(B, FX, FXP, dcus, pos):
    """Solo cio che scatta. Nessun avviso nei giorni normali."""
    A = []
    for v, b in B.items():
        for it in b['cat'].get('Certificati', {}).get('it', []):
            c = it.get('cuscino')
            if c is None: continue
            if c < 0.20: A.append(('crit', f"{it['nome']}: cuscino {c*100:.1f}%, barriera {it['barriera']:.2f}"))
            d = dcus.get(it.get('tk'))
            if d is not None and d <= -0.03:
                A.append(('warn', f"{it['nome']}: cuscino {abs(d)*100:.1f} punti in una seduta"))
            if it.get('gg', 999) <= 60: A.append(('warn', f"{it['nome']} scade fra {it['gg']} giorni"))
        for cat, c in b['cat'].items():
            for it in c['it']:
                if abs(it.get('d', 0)) >= 0.04:
                    A.append(('warn', f"{breve(it['nome'])}: {p(it['d'])}% in giornata"))
    for v in ('CHF', 'USD'):
        if v in FX and v in FXP and FXP[v]:
            x = FX[v] / FXP[v] - 1
            if abs(x) >= 0.005: A.append(('warn', f"EUR/{v} {p(-x)}% in giornata"))
    if not A: return ''
    h = ['<div class="card alr"><h2>Da guardare</h2>']
    for cls, t in A[:6]:
        h.append(f'<div class="al"><span class="adot" style="background:var(--{cls})"></span>{t}</div>')
    return ''.join(h) + '</div>'


# ═══════════════ versione consultabile: header fisso e pannelli ═══════════════
CSS_APP = """
.app{max-width:520px;margin:0 auto;padding:0 12px 90px}
.sticky{position:sticky;top:0;z-index:20;background:linear-gradient(var(--plane) 78%,transparent);
        padding:10px 0 8px;margin:0 -12px;padding-left:12px;padding-right:12px}
.shead{display:flex;justify-content:space-between;align-items:flex-end;gap:10px}
.sval{font-size:27px;font-weight:650;letter-spacing:-.8px;line-height:1}
.sval small{font-size:12px;font-weight:500;color:var(--muted);letter-spacing:0;margin-left:3px}
.sday{font-size:13px;font-weight:650;text-align:right;line-height:1.25}
.sday span{display:block;font-size:9.5px;font-weight:500;color:var(--muted);letter-spacing:.04em}
.per{display:flex;gap:5px;margin-top:9px}
.per div{flex:1;background:var(--surface);border-radius:9px;padding:6px 4px;text-align:center}
.per b{display:block;font-size:12.5px;font-weight:650;font-variant-numeric:tabular-nums}
.per span{font-size:8.5px;letter-spacing:.06em;text-transform:uppercase;color:var(--muted)}
.tabs{display:flex;gap:4px;overflow-x:auto;padding:9px 0 2px;margin:0 -12px;padding-left:12px;
      padding-right:12px;scrollbar-width:none}
.tabs::-webkit-scrollbar{display:none}
.tab{flex:none;background:none;border:1px solid var(--grid);color:var(--muted);
     font:600 11.5px/1 inherit;padding:7px 12px;border-radius:16px;cursor:pointer;
     white-space:nowrap;transition:background .12s,color .12s,border-color .12s}
.tab:hover{color:var(--ink-2)}
.tab:focus-visible{outline:2px solid var(--s1);outline-offset:2px}
.tab[aria-selected=true]{background:var(--ink);border-color:var(--ink);color:var(--plane)}
.tab .pip{display:inline-block;width:5px;height:5px;border-radius:50%;margin-left:5px;vertical-align:1px}
.panel[hidden]{display:none}
.panel{display:flex;flex-direction:column;gap:10px;padding-top:10px}
@media (prefers-reduced-motion:reduce){.tab{transition:none}}
"""

def render_app(B, FX, FXP, oggi, ora, pos, per, fs=None, dcus=None):
    """Stessi numeri, ma organizzati per la consultazione: intestazione sempre
    visibile e un pannello per volta invece di una colonna unica."""
    dcus = dcus or {}
    tot  = sum(b['val_eur'] for b in B.values())
    prev = sum(b['prev_eur'] for b in B.values())
    d_eur = tot - prev; d_pct = d_eur / prev if prev else 0
    netto = fs['netto'] if fs else 0
    ini = (tot / netto - 1) if netto else None

    al = allerta(B, FX, FXP, dcus, pos)
    n_al = al.count('class="al"')
    cert = certificati(B, dcus)
    peggior = min((it['cuscino'] for v, b in B.items()
                   for it in b['cat'].get('Certificati', {}).get('it', [])
                   if it.get('cuscino') is not None), default=None)

    h = [f'''<title>Portafoglio Swissquote</title>
<style>{CSS}{CSS_APP}</style>
<div class="app">
<div class="sticky">
  <div class="shead">
    <div><div class="sval num">{f(tot)}<small>EUR</small></div></div>
    <div class="sday num {k(d_eur)}"><span>{oggi.strftime("%d.%m")} · {ora}</span>
      {'▲' if d_eur>=0 else '▼'} {f(abs(d_eur))} · {p(d_pct)}%</div>
  </div>
  <div class="per num">
    <div><span>Inizio</span><b class="{k(ini)}">{p(ini)}</b></div>
    <div><span>MTD</span><b class="{k(per.get('MTD'))}">{p(per.get('MTD'))}</b></div>
    <div><span>QTD</span><b class="{k(per.get('QTD'))}">{p(per.get('QTD'))}</b></div>
    <div><span>YTD</span><b class="{k(per.get('YTD'))}">{p(per.get('YTD'))}</b></div>
  </div>
  <div class="tabs" role="tablist">''']

    tabs = [('sintesi', 'Sintesi', '')]
    for v in ['CHF', 'EUR', 'USD']:
        if v in B and B[v]['val']:
            b = B[v]
            tabs.append((v.lower(), f"{v} <span class='w'>{b['val_eur']/tot*100:.0f}%</span>", ''))
    if cert:
        cls, _ = fascia(peggior) if peggior is not None else ('good', '')
        tabs.append(('cert', 'Certificati', f'<span class="pip" style="background:var(--{cls})"></span>'))
    if al:
        tabs.append(('alert', f'Da guardare', f'<span class="pip" style="background:var(--warn)"></span>'))

    for i, (id_, lab, pip) in enumerate(tabs):
        h.append(f'<button class="tab" role="tab" aria-selected="{str(i==0).lower()}" '
                 f'aria-controls="p-{id_}" id="t-{id_}">{lab}{pip}</button>')
    h.append('</div></div>')

    # sintesi
    from collections import defaultdict
    S = defaultdict(float)
    for v, b in B.items():
        fxv = b['val_eur'] / b['val'] if b['val'] else 1
        for cat, c in b['cat'].items(): S[cat] += c['val'] * fxv
    h.append(f'''<div class="panel" id="p-sintesi" role="tabpanel" aria-labelledby="t-sintesi">
<div class="card"><h2>Il movimento di oggi</h2>
<div style="font-size:12px;color:var(--ink-2);line-height:1.5">{attribuzione(B, FX)}</div>
<div class="accr"><span>CHF/EUR <b class="num">{FX['CHF']:.4f}</b> <span class="w">EUR/CHF {1/FX['CHF']:.4f}</span> · USD/EUR <b class="num">{FX['USD']:.4f}</b></span>
<span class="num">IRR {p(fs['irr'])}% ann.{f" · {fs['giorni']} gg" if fs else ""}</span></div></div>
<div class="card"><h2>Come è ripartito</h2><div class="bar">''')
    for cat in ORD:
        if not S.get(cat): continue
        pc = S[cat] / tot * 100
        h.append(f'<div class="seg" style="background:var({COL[cat]});flex:{pc:.2f}">{pc:.0f}%</div>')
    h.append('</div><div class="leg">')
    for cat in ORD:
        if not S.get(cat): continue
        h.append(f'<span><span class="dot" style="background:var({COL[cat]})"></span>{cat} {f(S[cat])}</span>')
    h.append('</div></div>')
    h.append(f'''<div class="card"><h2>Per valuta</h2><table class="num">
<tr><th></th><th>Valore EUR</th><th>Peso</th><th>1G</th><th>YTD</th></tr>''')
    for v in ['CHF', 'EUR', 'USD']:
        if v not in B or not B[v]['val']: continue
        b = B[v]; pe = b.get('per_eur') or {}
        h.append(f'''<tr class="cat"><td>{v}</td><td>{f(b['val_eur'])}</td>
<td>{b['val_eur']/tot*100:.1f}%</td><td class="{k(b['d_eur'])}">{p(b['d_eur'])}</td>
<td class="{k(pe.get('YTD'))}">{p(pe.get('YTD'))}</td></tr>''')
    h.append('</table></div></div>')

    # un pannello per valuta
    for v in ['CHF', 'EUR', 'USD']:
        if v not in B or not B[v]['val']: continue
        h.append(f'<div class="panel" id="p-{v.lower()}" role="tabpanel" aria-labelledby="t-{v.lower()}" hidden>'
                 + scheda(v, B[v], tot, per, attrib_blocco(B[v], v)) + '</div>')
    if cert:
        h.append(f'<div class="panel" id="p-cert" role="tabpanel" aria-labelledby="t-cert" hidden>{cert}</div>')
    if al:
        h.append(f'<div class="panel" id="p-alert" role="tabpanel" aria-labelledby="t-alert" hidden>{al}</div>')

    h.append('''</div>
<script>
(function(){
  var tabs = [].slice.call(document.querySelectorAll('.tab'));
  function apri(t){
    tabs.forEach(function(x){
      var on = x === t;
      x.setAttribute('aria-selected', on);
      document.getElementById(x.getAttribute('aria-controls')).hidden = !on;
    });
    window.scrollTo({top:0});
  }
  tabs.forEach(function(t, i){
    t.addEventListener('click', function(){ apri(t); });
    t.addEventListener('keydown', function(e){
      var d = e.key === 'ArrowRight' ? 1 : e.key === 'ArrowLeft' ? -1 : 0;
      if (!d) return;
      e.preventDefault();
      var n = tabs[(i + d + tabs.length) % tabs.length];
      n.focus(); apri(n);
    });
  });
})();
</script>''')
    return ''.join(h)


# ═════════════════════ MAIN ═══════════════════════
"""Report giornaliero. Legge prezzi2.json, scrive report.html e report.png.
   Uso:  python3 run.py [AAAA-MM-GG] [ora]"""
import json, sys, os, subprocess
from datetime import date, datetime

BASE = os.path.dirname(os.path.abspath(__file__))
P = lambda f: os.path.join(BASE, f)

def main():
    oggi = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else date.today()
    ora  = sys.argv[2] if len(sys.argv) > 2 else datetime.now().strftime('%H:%M')

    D   = load('dati.json') or {}
    pos = D['posizioni']; pz = load('prezzi2.json')
    S_prec = D.get('serie', {})

    # ── rete di sicurezza: prezzo assente o fuori scala -> uso l'ultimo buono ──
    gg_prec = [d for d in sorted(S_prec) if d < oggi.isoformat()]
    prec = S_prec[gg_prec[-1]] if gg_prec else {}
    recuperati, sospetti = [], []
    # Un ticker OMESSO da prezzi2.json non compariva in pz.items() e non veniva mai
    # visitato: la rete di sicurezza non scattava e build() azzerava la posizione.
    # Il 28.08.2026 cinque ticker omessi valevano 110'000 EUR, -9.12% sul patrimonio.
    # Qui l'elenco di cio che il report si aspetta viene dalle posizioni, non dai prezzi.
    attesi  = [t['tk'] for t in pos['titoli']]
    attesi += [c['tk'] for c in pos['certificati']]
    attesi += [c['sottostante'] for c in pos['certificati'] if c.get('sottostante')]
    if pos.get('invest_easy'): attesi.append('AMBTSQ')
    for k in attesi:
        if k not in pz: pz[k] = {'px': None}
    for k, v in list(pz.items()):
        if k.startswith('_'): continue
        old = (prec.get(k) or {}).get('px')
        px = v.get('px')
        # None, zero e negativi sono tutti "prezzo non arrivato". Lo zero e il caso
        # insidioso: non e None e in piu e falsy, quindi passava indenne entrambi i
        # controlli e azzerava la posizione senza un avviso — IWDS il 27.08.2026,
        # 33'000 EUR spariti dal patrimonio.
        if px is None or not isinstance(px, (int, float)) or px <= 0:
            if old: pz[k] = {'px': old, 'prev': old, 'stimato': True}; recuperati.append(k)
            else:   pz[k] = {'px': None}; recuperati.append(k + ' (nessuno storico)')
        elif old and abs(px / old - 1) > 0.15:
            sospetti.append(f"{k} {old} -> {px}")
            pz[k] = {'px': old, 'prev': old, 'stimato': True}
        elif v.get('prev') is None and old:
            pz[k]['prev'] = old                      # 1G calcolato sulla mia serie

    B, FX, FXP = build(pos, pz, oggi, D['costo_eur'])
    tot = sum(b['val_eur'] for b in B.values())
    Per = periodi(B, tot, D['riferimenti'])
    S_new, dcus = serie(B, oggi, FX, S_prec, tot)

    html = render(B, FX, FXP, oggi, ora, pos,
                          {k: Per['TOT'][k] for k in ('MTD', 'QTD', 'YTD')},
                          None, D.get('flussi_sintesi'), dcus)
    open(P('report.html'), 'w').write(html)
    per3 = {x: Per['TOT'][x] for x in ('MTD', 'QTD', 'YTD')}
    open(P('app.html'), 'w').write(
        render_app(B, FX, FXP, oggi, ora, pos, per3, D.get('flussi_sintesi'), dcus))
    D['serie'] = S_new
    json.dump(D, open(P('dati.json'), 'w'), ensure_ascii=False, separators=(',', ':'))

    if os.path.exists(P('shot.js')):
        try: subprocess.run(['node', P('shot.js')], timeout=60, check=False)
        except Exception as e: print('screenshot saltato:', e)

    print(f"{oggi} {ora} · patrimonio {tot:,.0f} EUR · 1G {sum(b['val_eur']-b['prev_eur'] for b in B.values()):+,.0f}")
    # il patrimonio dell'ultima sera archiviata: un salto oltre il 2% non e un
    # movimento di mercato ma un prezzo sbagliato
    ti = prec.get('_tot')
    if ti and abs(tot / ti - 1) > 0.02:
        print(f"!! ATTENZIONE: {tot:,.0f} contro {ti:,.0f} del {gg_prec[-1]} "
              f"({(tot/ti-1)*100:+.2f}%). Un salto simile non e un movimento di mercato: "
              f"controlla i prezzi prima di pubblicare.")
    print(f"MTD {Per['TOT']['MTD']:+.2%} · QTD {Per['TOT']['QTD']:+.2%} · YTD {Per['TOT']['YTD']:+.2%}")
    if recuperati: print('prezzi non arrivati, usato ieri:', ', '.join(recuperati))
    if sospetti:   print('prezzi scartati perche fuori scala:', ' | '.join(sospetti))


if __name__ == '__main__':
    main()
