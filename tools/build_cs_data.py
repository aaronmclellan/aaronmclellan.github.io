#!/usr/bin/env python3
"""Build the CS2 tracker's data snapshot (cs2.json plus team calendar feeds).

    python3 tools/build_cs_data.py --prev prev.json --out out
    python3 tools/build_cs_data.py --prev out/cs2.json --out out --live-only

The GRID Open Access key comes from $GRID_API_KEY (or ~/.grid_api_key), so it
never has to live in a browser. .github/workflows/cs-data.yml runs this every
10 minutes and force-pushes out/ to the cs-data branch; cs.html reads it from
raw.githubusercontent.com.

A full run:
  1. Valve's official VRS standings for the team list and rankings
     (github.com/ValveSoftware/counter-strike_regional_standings).
  2. GRID Central Data: every CS2 series from 2 days ago to 3 weeks out (the
     whole history window once a day), merged into the previous snapshot.
  3. GRID Series State: scores for live and finished series. Finished scores
     never change, so they are carried forward and fetched only once.
--live-only skips 1 and 2 and refreshes scores for live series. It exits with
status 3 when nothing is live, which ends the workflow's polling loop.

Python 3.9+, standard library only.
"""
import argparse
import json
import os
import re
import sys
import threading
import time
import unicodedata
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

CENTRAL = 'https://api-op.grid.gg/central-data/graphql'
SERIES_STATE = 'https://api-op.grid.gg/live-data-feed/series-state/graphql'
VRS_REPO = 'ValveSoftware/counter-strike_regional_standings'
UA = 'cs2-tracker/1.0 (+https://www.ron.computer/cs.html)'
CS2_TITLE_ID = 28

AHEAD = timedelta(days=21)          # schedule horizon
HISTORY = timedelta(days=45)        # results kept in the snapshot
RECENT = timedelta(days=2)          # re-read on every run
FULL_EVERY = timedelta(hours=24)    # re-read the whole window this often
VRS_EVERY = timedelta(hours=6)
LIVE_WINDOW = timedelta(hours=8)    # scheduled this recently and unfinished: check live state
STATE_RETRY = timedelta(hours=12)   # GRID had no state for an old series: ask again after this
STATE_MAX_MISSES = 3                # then stop asking
MAX_STATE_FETCHES = 150             # backlog cap per run (live series are always fetched)
STALE_OK = timedelta(hours=3)       # ride out GRID outages this long before failing the run
RANKED_KEEP = 150
ICS_PAST = timedelta(days=14)

# Minimum time between request starts. Open Access allows 20 Central Data and
# 180 Series State requests a minute; GRID takes 1-4s to answer, so Series
# State requests run a few at a time.
CENTRAL_GAP = 3.2
STATE_GAP = 0.4
STATE_WORKERS = 4

# Team names differ between Valve's standings and GRID ("Team Spirit" vs
# "Spirit", "NIP" vs "Ninjas in Pyjamas"). Both sides go through team_key().
# Keep this and teamKey() in cs.html in sync.
TEAM_ALIASES = {
    'nip': 'ninjasinpyjamas',
    'navi': 'natusvincere',
    'mongolz': 'themongolz',
    'mousesports': 'mouz',
    'vp': 'virtuspro',
    'bigequipa': 'big',
    'betboomteam': 'betboom',
    'bcgameesports': 'bcgame',
    'betclicapogee': 'betclic',
    'dendelecs': 'dendele',
}


def team_key(name):
    s = unicodedata.normalize('NFKD', name or '').encode('ascii', 'ignore').decode().lower()
    s = re.sub(r'[^a-z0-9]+', '', s)
    if s in TEAM_ALIASES:
        return TEAM_ALIASES[s]
    if s.startswith('team') and len(s) > 5:
        s = s[4:]
    for suffix in ('esports', 'esport', 'gaming', 'clan', 'team'):
        if s.endswith(suffix) and len(s) > len(suffix) + 1:
            s = s[:-len(suffix)]
            break
    return TEAM_ALIASES.get(s, s)


def log(*parts):
    print('[cs-data]', *parts, file=sys.stderr, flush=True)


def iso(dt):
    return dt.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def parse_time(s):
    return datetime.fromisoformat(s.replace('Z', '+00:00'))


def strip_empty(obj):
    """Drop None, empty strings and empty lists so the snapshot stays small."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            v = strip_empty(v)
            if v is None or v == '' or v == [] or v == {}:
                continue
            out[k] = v
        return out
    if isinstance(obj, list):
        return [strip_empty(v) for v in obj]
    return obj


# ── HTTP ─────────────────────────────────────────────────────────────────────
class GridError(Exception):
    pass


_next_slot = {}
_slot_lock = threading.Lock()


def wait_for_slot(url, gap, backoff=0):
    """Space request starts to the same endpoint at least `gap` apart."""
    with _slot_lock:
        start = max(time.monotonic() + backoff, _next_slot.get(url, 0))
        _next_slot[url] = start + gap
    delay = start - time.monotonic()
    if delay > 0:
        time.sleep(delay)


def http_get(url, headers=None):
    req = urllib.request.Request(url, headers={'User-Agent': UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode('utf-8')


def grid(url, query, variables, key, gap):
    body = json.dumps({'query': query, 'variables': variables}).encode()
    backoff = 0
    for attempt in range(6):
        wait_for_slot(url, gap, backoff)
        backoff = 0
        req = urllib.request.Request(url, body, {
            'Content-Type': 'application/json',
            'x-api-key': key,
            'User-Agent': UA,
        })
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                data = json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise GridError('GRID rejected the API key (HTTP %d)' % e.code)
            if e.code == 429 or e.code >= 500:
                backoff = int(e.headers.get('Retry-After') or 0) or min(60, 5 * 2 ** attempt)
                log('HTTP %d, retrying in %ds' % (e.code, backoff))
                continue
            raise GridError('HTTP %d: %s' % (e.code, e.read()[:300]))
        except (urllib.error.URLError, TimeoutError) as e:
            log('network error (%s), retrying' % e)
            backoff = 5 * (attempt + 1)
            continue
        if data.get('errors'):
            msg = data['errors'][0].get('message') or 'GraphQL error'
            if 'rate' in msg.lower() and 'limit' in msg.lower():
                log('rate limited (%s), retrying' % msg)
                backoff = min(60, 5 * 2 ** attempt)
                continue
            raise GridError(msg)
        return data.get('data') or {}
    raise GridError('GRID kept failing; gave up')


# ── Valve Regional Standings ─────────────────────────────────────────────────
ROW_RE = re.compile(r'^\|\s*(\d+)\s*\|\s*(\d+)\s*\|\s*([^|]+?)\s*\|\s*([^|]*?)\s*\|')


def fetch_vrs(prev, now):
    cached = prev.get('rankings')
    if cached and cached.get('teams') and now - parse_time(cached['fetchedAt']) < VRS_EVERY:
        return cached
    headers = {'Accept': 'application/vnd.github+json'}
    if os.environ.get('GITHUB_TOKEN'):
        headers['Authorization'] = 'Bearer ' + os.environ['GITHUB_TOKEN']
    try:
        years = json.loads(http_get('https://api.github.com/repos/%s/contents/live' % VRS_REPO, headers))
        year = max((y['name'] for y in years if y['type'] == 'dir' and y['name'].isdigit()), key=int)
        files = json.loads(http_get('https://api.github.com/repos/%s/contents/live/%s' % (VRS_REPO, year), headers))
        latest = max(f['name'] for f in files if re.match(r'standings_global_\d{4}_\d{2}_\d{2}\.md$', f['name']))
        text = http_get('https://raw.githubusercontent.com/%s/main/live/%s/%s' % (VRS_REPO, year, latest))
    except Exception as e:  # keep the last good standings rather than failing the run
        log('VRS fetch failed (%s); keeping previous standings' % e)
        return cached
    teams = []
    for line in text.splitlines():
        m = ROW_RE.match(line)
        if not m:
            continue
        rank, points, name, roster = m.groups()
        teams.append({
            'rank': int(rank),
            'points': int(points),
            'name': name.strip(),
            'key': team_key(name),
            'roster': [p.strip() for p in roster.split(',') if p.strip()],
        })
        if len(teams) >= RANKED_KEEP:
            break
    if not teams:
        log('VRS file %s had no rows; keeping previous standings' % latest)
        return cached
    date = re.search(r'(\d{4})_(\d{2})_(\d{2})', latest).groups()
    log('VRS standings %s: %d teams' % ('-'.join(date), len(teams)))
    return {
        'date': '-'.join(date),
        'source': 'https://github.com/%s/blob/main/live/%s/%s' % (VRS_REPO, year, latest),
        'fetchedAt': iso(now),
        'teams': teams,
    }


# ── GRID Central Data ────────────────────────────────────────────────────────
class Schema:
    """GRID's Open Access tier rejects some filters and fields. Try them once,
    drop whatever it refuses, and remember that for the rest of the run."""
    title_filter = True
    streams = True


def series_query():
    flt = 'startTimeScheduled: { gte: $from, lte: $to }'
    if Schema.title_filter:
        flt += ', titleId: %d' % CS2_TITLE_ID
    streams = 'streams { url }' if Schema.streams else ''
    return '''query AllSeries($from: String!, $to: String!, $after: String) {
  allSeries(filter: { %s }, first: 50, after: $after,
            orderBy: StartTimeScheduled, orderDirection: ASC) {
    pageInfo { hasNextPage endCursor }
    edges { node {
      id startTimeScheduled
      title { nameShortened }
      format { name nameShortened }
      %s
      tournament { id name nameShortened }
      teams { baseInfo { id name nameShortened logoUrl colorPrimary } }
    } }
  }
}''' % (flt, streams)


def series_page(key, frm, to, after):
    variables = {'from': frm, 'to': to, 'after': after}
    while True:
        try:
            return grid(CENTRAL, series_query(), variables, key, CENTRAL_GAP)['allSeries']
        except GridError as e:
            msg = str(e)
            if Schema.title_filter and re.search(r'titleId|title', msg, re.I):
                log('titleId filter rejected (%s); filtering by title instead' % msg[:120])
                Schema.title_filter = False
                continue
            if Schema.streams and re.search(r'stream', msg, re.I):
                log('streams field rejected (%s); dropping it' % msg[:120])
                Schema.streams = False
                continue
            raise


# GRID's own test fixtures show up in Open Access data.
TEST_TOURNAMENT_RE = re.compile(r'^grid[-_ ]?test', re.I)


def map_name(name):
    """'de_mirage', 'default-mirage' or 'mirage' -> 'Mirage'."""
    name = re.sub(r'^(de_|default[-_])', '', (name or '').strip(), flags=re.I)
    return name[:1].upper() + name[1:] if name else None


def compact_series(n):
    fmt = n.get('format') or {}
    m = re.search(r'(\d+)', fmt.get('nameShortened') or fmt.get('name') or '')
    t = n.get('tournament') or {}
    teams = []
    for entry in n.get('teams') or []:
        b = (entry or {}).get('baseInfo') or {}
        teams.append({
            'id': b.get('id'),
            'name': b.get('name'),
            'short': b.get('nameShortened'),
            'logo': b.get('logoUrl'),
            'color': b.get('colorPrimary'),
            'key': team_key(b.get('name')),
        })
    return {
        'id': n['id'],
        'time': n['startTimeScheduled'],
        'bo': int(m.group(1)) if m else None,
        'tournament': {'id': t.get('id'), 'name': (t.get('name') or '').strip(),
                       'short': (t.get('nameShortened') or '').strip()},
        'teams': teams,
        'streams': [s['url'] for s in (n.get('streams') or []) if s and s.get('url')],
    }


def fetch_window(key, lo, hi):
    """Every CS2 series scheduled in [lo, hi], keyed by id."""
    out, after, pages = {}, None, 0
    while True:
        page = series_page(key, iso(lo), iso(hi), after)
        pages += 1
        for edge in page.get('edges') or []:
            n = edge.get('node') or {}
            if (n.get('title') or {}).get('nameShortened', '').lower() != 'cs2':
                continue
            if TEST_TOURNAMENT_RE.match(((n.get('tournament') or {}).get('name') or '').strip()):
                continue
            if n.get('id') and n.get('startTimeScheduled'):
                out[n['id']] = compact_series(n)
        info = page.get('pageInfo') or {}
        if not info.get('hasNextPage') or pages >= 400:
            break
        after = info.get('endCursor')
    if not out and Schema.title_filter:
        # An accepted filter that matches nothing means the title id is wrong.
        log('titleId filter returned no CS2 series; filtering by title instead')
        Schema.title_filter = False
        return fetch_window(key, lo, hi)
    log('series %s..%s: %d CS2 series over %d pages' % (iso(lo)[:10], iso(hi)[:10], len(out), pages))
    return out


# ── GRID Series State ────────────────────────────────────────────────────────
STATE_QUERY = '''query SeriesState($id: ID!) {
  seriesState(id: $id) {
    valid started finished forfeited updatedAt
    teams { ... on SeriesTeamStateCs2 { id score won } }
    games {
      sequenceNumber started finished
      map { name }
      teams { ... on GameTeamStateCs2 { id score won side } }
    }
  }
}'''


def fetch_state(key, series_id):
    try:
        st = grid(SERIES_STATE, STATE_QUERY, {'id': series_id}, key, STATE_GAP).get('seriesState')
    except GridError as e:
        log('state %s: %s' % (series_id, str(e)[:160]))
        return None
    if not st:
        return None
    return strip_empty({
        'valid': st.get('valid'),
        'started': st.get('started'),
        'finished': st.get('finished'),
        'forfeited': st.get('forfeited'),
        'updatedAt': st.get('updatedAt'),
        'teams': [{'id': t.get('id'), 'score': t.get('score'), 'won': t.get('won')}
                  for t in st.get('teams') or [] if t],
        'games': [{
            'n': g.get('sequenceNumber'),
            'started': g.get('started'),
            'finished': g.get('finished'),
            'map': map_name((g.get('map') or {}).get('name')),
            'teams': [{'id': t.get('id'), 'score': t.get('score'), 'won': t.get('won'), 'side': t.get('side')}
                      for t in g.get('teams') or [] if t],
        } for g in st.get('games') or [] if g],
    })


def is_live(s):
    st = s.get('state') or {}
    return bool(st.get('started')) and not st.get('finished')


def is_live_candidate(s, now, live_only):
    st = s.get('state') or {}
    if st.get('finished'):
        return False
    t = parse_time(s['time'])
    if live_only:  # the polling loop: matches in progress, plus ones due to start
        return is_live(s) or now - timedelta(minutes=30) <= t <= now + timedelta(minutes=5)
    return now - LIVE_WINDOW <= t <= now + timedelta(minutes=15)


def refresh_states(key, series, ranked_keys, now, live_only=False):
    """Fetch scores; returns True when a match is in progress."""
    live = [s for s in series if is_live_candidate(s, now, live_only)]
    live_ids = {s['id'] for s in live}
    backlog = []
    if not live_only:
        for s in series:
            st = s.get('state') or {}
            if st.get('finished') or s['id'] in live_ids:
                continue
            t = parse_time(s['time'])
            if t > now:
                continue
            checked = s.get('stateCheckedAt')
            if checked and now - parse_time(checked) < STATE_RETRY:
                continue
            if s.get('stateMisses', 0) >= STATE_MAX_MISSES:
                continue
            backlog.append(s)
        # Ranked teams first, then most recent.
        backlog.sort(key=lambda s: (not any(tm.get('key') in ranked_keys for tm in s['teams']),
                                    -parse_time(s['time']).timestamp()))
        backlog = backlog[:MAX_STATE_FETCHES]
    got = 0
    targets = live + backlog
    with ThreadPoolExecutor(max_workers=STATE_WORKERS) as pool:
        states = list(pool.map(lambda s: fetch_state(key, s['id']), targets))
    for s, st in zip(targets, states):
        s['stateCheckedAt'] = iso(now)
        if st:
            s['state'] = st
            s.pop('stateMisses', None)
            got += 1
        elif not s.get('state'):
            s['stateMisses'] = s.get('stateMisses', 0) + 1
    log('series state: %d live candidates, %d backlog, %d with data' % (len(live), len(backlog), got))
    return any(is_live(s) for s in live)


# ── Calendar feeds ───────────────────────────────────────────────────────────
def ics_text(s):
    return re.sub(r'([,;\\])', r'\\\1', str(s or '')).replace('\n', '\\n')


def ics_time(dt):
    return dt.astimezone(timezone.utc).strftime('%Y%m%dT%H%M%SZ')


def series_title(s):
    a, b = (s['teams'] + [{}, {}])[:2]
    na, nb = a.get('name') or 'TBD', b.get('name') or 'TBD'
    st = s.get('state') or {}
    if st.get('finished'):
        scores = {t.get('id'): t.get('score') for t in st.get('teams') or []}
        sa, sb = scores.get(a.get('id')), scores.get(b.get('id'))
        if isinstance(sa, int) and isinstance(sb, int):
            return '%s %d-%d %s' % (na, sa, sb, nb)
    return '%s vs %s' % (na, nb)


def write_ics(path, cal_name, series, now):
    lines = [
        'BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//CS2 Pro Tracker//EN', 'CALSCALE:GREGORIAN',
        'METHOD:PUBLISH', 'X-WR-CALNAME:' + ics_text(cal_name),
        'REFRESH-INTERVAL;VALUE=DURATION:PT1H', 'X-PUBLISHED-TTL:PT1H',
    ]
    for s in series:
        start = parse_time(s['time'])
        hours = {1: 1.5, 3: 3, 5: 5}.get(s.get('bo'), 2)
        desc = [s['tournament'].get('name') or '']
        if s.get('bo'):
            desc.append('Best of %d' % s['bo'])
        desc += s.get('streams', [])[:3]
        desc.append('https://www.ron.computer/cs.html')
        lines += [
            'BEGIN:VEVENT',
            'UID:cs2-%s@aaronmclellan.github.io' % s['id'],
            'DTSTAMP:' + ics_time(now),
            'DTSTART:' + ics_time(start),
            'DTEND:' + ics_time(start + timedelta(hours=hours)),
            'SUMMARY:' + ics_text(series_title(s)),
            'DESCRIPTION:' + ics_text('\n'.join(d for d in desc if d)),
            'LOCATION:' + ics_text(s['tournament'].get('name') or 'CS2'),
            'END:VEVENT',
        ]
    lines.append('END:VCALENDAR')
    # RFC 5545 wants CRLF line endings and lines folded at 75 octets.
    folded = []
    for line in lines:
        raw = line.encode('utf-8')
        while len(raw) > 75:
            cut = 75
            while (raw[cut] & 0xC0) == 0x80:  # don't split a UTF-8 character
                cut -= 1
            folded.append(raw[:cut].decode('utf-8'))
            raw = b' ' + raw[cut:]
        folded.append(raw.decode('utf-8'))
    path.write_text('\r\n'.join(folded) + '\r\n', encoding='utf-8')


def write_calendars(out, rankings, series, now):
    cal_dir = out / 'ics'
    cal_dir.mkdir(parents=True, exist_ok=True)
    for old in cal_dir.glob('*.ics'):
        old.unlink()
    by_key = {}
    for s in series:
        if parse_time(s['time']) < now - ICS_PAST:
            continue
        for t in s['teams']:
            by_key.setdefault(t.get('key'), []).append(s)
    written = 0
    for team in (rankings or {}).get('teams', []):
        matches = by_key.get(team['key'])
        if matches:
            write_ics(cal_dir / (team['key'] + '.ics'), team['name'] + ' (CS2)', matches, now)
            written += 1
    log('calendar feeds: %d teams' % written)


# ── Main ─────────────────────────────────────────────────────────────────────
def load_key():
    key = os.environ.get('GRID_API_KEY', '').strip()
    if not key:
        f = Path.home() / '.grid_api_key'
        if f.exists():
            key = f.read_text().strip()
    if not key:
        sys.exit('No GRID key: set GRID_API_KEY or write it to ~/.grid_api_key')
    return key


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--prev', help='previous cs2.json to build on')
    ap.add_argument('--out', default='out', help='output directory')
    ap.add_argument('--live-only', action='store_true', help='only refresh live scores')
    args = ap.parse_args()

    key = load_key()
    now = datetime.now(timezone.utc)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    prev = {}
    if args.prev and Path(args.prev).exists():
        try:
            prev = json.loads(Path(args.prev).read_text(encoding='utf-8') or '{}')
        except ValueError:
            log('previous snapshot unreadable; starting fresh')
    if prev.get('v') != 1:
        prev = {}

    series = {s['id']: s for s in prev.get('series', [])
              if not TEST_TOURNAMENT_RE.match(s['tournament'].get('name') or '')}
    rankings = prev.get('rankings')
    full_at = prev.get('fullAt')

    if not args.live_only:
        rankings = fetch_vrs(prev, now) or rankings
        full = not full_at or now - parse_time(full_at) >= FULL_EVERY
        lo = now - (HISTORY if full else RECENT)
        hi = now + AHEAD
        try:
            fresh = fetch_window(key, lo, hi)
        except GridError as e:
            # A failed scheduled run emails the repo owner, so skip quietly
            # while the published snapshot is still recent.
            if prev.get('generatedAt') and now - parse_time(prev['generatedAt']) < STALE_OK:
                log('GRID unavailable (%s); keeping the previous snapshot' % e)
                return
            raise
        for sid, s in list(series.items()):
            if lo <= parse_time(s['time']) <= hi and sid not in fresh:
                del series[sid]  # cancelled, or moved out of the window
        for sid, s in fresh.items():
            old = series.get(sid) or {}
            for carry in ('state', 'stateCheckedAt', 'stateMisses'):
                if carry in old:
                    s[carry] = old[carry]
            series[sid] = s
        if full:
            full_at = iso(now)
        for sid in [sid for sid, s in series.items() if parse_time(s['time']) < now - HISTORY]:
            del series[sid]

    ordered = sorted(series.values(), key=lambda s: s['time'])
    ranked_keys = {t['key'] for t in (rankings or {}).get('teams', [])}
    live = refresh_states(key, ordered, ranked_keys, now, live_only=args.live_only)

    # Attach logos to the standings so the team picker can show them.
    if rankings:
        logos = {}
        for s in ordered:
            for t in s['teams']:
                if t.get('logo') and t.get('key'):
                    logos[t['key']] = t['logo']
        for t in rankings.get('teams', []):
            if t['key'] in logos:
                t['logo'] = logos[t['key']]

    snapshot = strip_empty({
        'v': 1,
        'generatedAt': iso(now),
        'fullAt': full_at,
        'live': live,
        'rankings': rankings,
        'series': ordered,
    })
    tmp = out / 'cs2.json.tmp'
    tmp.write_text(json.dumps(snapshot, separators=(',', ':'), ensure_ascii=False), encoding='utf-8')
    tmp.replace(out / 'cs2.json')
    if not args.live_only:
        write_calendars(out, rankings, ordered, now)
    log('wrote %d series (%s)' % (len(ordered), 'live matches on' if live else 'nothing live'))
    if args.live_only and not live:
        sys.exit(3)


if __name__ == '__main__':
    main()
