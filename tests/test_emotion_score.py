"""冰点分/高潮分：分档口径、倒挂修正、池/兜底切换、pool/kline 双模式、端点。"""
import json

from fastapi.testclient import TestClient

from backend.app import create_app
from backend.emotion_score import history
from backend.leaders import list_pools
from backend.store import Store

D0, D1, D2, D3 = '2026-11-02', '2026-11-03', '2026-11-04', '2026-11-05'


def stk(code, change, touch=None, name='测试股', amount=1e6):
    """prev_close=100；touch=盘中最高涨幅（默认=max(change,0)）。"""
    prev = 100.0
    price = round(prev * (1 + change / 100), 2)
    high = round(prev * (1 + (touch if touch is not None else max(change, 0)) / 100), 2)
    return dict(code=code, name=name, change=change, price=price, high=high, amount=amount)


def save_day(store, day, up, down, rows):
    store.save('market', day, 'close', {'stats': {'up': up, 'down': down, 'flat': 0}}, f'{day}T15:00:00+08:00')
    store.save('stocks', day, 'close', {'rows': rows}, f'{day}T15:00:00+08:00')


def subs(row, side):
    return {d['key']: d for d in json.loads(row['subs'])[side]}


def full_rows(store):
    """history() 返回精简行；完整行（含 subs）从库表读。"""
    history(store, limit=10)
    with store.connect() as db:
        return {r['trade_date']: dict(r) for r in db.execute("SELECT * FROM emotion_scores").fetchall()}


def seed_kline(store):
    """D2 为考核日：纯 K 线模式（无 pools 快照）。"""
    trio = [stk('600101', 10, 10), stk('600102', 10, 10), stk('600103', 10, 10)]
    save_day(store, D0, 2000, 2000, trio + [stk('600900', 3)])
    save_day(store, D1, 2000, 2000, trio + [stk('600900', -2)])
    rows = [stk(f'6000{i:02d}', -10, -5) for i in range(10, 22)]          # 12 只跌停
    rows += [stk(f'6010{i:02d}', -7, 5) for i in range(10, 25)]           # 15 只大面(11.4%)
    rows += [stk('600101', -6, -3), stk('600102', -10, -5), stk('600103', -6, -3)]  # 昨日二连板龙头今日大跌
    rows += [stk('600900', 1), stk('830001', -30, -5, '北交票'), stk('600005', -10, -5, 'ST垃圾')]
    save_day(store, D2, 300, 3100, rows)


def test_ice_dims_kline(tmp_path):
    store = Store(tmp_path / 't.db')
    seed_kline(store)
    rows = full_rows(store)
    row = rows[D2]
    assert row['source'] == 'kline'
    ice = subs(row, 'ice')
    assert ice['breadth']['score'] == 0.5                        # 3000~3599 → 0.5
    assert ice['limit_down']['score'] == 2                       # 13 跌停 <15 → 0，跌停>涨停 +2
    assert 'seal_rate' not in ice                                # 样本(0 涨停+0 炸板)<10 → 置空
    assert 'ld_surge' not in ice                                 # 前 2 日跌停 0，样本 <3 天 → 缺失
    assert ice['leaders']['score'] == 2                          # 均跌-7.33→lerp 2.0，任一跌停 → 2 取大
    assert ice['leaders']['note'] == 'kline_streak'              # 池空 → 昨日连板兜底
    assert ice['drawdown']['score'] == 1.5                       # 15 只大面 → lerp(15)=1.5
    assert ice['st_yday']['score'] == 2                          # 昨连板 3 只今日均跌 -7.33 → 封顶 2
    assert ice['st_yday']['count'] == 3
    assert 'lu_yday' not in ice                                  # 昨日涨停仅 3 只 <5 → 不记分
    assert row['ice'] == 8
    clx = subs(row, 'climax')
    assert clx['breadth']['score'] == 0 and clx['limit_up']['score'] == 0
    assert clx['max_boards']['input'] == 0                       # D2 无涨停 → 连板重建为 0
    assert clx['promote']['input'] == 0.0                        # 昨日 3 只连板今日无一涨停
    assert clx['st_yday']['score'] == 0                          # 均跌 -7.33 → 高潮侧 0 分
    assert row['climax'] == 0
    # ST/北交所不得计入：跌停恰为 13（12 只跌停股 + 龙头 600102；ST 与北交所被过滤）
    assert ice['limit_down']['input'] == 13
    assert ice['drawdown']['input'] == 15


def test_climax_dims_pool_mode(tmp_path):
    store = Store(tmp_path / 't.db')
    seed_kline(store)
    # 情绪核心池三只（近端评分用池内股票；≥3 只才记龙头分）
    list_pools(store)  # 建表
    with store.connect() as db:
        db.executemany("INSERT INTO leader_stocks(pool, code, name, added_at) VALUES('emotion',?,?,?)",
                       [('600201', '池股甲', '2026-11-05T09:00:00+08:00'),
                        ('600202', '池股乙', '2026-11-05T09:00:00+08:00'),
                        ('600203', '池股丙', '2026-11-05T09:00:00+08:00')])
        db.commit()
    save_day(store, D3, 4600, 200, [stk('600201', 6, 6), stk('600202', 9, 9), stk('600203', 4, 4)])
    store.save('pools', D3, 'close', {'pools': {'up': [], 'broken': [], 'down': []},
                                      'stats': {'limit_up': 80, 'limit_down': 5, 'broken': 5,
                                                'broken_ratio': 5.9, 'max_boards': 9}},
               f'{D3}T15:00:00+08:00')
    rows = full_rows(store)
    row = rows[D3]
    assert row['source'] == 'pool'
    clx = subs(row, 'climax')
    assert clx['breadth']['score'] == 3                          # lerp(4600)∈[3800..5000] → 3.0
    assert clx['limit_up']['score'] == 1.5                       # lerp(80)∈[70,85] → 1.67 → 1.5
    assert clx['seal_rate']['score'] == 2                        # 80/85=94.1 → 钳制 2
    assert clx['leaders']['score'] == 2                          # 池内均涨 6.33 → 钳制 2
    assert clx['leaders']['note'] == 'emotion_pool'
    assert clx['max_boards']['score'] == 4                       # 9 板 → 9-5（前序快照 <10 天，无异动加成）
    assert clx['lu_surge']['score'] == 2                         # 80 vs 前3日均 2 → 40 倍 → 钳制 2
    assert 'promote' not in clx                                  # D2 无涨停 → 昨日无连板 → 缺失
    assert row['climax'] == 14.5
    ice = subs(row, 'ice')
    assert ice['limit_down']['score'] == 0                       # 5 跌停 <15
    assert ice['seal_rate']['score'] == 0                        # 94.1 ≥ 66
    assert ice['leaders']['score'] == 0
    assert row['ice'] == 0


def test_fallback_streak_leaders(tmp_path):
    """池空时：昨日 K 线连板股（streak>=2）作为龙头。"""
    store = Store(tmp_path / 't.db')
    trio = [stk('600301', 10, 10), stk('600302', 10, 10), stk('600303', 10, 10)]
    save_day(store, D0, 2000, 2000, trio)
    save_day(store, D1, 2000, 2000, trio)
    save_day(store, D2, 4200, 500, [stk('600301', 6, 6), stk('600302', 5, 5), stk('600303', 4, 4)])
    rows = full_rows(store)
    clx = subs(rows[D2], 'climax')
    assert clx['leaders']['note'] == 'kline_streak'
    assert clx['leaders']['score'] == 1.5                        # 均涨 5 → lerp(5)∈[3,6] → 1.5
    assert clx['promote']['input'] == 0.0                        # 3 只昨日连板今日均未封板
    # D0 无昨日连板 → 龙头维度缺失
    assert 'leaders' not in subs(rows[D0], 'climax')
    assert 'promote' not in subs(rows[D0], 'climax')


def test_emotion_score_endpoint_and_cache(tmp_path):
    store = Store(tmp_path / 't.db')
    seed_kline(store)
    with TestClient(create_app(store, collect=False)) as c:
        data = c.get('/api/emotion-score').json()
        dates = [r['date'] for r in data['rows']]
        assert dates == sorted(dates) and dates[-1] == D2
        r = data['rows'][-1]
        assert r['ice'] == 8 and r['climax'] == 0 and r['source'] == 'kline'
        subs = r['subs']                                          # 维度明细随曲线输出
        assert [d['key'] for d in subs['ice']] and [d['key'] for d in subs['climax']]
        assert all('name' in d and 'score' in d for d in subs['ice'] + subs['climax'])
        again = c.get('/api/emotion-score').json()               # 缓存一致
        assert again == data
        # 市场快照更新（revision 变化）→ 重算
        store.save('market', D2, 'close', {'stats': {'up': 300, 'down': 4100, 'flat': 0}},
                   f'{D2}T15:30:00+08:00')
        fresh = c.get('/api/emotion-score').json()
        assert fresh['rows'][-1]['ice'] == 9.5                   # 广度2 + 跌停2 + 龙头2 + 大面1.5 + 昨连板今表现2（重算生效）


def test_emotion_score_empty(tmp_path):
    store = Store(tmp_path / 't.db')
    assert history(store, limit=10) == []
    with TestClient(create_app(store, collect=False)) as c:
        assert c.get('/api/emotion-score').json() == {'rows': []}


def test_yday_perf_dims(tmp_path):
    """昨涨停/昨连板今表现：正反馈记高潮、负反馈记冰点、样本门槛。"""
    # 正向：D0 三只涨停（建立 streak），D1 八只涨停，D2 全体上涨
    store = Store(tmp_path / 'a.db')
    ups = [stk(f'6001{i:02d}', 10, 10) for i in range(10, 18)]     # 8 只涨停
    save_day(store, D0, 2000, 2000, ups[:3])
    save_day(store, D1, 2000, 2000, ups)
    rows = [stk(f'6001{i:02d}', 4.5, 5) for i in range(10, 13)]    # 3 只连板股 +4.5
    rows += [stk(f'6001{i:02d}', 3.5, 4) for i in range(13, 18)]   # 其余 5 只 +3.5
    save_day(store, D2, 2000, 2000, rows)
    clx = subs(full_rows(store)[D2], 'climax')
    assert clx['lu_yday']['score'] == 2                            # 均值 3.875 → lerp 1.94 → 2.0
    assert clx['lu_yday']['count'] == 8
    assert clx['st_yday']['score'] == 2                            # 连板股均值 +4.5 → lerp 1.75，0.5 取整到 2
    ice = subs(full_rows(store)[D2], 'ice')
    assert ice['lu_yday']['score'] == 0 and ice['st_yday']['score'] == 0  # 上涨日记 0 分

    # 负向：同样的昨日涨停/连板，次日全跌 -3%
    store = Store(tmp_path / 'b.db')
    save_day(store, D0, 2000, 2000, ups[:3])
    save_day(store, D1, 2000, 2000, ups)
    save_day(store, D2, 2000, 2000, [stk(f'6001{i:02d}', -3) for i in range(10, 18)])
    ice = subs(full_rows(store)[D2], 'ice')
    assert ice['lu_yday']['score'] == 2                            # 均值 -3 ≤ -2.5 → 封顶 2
    assert ice['st_yday']['score'] == 2                            # 均值 -3 → lerp(3,[(0,1),(1.5,1.5),(3,2)])=2
    clx = subs(full_rows(store)[D2], 'climax')
    assert clx['lu_yday']['score'] == 0 and clx['st_yday']['score'] == 0


def test_yday_perf_anchors():
    """昨涨停/昨连板今表现的完整阶梯（用户拍板 2026-11-10）。"""
    from backend.emotion_score import _climax_dims, _ice_dims

    def clx(lu_avg, st_avg):
        return {d['key']: d['score'] for d in _climax_dims(
            None, None, None, '', 0,
            yday={'lu': (lu_avg, 8), 'streak': (st_avg, 4)})}

    assert clx(2, 3)['lu_yday'] == 0 and clx(2, 3)['st_yday'] == 0      # 恰在门槛 → 0
    assert clx(2.2, 3.2)['lu_yday'] == 1 and clx(2.2, 3.2)['st_yday'] == 1
    assert clx(2.6, 3.6)['lu_yday'] == 1.5                              # 2.6→1.3→取整1.5
    assert clx(3.5, 4.5)['lu_yday'] == 2 and clx(3.5, 4.5)['st_yday'] == 2
    assert clx(9, 9)['lu_yday'] == 2 and clx(9, 9)['st_yday'] == 2      # 右端钳制

    def ice(lu_avg, st_avg):
        return {d['key']: d['score'] for d in _ice_dims(
            None, None, None, '', yday={'lu': (lu_avg, 8), 'streak': (st_avg, 4)})}

    assert ice(0, 0)['lu_yday'] == 0 and ice(0, 0)['st_yday'] == 0      # ≥0 → 0
    assert ice(-0.2, -0.2)['lu_yday'] == 1                              # <0% 即 1 分
    assert ice(-1.75, -2)['lu_yday'] == 1.5 and ice(-1.75, -2)['st_yday'] == 1.5
    assert ice(-3, -3)['lu_yday'] == 2 and ice(-3, -3)['st_yday'] == 2


def test_lerp():
    from backend.emotion_score import _lerp
    a = [(4000, 1), (4334, 2), (4667, 3), (5000, 4)]
    assert _lerp(4000, a) == 1 and _lerp(4334, a) == 2 and _lerp(5000, a) == 4
    assert _lerp(9999, a) == 4                                   # 右端钳制
    assert _lerp(4167, a) == 1.5                                 # 段内中点 → 线性
    assert _lerp(4100, a) == 1.5                                 # 1.299 → 0.5 取整 → 1.5
    assert _lerp(4300, a) == 2                                   # 1.898 → 2.0
    desc = [(55, 2), (62.5, 1), (70, 0)]                         # 降序得分（封板率-冰）
    assert _lerp(70, desc) == 0 and _lerp(55, desc) == 2
    assert _lerp(60, desc) == 1.5
    assert _lerp(90, desc) == 0                                  # 降序右端钳制


def test_surge():
    from backend.emotion_score import _surge
    ratio, base = _surge(8, [1, 2, 1])
    assert base == 4 / 3 and round(ratio, 2) == 6.0
    assert _surge(5, [0, 0, 0]) is None                          # 均值 0 → 缺失
    assert _surge(5, [1, 2]) is None                             # 样本 <3 天 → 缺失
    assert _surge(5, None) is None


def test_surge_and_promote_dims(tmp_path):
    """涨停放大 2 倍 → 2 分；连板晋级 2/4=50% → 1.5 分。"""
    store = Store(tmp_path / 't.db')
    duo = [stk('600401', 10, 10), stk('600402', 10, 10)]
    save_day(store, D0, 2000, 2000, duo)                         # lu=2
    save_day(store, D1, 2000, 2000, duo)                         # lu=2，两只二连板
    save_day(store, D2, 2000, 2000, duo)                         # lu=2，两只三连板
    rows = [stk('600401', 10, 10), stk('600402', -5)]
    rows += [stk('600501', 10, 10), stk('600502', 10, 10), stk('600503', 10, 10)]
    save_day(store, D3, 3900, 100, rows)                         # lu=4，晋级 1/2
    clx = subs(full_rows(store)[D3], 'climax')
    assert clx['lu_surge']['score'] == 2                         # 4 vs 近3日均 2 → 2 倍
    assert clx['lu_surge']['base'] == 2
    assert clx['promote']['score'] == 1.5                        # 50% → lerp∈[40,60] → 1.5
    assert clx['promote']['count'] == 2
    assert 'leaders' not in clx                                  # 龙头仅 2 只 <3 → 缺失


def test_promote_sample_guard(tmp_path):
    """昨日连板仅 1 只（<2）→ 晋级率维度缺失。"""
    store = Store(tmp_path / 't.db')
    save_day(store, D0, 2000, 2000, [stk('600601', 10, 10)])
    save_day(store, D1, 2000, 2000, [stk('600601', 10, 10)])
    save_day(store, D2, 4500, 100, [stk('600601', 10, 10)])
    clx = subs(full_rows(store)[D2], 'climax')
    assert 'promote' not in clx


def test_new_stock_excluded(tmp_path):
    """无涨跌幅限制的新股（涨幅超制度阈值 1.5 倍）不计涨停、不入连板。"""
    store = Store(tmp_path / 't.db')
    save_day(store, D0, 2000, 2000, [stk('600701', 44, 44, 'N新股'), stk('600900', 2)])
    save_day(store, D1, 2000, 2000, [stk('600701', 30, 30), stk('600702', 10, 10)])
    clx = subs(full_rows(store)[D1], 'climax')
    assert clx['limit_up']['input'] == 1      # 只有 600702 算涨停，新股两日暴涨不计
    assert clx['max_boards']['input'] == 1    # 新股不入连板，最高板仅 600702 的 1 板
    assert 'leaders' not in clx               # 昨日无有效连板 → 无龙头样本


def test_no_limit_boundary(tmp_path):
    """阈值边界：正常涨停股（20cm/30cm 制度内）不被误伤。"""
    store = Store(tmp_path / 't.db')
    save_day(store, D0, 2000, 2000, [stk('300701', 20, 20), stk('830702', 30, 30, '北交A'),
                                     stk('600703', 10, 10)])
    clx = subs(full_rows(store)[D0], 'climax')
    assert clx['limit_up']['input'] == 2      # 300 与主板计入；北交所本就不在沪深范围


def test_lu_amount_bonus(tmp_path):
    """涨停成交额 ÷ 近5日均值 ≥2 倍 → +1（≥3 倍 +2）；昨日已放量、今日更高也得分。"""
    store = Store(tmp_path / 't.db')
    save_day(store, D0, 2000, 2000, [stk('600801', 10, 10, amount=1e8)])
    save_day(store, D1, 2000, 2000, [stk('600802', 10, 10, amount=1e8)])
    save_day(store, D2, 2000, 2000, [stk('600803', 10, 10, amount=2.4e8)])  # 昨日已放量
    rows_today = [stk('600804', 10, 10, amount=1e8), stk('600805', 10, 10, amount=1e8),
                  stk('600806', 10, 10, amount=1e8)]                        # 今日 3 亿，仅昨日 1.25 倍
    save_day(store, D3, 2000, 2000, rows_today)
    rows = full_rows(store)
    d0 = subs(rows[D0], 'climax')['limit_up']
    assert d0['score'] == 0 and 'bonus' not in d0           # 首日无基线
    assert d0['lu_amount'] == 1.0                           # 亿元
    d3 = subs(rows[D3], 'climax')['limit_up']
    assert d3['lu_amount'] == 3.0 and d3['amount_base'] == 1.47
    assert d3['amount_ratio'] == 2.05                       # 相对近5日均值放量 2 倍
    assert d3['bonus'] == 1 and d3['score'] == 1            # 计数 0 分 + 加成 1


def test_lu_amount_bonus_lerp():
    from backend.emotion_score import _lerp
    a = [(2, 1), (3, 2)]
    assert _lerp(1.9, a) == 1                               # 钳制到下端（调用方判 <2 不加成）
    assert _lerp(2.5, a) == 1.5
    assert _lerp(5, a) == 2                                 # 3 倍以上封顶 +2


def test_limit_up_stacked_cap6(tmp_path):
    """涨停维度：个数 100→3.0 叠加成交额加成 +2 = 5.0；合计上限 6。"""
    store = Store(tmp_path / 't.db')
    for d in (D0, D1, D2):
        save_day(store, d, 2000, 2000, [stk('600901', 10, 10, amount=1e8)])
    rows = [stk('600902', 10, 10, amount=1.5e8), stk('600903', 10, 10, amount=1.5e8),
            stk('600904', 10, 10, amount=1.5e8)]
    save_day(store, D3, 4600, 200, rows)
    store.save('pools', D3, 'close', {'pools': {'up': [], 'broken': [], 'down': []},
                                      'stats': {'limit_up': 100, 'limit_down': 5, 'broken': 5,
                                                'broken_ratio': 4.8, 'max_boards': 7}},
               f'{D3}T15:00:00+08:00')
    dim = subs(full_rows(store)[D3], 'climax')['limit_up']
    assert dim['bonus'] == 2                    # 4.5 亿 ÷ 近5日均 1 亿 = 4.5 倍 → +2
    assert dim['score'] == 5                    # 3.0 + 2 叠加，未到 6 上限


def test_hi3_dim(tmp_path):
    """高位股群：3 板及以上家数 5→1、7→2（用户拍板 2026-11-10）。"""
    store = Store(tmp_path / 'a.db')
    five = [stk(f'6008{i:02d}', 10, 10) for i in range(10, 15)]  # 5 只三连板
    save_day(store, D0, 2000, 2000, five)
    save_day(store, D1, 2000, 2000, five)
    save_day(store, D2, 3000, 500, five)
    clx = subs(full_rows(store)[D2], 'climax')
    assert clx['hi3']['input'] == 5 and clx['hi3']['score'] == 1

    store = Store(tmp_path / 'b.db')
    seven = [stk(f'6008{i:02d}', 10, 10) for i in range(10, 17)]  # 7 只三连板
    save_day(store, D0, 2000, 2000, seven)
    save_day(store, D1, 2000, 2000, seven)
    save_day(store, D2, 3000, 500, seven)
    clx = subs(full_rows(store)[D2], 'climax')
    assert clx['hi3']['input'] == 7 and clx['hi3']['score'] == 2


def test_abnormal_height_bonus(tmp_path):
    """绕异动高度：10 日涨幅 ≥100% 个股出现 → 最高板维度 +1；未触发 → 不加（用户拍板 2026-11-10）。"""
    # 触发：600111 前 12 天价格 50 元，D3 回到 100 元 → 10 日涨幅 100%
    store = Store(tmp_path / 'a.db')
    prior = [f'2026-10-{d:02d}' for d in range(18, 32)]           # 14 个前序快照日
    for d in prior:
        save_day(store, d, 2000, 2000, [stk('600111', -50)])      # price=50
    save_day(store, D0, 2000, 2000, [stk('600111', 0)])           # price=100
    clx = subs(full_rows(store)[D0], 'climax')
    mb = clx['max_boards']
    assert mb['input'] == 0                                       # 当日无涨停 → 阶梯 0 分
    assert mb['abnormal'] >= 1 and mb['bonus'] == 1
    assert mb['score'] == 1                                       # 0 + 异动加成 1

    # 未触发：D0 价格 91 元（10 日涨幅 82% <100%）→ 无加成
    store = Store(tmp_path / 'b.db')
    for d in prior:
        save_day(store, d, 2000, 2000, [stk('600111', -50)])
    save_day(store, D0, 2000, 2000, [stk('600111', -9)])          # price=91
    mb = subs(full_rows(store)[D0], 'climax')['max_boards']
    assert mb['score'] == 0 and 'bonus' not in mb

    # 前序快照不足 10 天 → 异动检测不生效（不报错、不加成）
    store = Store(tmp_path / 'c.db')
    save_day(store, D0, 2000, 2000, [stk('600111', -50)])
    save_day(store, D1, 2000, 2000, [stk('600111', 0)])
    mb = subs(full_rows(store)[D1], 'climax')['max_boards']
    assert mb['score'] == 0 and 'bonus' not in mb
