"""每日涨停汇总：多源采集 → 题材分组聚合 → 本地落库 → 页面/接口输出。

数据口径：
- 股票全集以东方财富涨停池为准（同花顺/开盘啦名单做并集补充，标注 sources）；
- 题材分组以开盘啦复盘/梯队为准，未分组股票按同花顺涨停原因首标签归并；
- 涨停原因：开盘啦原因全文优先，韭研公社异动解析补充，同花顺标签兜底；
- 题材强度取开盘啦强度榜（仅近 2 个交易日），历史逐板块用 GetPlate_Info_QJ 回补；
- 排序：题材按开盘啦强度降序（无强度按涨停数降序）；题材内按连板数降序、同身位按首封时间升序。

每天 17:00（北京时间）自动采集当日数据并落库，失败每 10 分钟重试至 20:00；
单源失败不影响其他源，sources 字段标注每只股票/题材的数据来源。
"""
import json
import re
import threading
import time
from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException, Query

from .sources import (EastmoneySource, JiuyangongsheSource, KaipanlaSource,
                      SourceError, ThsSource)
from .store import TZ, now_iso

JOB = 'limitup_daily'
_COLLECT_LOCK = threading.Lock()
_scheduler_started = False

_SCHEMA = """
CREATE TABLE IF NOT EXISTS limitup_meta(
    trade_date TEXT PRIMARY KEY,
    stats TEXT NOT NULL,
    sources TEXT NOT NULL,
    collected_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS limitup_theme(
    trade_date TEXT NOT NULL,
    theme_code TEXT NOT NULL,
    name TEXT NOT NULL,
    stock_count INTEGER NOT NULL,
    strength REAL,
    strength_rank INTEGER,
    change REAL,
    logic TEXT,
    PRIMARY KEY(trade_date, theme_code)
);
CREATE TABLE IF NOT EXISTS limitup_stock(
    trade_date TEXT NOT NULL,
    code TEXT NOT NULL,
    name TEXT NOT NULL,
    boards INTEGER,
    board_label TEXT,
    first_time TEXT,
    last_time TEXT,
    open_num INTEGER,
    seal_fund REAL,
    change REAL,
    reason TEXT,
    reason_full TEXT,
    tags TEXT,
    theme_code TEXT NOT NULL,
    theme_name TEXT NOT NULL,
    sources TEXT NOT NULL,
    PRIMARY KEY(trade_date, code)
)
"""

_BOARD_PATTERNS = [
    (re.compile(r'(\d+)天(\d+)板'), lambda m: int(m.group(2))),
    (re.compile(r'(\d+)连板'), lambda m: int(m.group(1))),
    (re.compile(r'^(\d+)板$'), lambda m: int(m.group(1))),
]


def parse_boards(label) -> int | None:
    """'5天3板'→3，'2连板'→2，'首板'→1；无法解析返回 None。"""
    if not label:
        return None
    text = str(label).strip()
    for pattern, pick in _BOARD_PATTERNS:
        m = pattern.search(text)
        if m:
            return pick(m)
    return 1 if '首板' in text else None


def _ensure_tables(db):
    db.executescript(_SCHEMA)


def _earliest(*times):
    vals = sorted(t for t in times if t)
    return vals[0] if vals else None


def _merge_stock(base: dict, extra: dict, source: str):
    """把单源数据合入股票记录；boards 取各源最大值，first_time 取最早值。"""
    if extra.get('boards') is not None:
        base['boards'] = max(base.get('boards') or 0, extra['boards'])
    if extra.get('board_label') and not base.get('board_label'):
        base['board_label'] = extra['board_label']
    first = _earliest(base.get('first_time'), extra.get('first_time'))
    if first:
        base['first_time'] = first
    if extra.get('last_time') and not base.get('last_time'):
        base['last_time'] = extra['last_time']
    for key in ('open_num', 'seal_fund', 'change', 'reason', 'reason_full'):
        if extra.get(key) not in (None, '') and base.get(key) in (None, ''):
            base[key] = extra[key]
    if extra.get('tags'):
        base['tags'] = sorted(set(base.get('tags') or []) | set(extra['tags']))
    if source not in base['sources']:
        base['sources'].append(source)


def _theme_match(name: str, candidates: dict) -> str | None:
    """韭研题材名与开盘啦题材名模糊匹配（互相包含即视为同一题材）。"""
    if not name:
        return None
    if name in candidates:
        return name
    hits = [c for c in candidates if name in c or c in name]
    return max(hits, key=len) if hits else None


def collect(store, day: str, *, em=None, ths=None, kpl=None, jygs=None, today: str | None = None) -> dict:
    """采集并聚合某日涨停汇总，写库后返回视图。单源失败降级并在 meta.sources 标注。"""
    em = em or EastmoneySource()
    ths = ths or ThsSource()
    kpl = kpl or KaipanlaSource()
    jygs = jygs if jygs is not None else JiuyangongsheSource()
    today = today or datetime.now(TZ).date().isoformat()

    status: dict[str, str] = {}
    stocks: dict[str, dict] = {}

    # 1) 东财涨停池：股票全集基准
    try:
        for r in em.fetch_limit_pool(day):
            stocks[r['code']] = {**r, 'tags': [], 'sources': ['eastmoney']}
        status['eastmoney'] = f'ok:{len(stocks)}'
    except SourceError as e:
        status['eastmoney'] = f'fail:{e}'

    # 2) 同花顺涨停揭秘：原因标签与板型
    try:
        rows = ths.fetch_limit_pool(day)
        for r in rows:
            if r['code'] in stocks:
                _merge_stock(stocks[r['code']], {
                    'boards': parse_boards(r.get('high_days')),
                    'board_label': r.get('high_days'),
                    'first_time': r.get('first_time'), 'last_time': r.get('last_time'),
                    'open_num': r.get('open_num'), 'seal_fund': r.get('order_amount'),
                    'change': r.get('change'), 'reason': r.get('reason')}, 'ths')
            else:
                stocks[r['code']] = {**r, 'boards': parse_boards(r.get('high_days')),
                                     'board_label': r.get('high_days'), 'seal_fund': r.get('order_amount'),
                                     'tags': [], 'sources': ['ths']}
        status['ths'] = f'ok:{len(rows)}'
    except SourceError as e:
        status['ths'] = f'fail:{e}'

    # 3) 开盘啦：题材分组（当日用复盘含原因全文，历史用梯队）
    themes: dict[str, dict] = {}
    membership: dict[str, str] = {}   # code -> theme_code
    stats: dict = {}
    try:
        if day >= today:
            resumed = kpl.fetch_resumption(day)
            stats = {k: v for k, v in resumed['stats'].items() if v is not None}
            raw_themes = resumed['themes']
        else:
            raw_themes = kpl.fetch_sector_ladder(day)
        for t in raw_themes:
            if not t.get('code') or not t.get('name'):
                continue
            theme = themes.setdefault(t['code'], {'code': t['code'], 'name': t['name'],
                                                  'logic': t.get('explain'), 'stocks': []})
            for s in t.get('stocks') or []:
                code, name = s.get('code'), s.get('name')
                if not code or not name:
                    continue
                membership[code] = t['code']
                boards = s.get('boards') if s.get('boards') is not None else parse_boards(s.get('board_label'))
                if code in stocks:
                    _merge_stock(stocks[code], {
                        'boards': boards, 'board_label': s.get('board_label'),
                        'first_time': s.get('first_time'), 'seal_fund': s.get('seal_fund'),
                        'change': s.get('change'), 'reason_full': s.get('reason_full'),
                        'tags': s.get('tags')}, 'kaipanla')
                else:
                    stocks[code] = {'code': code, 'name': name, 'boards': boards,
                                    'board_label': s.get('board_label'), 'first_time': s.get('first_time'),
                                    'last_time': None, 'open_num': None, 'seal_fund': s.get('seal_fund'),
                                    'change': s.get('change'), 'reason': None,
                                    'reason_full': s.get('reason_full'), 'tags': s.get('tags') or [],
                                    'sources': ['kaipanla']}
                theme['stocks'].append(code)
        status['kaipanla'] = f'ok:{len(themes)}题材'
    except SourceError as e:
        status['kaipanla'] = f'fail:{e}'

    if not stocks:
        raise SourceError(f'{day} 全部渠道均无涨停数据（{status}）')

    # 4) 开盘啦题材强度：强度榜仅保留最近约 2 个交易日（取 4 个自然日窗口），更早逐板块回补
    strengths: dict[str, dict] = {}
    try:
        recent = day >= (datetime.now(TZ).date() - timedelta(days=4)).isoformat()
        if recent:
            for r in kpl.fetch_strength_board(day):
                strengths[r['code']] = r
            status['kpl_strength'] = f'ok:{len(strengths)}'
            if not strengths:
                raise SourceError('强度榜无数据，走逐板块回补')
        else:
            raise SourceError('历史日走逐板块回补')
    except SourceError:
        got = 0
        for code in [c for c in themes if not c.startswith('tag:')]:
            try:
                row = kpl.fetch_sector_strength(code, day)
                if row:
                    strengths[code] = row
                    got += 1
            except SourceError:
                continue
        status['kpl_strength'] = f'ok:{got}/{len(themes)}板块' if got else 'fail:无强度数据'

    # 5) 韭研公社：题材逻辑 + 个股原因补充（可缺席）
    jygs_logic: dict[str, str] = {}
    if jygs is not None and jygs.available():
        try:
            fields = jygs.fetch_action_fields(day)
            kpl_names = {t['name']: t['code'] for t in themes.values()}
            for f in fields:
                matched = _theme_match(f['name'], kpl_names)
                if matched and f.get('reason'):
                    jygs_logic[kpl_names[matched]] = f['reason']
                if not f.get('id'):
                    continue
                for s in jygs.fetch_action_list(f['id']):
                    if s['code'] in stocks:
                        _merge_stock(stocks[s['code']], {
                            'boards': parse_boards(s.get('board_label')),
                            'board_label': s.get('board_label'),
                            'first_time': s.get('time'),
                            'reason_full': s.get('reason')}, 'jiuyangongshe')
            status['jiuyangongshe'] = f'ok:{len(fields)}题材'
        except SourceError as e:
            status['jiuyangongshe'] = f'fail:{e}'
    else:
        status['jiuyangongshe'] = 'skip:未配置SESSION'

    # 6) 未分组股票按同花顺原因首标签归并
    for code, s in stocks.items():
        if code in membership:
            continue
        tag = (s.get('reason') or '').split('+')[0].split('、')[0].strip()
        tcode = 'tag:' + tag if tag else 'tag:其他'
        membership[code] = tcode
        theme = themes.setdefault(tcode, {'code': tcode, 'name': tag or '其他',
                                          'logic': None, 'stocks': []})
        theme['stocks'].append(code)

    # 7) 落库：同日重采覆盖
    collected = now_iso()
    with store.connect() as db:
        _ensure_tables(db)
        db.execute('DELETE FROM limitup_meta WHERE trade_date=?', (day,))
        db.execute('DELETE FROM limitup_theme WHERE trade_date=?', (day,))
        db.execute('DELETE FROM limitup_stock WHERE trade_date=?', (day,))
        for t in themes.values():
            st = strengths.get(t['code']) or {}
            db.execute(
                'INSERT INTO limitup_theme VALUES(?,?,?,?,?,?,?,?)',
                (day, t['code'], t['name'], len(t['stocks']),
                 st.get('strength'), st.get('rank'), st.get('change'),
                 t.get('logic') or jygs_logic.get(t['code'])))
        for code, s in stocks.items():
            tcode = membership[code]
            db.execute(
                'INSERT INTO limitup_stock VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (day, code, s['name'], s.get('boards'), s.get('board_label'),
                 s.get('first_time'), s.get('last_time'), s.get('open_num'), s.get('seal_fund'),
                 s.get('change'), s.get('reason'), s.get('reason_full'),
                 json.dumps(s.get('tags') or [], ensure_ascii=False),
                 tcode, themes[tcode]['name'], json.dumps(s['sources'], ensure_ascii=False)))
        stats.setdefault('limit_up', len(stocks))
        db.execute('INSERT INTO limitup_meta VALUES(?,?,?,?)',
                   (day, json.dumps(stats, ensure_ascii=False), json.dumps(status, ensure_ascii=False), collected))
    store.event(JOB, all(v.startswith(('ok', 'skip')) for v in status.values()),
                f'{day} 涨停 {len(stocks)} 只 · ' + '；'.join(f'{k}:{v}' for k, v in status.items()))
    return view(store, day)


def view(store, day: str) -> dict:
    """读取某日涨停汇总视图；无数据返回 None。"""
    with store.connect() as db:
        _ensure_tables(db)
        meta = db.execute('SELECT * FROM limitup_meta WHERE trade_date=?', (day,)).fetchone()
        if not meta:
            return None
        themes = [dict(r) for r in db.execute(
            'SELECT * FROM limitup_theme WHERE trade_date=? ORDER BY strength DESC, stock_count DESC', (day,))]
        stocks = [dict(r) for r in db.execute(
            'SELECT * FROM limitup_stock WHERE trade_date=?', (day,))]
    by_theme: dict[str, list] = {}
    for s in stocks:
        s['tags'] = json.loads(s['tags'])
        s['sources'] = json.loads(s['sources'])
        by_theme.setdefault(s['theme_code'], []).append(s)
    for t in themes:
        members = by_theme.get(t['theme_code'], [])
        members.sort(key=lambda s: (-(s['boards'] or 1), s['first_time'] or '99:99:99', s['code']))
        t['stocks'] = members
    return {'date': day, 'stats': json.loads(meta['stats']), 'sources': json.loads(meta['sources']),
            'collected_at': meta['collected_at'], 'themes': themes}


def dates(store) -> list[str]:
    with store.connect() as db:
        _ensure_tables(db)
        return [r[0] for r in db.execute('SELECT trade_date FROM limitup_meta ORDER BY trade_date DESC')]


def _scheduler_loop(store):
    """每日 17:00-20:00（北京时间）采集当日涨停汇总；失败每 10 分钟重试。"""
    while True:
        try:
            now = datetime.now(TZ)
            day = now.date().isoformat()
            calendar = store.observed('calendar') or []
            if day in calendar and '17:00' <= now.strftime('%H:%M') <= '20:00':
                with store.connect() as db:
                    _ensure_tables(db)
                    meta = db.execute('SELECT sources, collected_at FROM limitup_meta WHERE trade_date=?', (day,)).fetchone()
                dirty = True
                if meta:
                    src = json.loads(meta['sources'])
                    core_ok = all(str(src.get(k, '')).startswith('ok') for k in ('eastmoney', 'ths', 'kaipanla'))
                    dirty = not core_ok and (now - datetime.fromisoformat(meta['collected_at'])).total_seconds() >= 600
                if dirty and _COLLECT_LOCK.acquire(blocking=False):
                    try:
                        collect(store, day, today=day)
                    except Exception as e:  # 调度线程不允许崩
                        store.event(JOB, False, f'{day} 采集失败: {e}')
                    finally:
                        _COLLECT_LOCK.release()
        except Exception:
            pass
        time.sleep(60)


def start_scheduler(store):
    global _scheduler_started
    if _scheduler_started:
        return
    _scheduler_started = True
    threading.Thread(target=_scheduler_loop, args=(store,), daemon=True, name='limitup-scheduler').start()


def _check_date(day: str) -> str:
    """校验 YYYY-MM-DD 且为真实日期。"""
    try:
        return datetime.strptime(day, '%Y-%m-%d').date().isoformat()
    except ValueError:
        raise HTTPException(422, '日期格式应为 YYYY-MM-DD')


def router(store):
    with store.connect() as db:
        _ensure_tables(db)
    api = APIRouter()

    @api.get('/api/limitup/dates')
    def list_dates():
        return {'dates': dates(store)}

    @api.get('/api/limitup/daily')
    def daily(date: str | None = None):
        day = date or (dates(store) or [datetime.now(TZ).date().isoformat()])[0]
        day = _check_date(day)
        result = view(store, day)
        if result is None and day >= datetime.now(TZ).date().isoformat():
            try:
                with _COLLECT_LOCK:
                    result = collect(store, day, today=day)
            except SourceError as e:
                raise HTTPException(503, f'当日采集失败：{e}')
        if result is None:
            raise HTTPException(404, '该日期没有涨停汇总数据（历史回补请调用 /api/limitup/collect）')
        return result

    @api.post('/api/limitup/collect')
    def force_collect(date: str = Query(...)):
        date = _check_date(date)
        if date > datetime.now(TZ).date().isoformat():
            raise HTTPException(422, '不能采集未来日期')
        try:
            with _COLLECT_LOCK:
                return collect(store, date)
        except SourceError as e:
            raise HTTPException(503, str(e))

    return api
