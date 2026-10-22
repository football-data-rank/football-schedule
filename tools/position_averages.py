# -*- coding: utf-8 -*-
"""
Football Week：5大リーグの「ポジション別の平均的な選手」を計算して data/position-averages.json に書き出す。
GitHub Actions で毎晩1回動かす（.github/workflows/position-averages.yml）。手元でも `python tools/position_averages.py` で動く。

・選手ごとの詳しい成績（出場時間・パス・タックル・インターセプト等）はESPNで1人ずつしか取れないため、
  見る人のスマホではなく、ここでまとめて計算し、結果（数KB）だけをサイトに置く。
・取ってきた元のデータは保存しない（計算が終わったら捨てる）。
・標準ライブラリだけで動く（追加のインストール不要）。
"""
import json, os, sys, time, datetime, urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor

LEAGUES = ['eng.1', 'esp.1', 'ger.1', 'ita.1', 'fra.1']
SITE = 'https://site.api.espn.com/apis/site/v2/sports/soccer'
CORE = 'https://sports.core.api.espn.com/v2/sports/soccer/leagues'
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36'
MIN_MINUTES = 270          # 比較の対象にする選手：出場時間が3試合分以上
POS = {'G': 'GK', 'D': 'DF', 'M': 'MF', 'F': 'FW'}
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'data', 'position-averages.json')

# 指標：(名前, 種類, 分子, 分母, 個人の値として扱う最低の分母)
#   per90 … 分子 ÷ 出場時間 × 90、rate … 分子 ÷ 分母（%）
METRICS = {
    'goals90':     ('per90', 'totalGoals', None, 0),
    'assists90':   ('per90', 'goalAssists', None, 0),
    'shots90':     ('per90', 'totalShots', None, 0),
    'keyPasses90': ('per90', 'shotAssists', None, 0),
    'passes90':    ('per90', 'totalPasses', None, 0),
    'tackles90':   ('per90', 'totalTackles', None, 0),
    'intercept90': ('per90', 'interceptions', None, 0),
    'clear90':     ('per90', 'totalClearance', None, 0),
    'recov90':     ('per90', 'recoveries', None, 0),
    'saves90':     ('per90', 'saves', None, 0),
    'conceded90':  ('per90', 'goalsConceded', None, 0),
    'shotOnPct':   ('rate', 'shotsOnTarget', 'totalShots', 5),
    'convPct':     ('rate', 'totalGoals', 'totalShots', 5),
    'passPct':     ('rate', 'accuratePasses', 'totalPasses', 50),
    'tacklePct':   ('rate', 'effectiveTackles', 'totalTackles', 5),
    'duelPct':     ('rate', 'duelsWon', 'duels', 10),
    'savePct':     ('rate', 'saves', ('saves', 'goalsConceded'), 10),   # 枠内に飛んできたシュートの数が無いので セーブ÷(セーブ+失点)
}
NEEDED = {'minutes', 'appearances'} | {m[1] for m in METRICS.values()} | {x for m in METRICS.values() if m[2] for x in (m[2] if isinstance(m[2], tuple) else (m[2],))}


def den_of(m, den):
    return sum(m.get(x, 0) for x in den) if isinstance(den, tuple) else m.get(den, 0)


def get(url, tries=4):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept': 'application/json'})
            with urllib.request.urlopen(req, timeout=25) as r:
                return json.loads(r.read().decode('utf-8'))
        except Exception as e:
            if isinstance(e, urllib.error.HTTPError) and e.code == 404:
                return None
            time.sleep(1.5 * (i + 1))
    return None


def current_season():
    d = datetime.datetime.now(datetime.timezone.utc)
    return d.year if d.month >= 8 else d.year - 1


def stat_map(js):
    m = {}
    for c in (js or {}).get('splits', {}).get('categories', []) or []:
        for s in c.get('stats', []) or []:
            if s.get('name') in NEEDED and s['name'] not in m:
                m[s['name']] = s.get('value') or 0
    return m


def league_players(lg):
    teams = get(f'{SITE}/{lg}/teams') or {}
    ids = [t['team']['id'] for t in (teams.get('sports', [{}])[0].get('leagues', [{}])[0].get('teams', []) or [])]
    out = []
    with ThreadPoolExecutor(6) as ex:
        for roster in ex.map(lambda tid: get(f'{SITE}/{lg}/teams/{tid}/roster'), ids):
            for a in (roster or {}).get('athletes', []) or []:
                pos = POS.get((a.get('position') or {}).get('abbreviation', ''))
                apps = stat_map(a.get('statistics')).get('appearances', 0)
                if pos and apps >= 2:   # 2試合以上出た選手だけ詳しい成績を取る（取得回数を減らす）
                    out.append((a['id'], pos))
    return len(ids), out


def compute(lg, season):
    nteams, players = league_players(lg)
    def fetch(p):
        aid, pos = p
        return pos, stat_map(get(f'{CORE}/{lg}/seasons/{season}/types/1/athletes/{aid}/statistics'))
    with ThreadPoolExecutor(8) as ex:
        rows = [r for r in ex.map(fetch, players) if r[1].get('minutes', 0) >= MIN_MINUTES]
    res = {}
    for pos in ('GK', 'DF', 'MF', 'FW'):
        group = [m for p, m in rows if p == pos]
        if len(group) < 5:
            continue
        mins = sum(m.get('minutes', 0) for m in group)
        mm = {}
        for name, (kind, num, den, min_den) in METRICS.items():
            if kind == 'per90':
                avg = sum(m.get(num, 0) for m in group) / mins * 90
                vals = sorted(m.get(num, 0) / m['minutes'] * 90 for m in group)
            else:
                d = sum(den_of(m, den) for m in group)
                if d <= 0:
                    continue
                avg = sum(m.get(num, 0) for m in group) / d * 100
                vals = sorted(m.get(num, 0) / den_of(m, den) * 100 for m in group if den_of(m, den) >= min_den)
            if not vals:
                continue
            p90 = vals[min(len(vals) - 1, int(len(vals) * 0.9))]   # 上位10%の境目（横棒の目盛りに使う）
            mm[name] = {'avg': round(avg, 3), 'p90': round(p90, 3)}
        res[pos] = {'n': len(group), 'm': mm}
    print(f'{lg}: teams={nteams} fetched={len(players)} qualified={len(rows)} ' +
          ' '.join(f'{k}={v["n"]}' for k, v in res.items()), flush=True)
    return res


def main():
    season = current_season()
    out = {'updated': datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%MZ'), 'season': season,
           'minMinutes': MIN_MINUTES, 'leagues': {}}
    for lg in LEAGUES:
        try:
            r = compute(lg, season)
            if r:
                out['leagues'][lg] = r
        except Exception as e:
            print(f'{lg}: failed {e}', file=sys.stderr, flush=True)
    if not out['leagues']:
        print('no data — keep the previous file', file=sys.stderr)
        sys.exit(1)
    # 前回の結果があり、今回取れなかったリーグは前回分を残す（ESPNの一時的な不調で消さない）
    try:
        with open(OUT, encoding='utf-8') as f:
            prev = json.load(f)
        for lg, v in prev.get('leagues', {}).items():
            out['leagues'].setdefault(lg, v)
    except Exception:
        pass
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(out, f, ensure_ascii=False, separators=(',', ':'))
    print('wrote', os.path.normpath(OUT), os.path.getsize(OUT), 'bytes')


if __name__ == '__main__':
    main()
