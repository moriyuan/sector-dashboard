"""Exchange-summary financing balances in CNY; weekly observations are stocks."""
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
import json
import subprocess
from urllib.parse import urlencode

from .store import TZ

KEY = 'financing:series'
SOURCE = '东方财富 · 沪深交易所融资汇总'
SOURCE_URL = 'https://data.eastmoney.com/rzrq/total.html'
API = 'https://datacenter-web.eastmoney.com/api/data/v1/get'
RULE = 'financing-weekly-v1'
MARKETS = {'sh': '007', 'sz': '001'}
SCOPE = '沪市＋深市，排除京市；交易所汇总证券口径，含基金及已调出标的的未偿余额；不含融券余额'
PUBLICATION = '融资余额通常于下一交易日披露，非实时行情；来源未提供准确发布时间。'


def get(params):
    result = subprocess.run(['curl','--fail','--silent','--show-error','--compressed',
                             '--connect-timeout','5','--max-time','15','-A','Mozilla/5.0',
                             '-e',SOURCE_URL,API+'?'+urlencode(params)], capture_output=True, timeout=18)
    if result.returncode:
        raise ValueError(f'融资余额公开接口请求失败 ({result.returncode})')
    return json.loads(result.stdout)


def money(value):
    if value is None or isinstance(value, bool):
        raise ValueError('融资余额字段缺失')
    try:
        amount = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError('融资余额非数值') from exc
    if not amount.is_finite() or amount < 0:
        raise ValueError('融资余额无效')
    return int(amount) if amount == amount.to_integral_value() else float(amount)


def fetch_market(exchange, start, end):
    code = MARKETS[exchange]
    values, raw = {}, []
    count, pages = None, None
    for page in range(1, 11):
        params = dict(reportName='RPTA_WEB_RZRQ_LSSH',columns='DIM_DATE,SCDM,RZYE,RQYE,RZRQYE',
                      sortColumns='DIM_DATE',sortTypes=-1,pageSize=500,pageNumber=page,
                      filter=f"(SCDM={code})(DIM_DATE>='{start}')(DIM_DATE<='{end}')")
        obj = get(params)
        result = obj.get('result')
        if obj.get('success') is not True or not isinstance(result, dict):
            raise ValueError('融资余额来源未返回成功数据')
        current_count, current_pages = int(result['count']), int(result['pages'])
        if count is None:
            count, pages = current_count, current_pages
        if (current_count, current_pages) != (count, pages) or not 1 <= pages <= 10 or count <= 0:
            raise ValueError('融资余额分页总数变化或无可用数据')
        if not isinstance(result['data'], list) or not result['data']:
            raise ValueError('融资余额缺失分页')
        for row in result['data']:
            if str(row.get('SCDM')) != code:
                raise ValueError('融资余额市场代码不符')
            day = date.fromisoformat(str(row['DIM_DATE'])[:10]).isoformat()
            if not start <= day <= end:
                raise ValueError('融资余额日期超出请求区间')
            if day in values:
                raise ValueError('融资余额日期重复，拒绝不完整分页')
            values[day] = money(row.get('RZYE'))
        raw.append(dict(params=params,response=obj))
        if page == pages:
            if len(values) != count:
                raise ValueError('融资余额分页条数不全')
            return values, raw
    raise ValueError('融资余额超过分页上限')


def collect(store, now=None):
    now = now or datetime.now(TZ)
    today = now.astimezone(TZ).date()
    start, end = (today-timedelta(days=760)).isoformat(), today.isoformat()
    sh, sh_raw = fetch_market('sh', start, end)
    sz, sz_raw = fetch_market('sz', start, end)
    rows = []
    for day in sorted(sh.keys() | sz.keys()):
        left, right = sh.get(day), sz.get(day)
        rows.append(dict(date=day, sh_balance=left, sz_balance=right,
                         balance=left+right if left is not None and right is not None else None))
    complete = [r['date'] for r in rows if r['balance'] is not None]
    if not complete:
        raise ValueError('沪深没有同日融资余额，不发布单市场替代值')
    raw_revision = store.observe('financing:raw',dict(sh=sh_raw,sz=sz_raw))
    # Observation IDs and hashes preserve all source revisions without rewriting history.
    revision = store.observe(KEY,dict(rows=rows,latest_date=max(complete),rule=RULE,
                                    raw_revision=raw_revision,history_backfilled=True))
    return dict(revision=revision,latest_date=max(complete),rows=len(rows),complete_days=len(complete))


def weekly(rows, calendar, asof, count=104):
    if not calendar:
        return []
    end = date.fromisoformat(asof)
    monday = end-timedelta(days=end.weekday())
    # Include one extra week for an honest week-on-week change at the left edge.
    first = monday-timedelta(weeks=count)
    values = {r['date']: r for r in rows if r['date'] <= asof}
    sessions = sorted(set(calendar))
    result = []
    for offset in range(count+1):
        begin = first+timedelta(weeks=offset)
        finish = begin+timedelta(days=4)
        start_day, last_day = begin.isoformat(), finish.isoformat()
        days = [d for d in sessions if start_day <= d <= last_day]
        provisional = last_day > asof
        published = [d for d in days if d <= asof and d in values and values[d].get('balance') is not None]
        # An ended week requires its actual final trading day, never the last
        # available Thursday when Friday is missing. Holidays use the calendar.
        chosen = max(published) if provisional and published else days[-1] if days else None
        row = values.get(chosen, {}) if chosen else {}
        balance = row.get('balance')
        previous = result[-1]['balance'] if result else None
        change = balance-previous if balance is not None and previous is not None else None
        result.append(dict(week_end=last_day,date=chosen if balance is not None else None,
                           balance=balance,sh_balance=row.get('sh_balance'),sz_balance=row.get('sz_balance'),
                           change=change,change_pct=change/previous*100 if change is not None and previous else None,
                           provisional=provisional,missing=balance is None,backfilled=False))
    return result[-count:]


def day_view(store, day, mode='close', cutoff=None):
    cutoff = cutoff or day+('T12:00:00+08:00' if mode == 'midday' else 'T23:59:59+08:00')
    with store.connect() as db:
        row = db.execute('SELECT id FROM observations WHERE key=? AND observed_at<=? ORDER BY id DESC LIMIT 1',
                         (KEY,cutoff)).fetchone()
        if row is None:
            # No contemporaneous archive: expose the FIRST later backfill with
            # an explicit label, rather than silently mixing later corrections.
            row = db.execute('SELECT id FROM observations WHERE key=? ORDER BY id LIMIT 1',(KEY,)).fetchone()
    record = store.observation_record(revision=row['id']) if row else None
    result = dict(status='missing',source=SOURCE,source_url=SOURCE_URL,unit='CNY',scope=SCOPE,
                  source_time=None,latest_date=None,collected_at=None,revision=None,backfilled=False,
                  publication_note=PUBLICATION,rule_version=RULE,weeks=[])
    if not record:
        return result
    payload = record['payload']
    backfilled = record['observed_at'] > cutoff or payload.get('history_backfilled',False)
    calendar = sorted(set(store.observed('calendar') or []))
    # Even an explicitly labelled backfill cannot place today's end-of-day
    # loan balance in the noon view. A missing historical source stays unknown.
    rows = [r for r in payload['rows'] if r['date'] < day or (mode == 'close' and r['date'] == day)]
    weeks = weekly(rows,calendar,day)
    for point in weeks:
        point['backfilled'] = backfilled
    # Freshness describes the disclosed daily source, not the weekly sample:
    # Friday's normal T+1 feed can be current through Thursday while its final
    # weekly point remains pending until Monday.
    available = [r['date'] for r in rows if r.get('balance') is not None]
    latest = max(available) if available else None
    prior = [d for d, next_day in zip(calendar,calendar[1:]) if next_day <= day]
    expected = max(prior,default=None)
    status = 'missing' if latest is None else 'stale' if expected and latest < expected else 'available'
    result.update(status=status,latest_date=latest,collected_at=record['observed_at'],revision=record['id'],
                  backfilled=backfilled,weeks=weeks)
    return result


def due_session(now, calendar):
    today = now.astimezone(TZ).date().isoformat()
    if not calendar or today not in calendar:
        return None
    clock = now.astimezone(TZ).strftime('%H:%M')
    for threshold in ('16:30','12:00','09:15'):
        if clock >= threshold:
            return today, threshold
    return None


def run_scheduler(store, stop):
    while not stop.is_set():
        now = datetime.now(TZ)
        try:
            calendar = sorted(store.observed('calendar') or [])
            session = due_session(now,calendar)
            if session:
                day, batch = session
                key = 'financing:check:'+day+':'+batch
                state = store.observed(key) or {}
                if not state.get('ok') and now.timestamp() >= state.get('retry_at',0):
                    try:
                        result = collect(store,now)
                        previous = max((d for d in calendar if d < day),default=None)
                        ok = previous is not None and result['latest_date'] >= previous
                        store.observe(key,dict(ok=ok,retry_at=now.timestamp()+300,failures=0))
                        store.event('融资余额',ok,f"沪深 {result['latest_date']} · {result['complete_days']}/{result['rows']} 日齐备")
                    except Exception:
                        failures = state.get('failures',0)+1
                        store.observe(key,dict(ok=False,failures=failures,
                                               retry_at=now.timestamp()+min(3600,300*2**min(failures-1,4))))
                        raise
        except Exception as exc:
            store.event('融资余额',False,'采集异常 · '+str(exc)[:180])
        stop.wait(60)
