# -*- coding: utf-8 -*-
"""
Football Week：選手ごとに「どちらの足でゴールしたか」（左足・右足・頭・その他）を数えて data/goal-feet.json に書き出す。
GitHub Actions で毎晩動かす（.github/workflows/position-averages.yml の中で一緒に実行）。

・ESPNの選手の成績には「足ごとのゴール数」が無い（シュート数だけ）。試合の実況（summary の keyEvents）には
  「left footed shot」「right footed shot」「header」と書かれているので、それを数える。
・実況は1試合400KB前後と重いので、見る人のスマホではなくここで数え、結果（小さいファイル）だけをサイトに置く。
・一度数えた試合は記録しておき、次からは新しく終わった試合だけを読む。
・標準ライブラリだけで動く。
"""
import json, os, sys, time, datetime, urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor

LEAGUES = ['eng.1', 'esp.1', 'ger.1', 'ita.1', 'fra.1', 'uefa.champions', 'uefa.europa']
SITE = 'https://site.api.espn.com/apis/site/v2/sports/soccer'
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36'
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'data', 'goal-feet.json')


def get(url, tries=4):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept': 'application/json'})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode('utf-8'))
        except Exception as e:
            if isinstance(e, urllib.error.HTTPError) and e.code == 404:
                return None
            time.sleep(1.5 * (i + 1))
    return None


def current_season():
    d = datetime.datetime.now(datetime.timezone.utc)
    return d.year if d.month >= 8 else d.year - 1


def finished_events(lg):
    """その大会の今季の終わった試合のID（各チームの日程から集める）"""
    teams = get(f'{SITE}/{lg}/teams') or {}
    ids = [t['team']['id'] for t in (teams.get('sports', [{}])[0].get('leagues', [{}])[0].get('teams', []) or [])]
    evs = set()
    with ThreadPoolExecutor(6) as ex:
        for sch in ex.map(lambda tid: get(f'{SITE}/{lg}/teams/{tid}/schedule'), ids):
            for e in (sch or {}).get('events', []) or []:
                c = (e.get('competitions') or [{}])[0]
                if (c.get('status') or {}).get('type', {}).get('completed'):
                    evs.add(str(e['id']))
    return evs


def foot_of(text):
    t = (text or '').lower()
    if 'left footed' in t or 'left-footed' in t:
        return 0
    if 'right footed' in t or 'right-footed' in t:
        return 1
    if 'header' in t or 'headed' in t:
        return 2
    return 3   # その他（胸・不明など）


def goals_in(lg, eid):
    d = get(f'{SITE}/{lg}/summary?event={eid}')
    if d is None:
        return None
    out = []
    for k in d.get('keyEvents') or []:
        typ = ((k.get('type') or {}).get('type') or '').lower()
        if 'own' in typ:
            continue
        if not (typ.startswith('goal') or typ == 'penalty---scored'):
            continue
        ps = k.get('participants') or []
        aid = ((ps[0] if ps else {}).get('athlete') or {}).get('id')
        if aid:
            out.append((str(aid), foot_of(k.get('text'))))
    return out


def main():
    season = current_season()
    try:
        with open(OUT, encoding='utf-8') as f:
            data = json.load(f)
        if data.get('season') != season:
            data = None
    except Exception:
        data = None
    data = data or {'season': season, 'leagues': {}}
    for lg in LEAGUES:
        L = data['leagues'].setdefault(lg, {'done': [], 'players': {}})
        done = set(L['done'])
        todo = sorted(finished_events(lg) - done)
        with ThreadPoolExecutor(6) as ex:
            results = list(ex.map(lambda e: (e, goals_in(lg, e)), todo))
        n = 0
        for eid, goals in results:
            if goals is None:
                continue   # 取れなかった試合は次回もう一度
            for aid, f in goals:
                L['players'].setdefault(aid, [0, 0, 0, 0])[f] += 1
            done.add(eid)
            n += 1
        L['done'] = sorted(done)
        print(f'{lg}: new matches={n}/{len(todo)} total={len(done)} scorers={len(L["players"])}', flush=True)
    data['updated'] = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%MZ')
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(data, f, ensure_ascii=False, separators=(',', ':'))
    print('wrote', os.path.normpath(OUT), os.path.getsize(OUT), 'bytes')


if __name__ == '__main__':
    main()
