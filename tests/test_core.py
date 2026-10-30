from datetime import datetime
from zoneinfo import ZoneInfo

from backend.analytics import percentile, breadth, pool_stats, analyze
from backend.store import Store
from backend.schedule import phase


def test_percentile_ties_and_missing():
    assert percentile([1, 2, 2, None]) == [0.0, 75.0, 75.0, None]
    assert percentile([3, 3]) == [50.0, 50.0]
    assert percentile([None]) == [None]


def test_breadth_excludes_st_suspension_and_duplicates():
    rows = [dict(code='600001', name='甲', price=10, change=1, amount=100),
            dict(code='600001', name='甲', price=10, change=1, amount=100),
            dict(code='600002', name='乙', price=10, change=0, amount=200),
            dict(code='600003', name='*ST丙', price=2, change=-5, amount=20),
            dict(code='600004', name='停牌', price=3, change=0, amount=0),
            dict(code='920001', name='北交所', price=3, change=2, amount=20)]
    got = breadth(rows)
    assert got['up'] == 1 and got['flat'] == 1 and got['down'] == 0
    assert got['up_ratio'] == 50 and got['st']['down'] == 1
    assert got['amount'] == 320
    assert breadth([])['up_ratio'] is None


def test_pool_deduplicates_resealed_and_preserves_unknown():
    sealed = [{'code': '600001', 'boards': 3}]
    broken = [{'code': '600001'}, {'code': '600002'}, {'code': '600002'}]
    assert pool_stats(sealed, broken, [])['broken_ratio'] == 50
    assert pool_stats(sealed, None, [])['broken_ratio'] is None
    assert pool_stats([], [], [])['broken_ratio'] is None


def test_store_revision_and_no_future_lookup(tmp_path):
    db = Store(tmp_path / 'test.db')
    a = db.save('industry', '2026-10-28', 'close', {'rows': [{'code': 'a'}]}, '2026-10-28T15:30:00+08:00')
    db.save('industry', '2026-10-29', 'live', {'rows': [{'code': 'b'}]}, None)
    b = db.save('industry', '2026-10-28', 'close', {'rows': [{'code': 'c'}]}, '2026-10-28T16:30:00+08:00')
    assert b > a
    assert db.snapshot('industry', '2026-10-28', 'close')['payload']['rows'][0]['code'] == 'c'
    assert db.snapshot('industry', '2026-10-27', 'close') is None
    assert db.snapshot('industry', '2026-10-28', 'live') is None
    assert db.snapshot('industry', '2026-10-28', 'close', a)['payload']['rows'][0]['code'] == 'a'


def test_no_score_with_missing_inputs():
    rows = [{'code': 'a', 'change': 3, 'up_ratio': 80, 'price': 20}]
    out = analyze(rows, {}, [], '2026-10-29', 'live')
    assert out[0]['heat'] is None
    assert not out[0]['labels']


def test_schedule_lunch_holiday_and_archive():
    tz = ZoneInfo('Asia/Shanghai')
    def at(s): return datetime.fromisoformat(s).replace(tzinfo=tz)
    cal = ['2026-10-28', '2026-10-29', '2026-10-30']
    assert phase(at('2026-10-29T12:00:00'), cal) == 'lunch'
    assert phase(at('2026-10-29T15:30:00'), cal) == 'archive_1530'
    assert phase(at('2026-10-29T16:30:00'), cal) == 'archive_1630'
    assert phase(at('2026-10-31T10:00:00'), cal) == 'calendar_unknown'
    assert phase(at('2026-10-29T10:00:00'), []) == 'calendar_unknown'


def test_prior_five_does_not_include_current_session_and_missing_is_not_skipped():
    dates=[f'2026-10-{i:02d}' for i in range(1,9)]
    bars=[{'date':d,'close':100+i,'change':1} for i,d in enumerate(dates)]
    rows=[{'code':'a','change':5,'up_ratio':80,'price':999}]
    got=analyze(rows,{'a':bars},[],dates[-1],'live',dates)[0]
    assert round(got['prior_5'],8)==round((106/101-1)*100,8)
    assert round(got['return_5'],8)==round((999/102-1)*100,8)
    incomplete=[b for b in bars if b['date']!=dates[-3]]
    assert analyze(rows,{'a':incomplete},[],dates[-1],'live',dates)[0]['prior_5'] is None


def test_streak_uses_close_only_and_missing_calendar_date_breaks_evidence():
    dates=[f'2026-10-{i:02d}' for i in range(1,13)]
    histories={str(j):[{'date':d,'close':100+i+j,'change':j} for i,d in enumerate(dates)] for j in range(5)}
    rows=[{'code':str(j),'price':120,'change':j,'up_ratio':j*20} for j in range(5)]
    got={r['code']:r for r in analyze(rows,histories,[],dates[-1],'live',dates)}
    assert got['4']['streak']==11 and got['4']['top_days_10']==10
    histories['0']=[b for b in histories['0'] if b['date']!=dates[-2]]
    got={r['code']:r for r in analyze(rows,histories,[],dates[-1],'live',dates)}
    assert got['4']['streak'] is None and got['4']['top_days_10'] is None


def test_heat_missing_constituent_does_not_redistribute_weights():
    dates=[f'2026-10-{i:02d}' for i in range(1,9)]
    histories={str(j):[{'date':d,'close':100+i*(j+1),'change':j} for i,d in enumerate(dates)] for j in range(5)}
    rows=[{'code':str(j),'price':120,'change':j,'up_ratio':j*20 if j else None} for j in range(5)]
    got={r['code']:r for r in analyze(rows,histories,[],dates[-1],'live',dates)}
    assert got['0']['heat'] is None
    assert got['4']['heat']==100 and 'hot' in got['4']['labels']


def test_cold_requires_sixty_daily_denominators(tmp_path):
    from datetime import date,timedelta
    dates=[(date(2026,1,1)+timedelta(days=i)).isoformat() for i in range(65)]
    histories={str(j):[{'date':d,'close':100+i,'change':j} for i,d in enumerate(dates)] for j in range(5)}
    archived=[{'date':d,'rows':[{'code':'0','amount_share':100-i}]} for i,d in enumerate(dates[:-1])]
    rows=[{'code':str(j),'price':165,'change':j,'up_ratio':20,'amount_share':1} for j in range(5)]
    got={r['code']:r for r in analyze(rows,histories,archived,dates[-1],'close',dates)}
    assert got['0']['cold'] is True
    incomplete=[a for a in archived if a['date']!=dates[-3]]
    got={r['code']:r for r in analyze(rows,histories,incomplete,dates[-1],'close',dates)}
    assert got['0']['cold'] is False
