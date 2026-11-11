"""涨停汇总：聚合逻辑、排序口径、降级策略与各渠道解析的测试。"""
import json

import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.limitup import collect, dates, parse_boards, view
from backend.sources import SourceError
from backend.sources.base import hhmmss
from backend.sources.eastmoney import EastmoneySource
from backend.sources.jiuyangongshe import JiuyangongsheSource
from backend.sources.kaipanla import KaipanlaSource
from backend.sources.ths import ThsSource
from backend.store import Store

DAY = '2026-11-10'
HIST = '2026-09-30'


class FakeEM:
    def __init__(self, rows=None, fail=False):
        self.rows, self.fail = rows or [], fail

    def fetch_limit_pool(self, day):
        if self.fail:
            raise SourceError('em 模拟故障')
        return self.rows


class FakeTHS(FakeEM):
    pass


class FakeKPL:
    def __init__(self, themes=None, stats=None, board=None, strength=None, fail=False):
        self.themes, self.stats = themes or [], stats or {}
        self.board, self.strength = board or [], strength or {}
        self.fail = fail
        self.calls = []

    def fetch_resumption(self, day):
        if self.fail:
            raise SourceError('kpl 模拟故障')
        self.calls.append(('resumption', day))
        return {'stats': self.stats, 'themes': self.themes}

    def fetch_sector_ladder(self, day):
        if self.fail:
            raise SourceError('kpl 模拟故障')
        self.calls.append(('ladder', day))
        return self.themes

    def fetch_strength_board(self, day):
        if self.fail:
            raise SourceError('kpl 模拟故障')
        self.calls.append(('board', day))
        return self.board

    def fetch_sector_strength(self, code, day):
        if self.fail:
            raise SourceError('kpl 模拟故障')
        self.calls.append(('strength', code, day))
        return self.strength.get(code)


class FakeJYGS:
    def __init__(self, fields=None, lists=None, usable=True):
        self.fields, self.lists, self.usable = fields or [], lists or {}, usable

    def available(self):
        return self.usable

    def fetch_action_fields(self, day):
        return self.fields

    def fetch_action_list(self, field_id):
        return self.lists.get(field_id, [])


def em_row(code, name, boards, first, label=None):
    return {'code': code, 'name': name, 'boards': boards,
            'board_label': label or f'{boards}连板', 'first_time': first,
            'last_time': first, 'open_num': 0, 'seal_fund': 1e8,
            'change': 10.0, 'turnover': 5.0, 'amount': 2e8, 'industry': '通用设备'}


def ths_row(code, name, reason, high_days='首板', first='09:30:00'):
    return {'code': code, 'name': name, 'reason': reason, 'high_days': high_days,
            'first_time': first, 'last_time': first, 'open_num': 0,
            'order_amount': 5e7, 'limit_up_type': '换手板', 'seal_rate': 80.0,
            'change': 10.0, 'turnover': 5.0}


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / 'test.db')


def run_today(store, jygs=None):
    """当日口径全源采集的标准样本。"""
    em = FakeEM([
        em_row('300001', '电机甲', 1, '09:32:00'),
        em_row('300002', '丝杠乙', 3, '09:31:00', '3天3板'),
        em_row('300003', '减速丙', 3, '09:25:00', '3天3板'),
        em_row('300004', '锂电丁', 2, '10:01:00'),
        em_row('300005', '算力戊', 1, '09:40:00'),
    ])
    ths = FakeTHS([
        ths_row('300001', '电机甲', '机器人+电机'),
        ths_row('300002', '丝杠乙', '机器人+丝杠', '3天3板', '09:31:00'),
        ths_row('300003', '减速丙', '机器人+减速器', '3天3板', '09:25:00'),
        ths_row('300004', '锂电丁', '锂电池+固态电池', '2连板', '10:01:00'),
        ths_row('300005', '算力戊', '算力租赁+IDC', '首板', '09:40:00'),
    ])
    kpl = FakeKPL(
        stats={'limit_up': 5, 'limit_down': 2, 'broken_ratio': '21%', 'up': 3200, 'down': 1800},
        themes=[
            {'code': '801001', 'name': '机器人', 'explain': None, 'stocks': [
                {'code': '300001', 'name': '电机甲', 'change': 10.0, 'first_time': '09:32:00',
                 'boards': 1, 'seal_fund': 1e8, 'board_label': '首板',
                 'tags': ['机器人', '电机'], 'reason_full': '开盘啦：电机本体供应商'},
                {'code': '300002', 'name': '丝杠乙', 'change': 10.0, 'first_time': '09:31:00',
                 'boards': 3, 'seal_fund': 2e8, 'board_label': '3天3板',
                 'tags': ['机器人', '丝杠'], 'reason_full': '开盘啦：行星滚柱丝杠'},
                {'code': '300003', 'name': '减速丙', 'change': 10.0, 'first_time': '09:25:00',
                 'boards': 3, 'seal_fund': 3e8, 'board_label': '3天3板',
                 'tags': ['机器人', '减速器'], 'reason_full': '开盘啦：谐波减速器'},
            ]},
            {'code': '801002', 'name': '锂电池', 'explain': None, 'stocks': [
                {'code': '300004', 'name': '锂电丁', 'change': 10.0, 'first_time': '10:01:00',
                 'boards': 2, 'seal_fund': 1e8, 'board_label': '2连板',
                 'tags': ['锂电池'], 'reason_full': '开盘啦：固态电池'},
            ]},
        ],
        board=[
            {'code': '801001', 'name': '机器人', 'strength': 5230.0, 'change': 2.1, 'rank': 1},
            {'code': '801002', 'name': '锂电池', 'strength': 3100.0, 'change': -0.5, 'rank': 2},
        ])
    jygs = jygs if jygs is not None else FakeJYGS(
        fields=[{'id': 'f1', 'name': '机器人', 'count': 3, 'reason': '特斯拉 Optimus 量产预期'}],
        lists={'f1': [{'code': '300002', 'name': '丝杠乙', 'time': '09:31:00',
                       'board_label': '3天3板', 'reason': '韭研：丝杠核心供应商'}]})
    return collect(store, DAY, em=em, ths=ths, kpl=kpl, jygs=jygs, today=DAY)


def test_parse_boards():
    assert parse_boards('5天3板') == 3
    assert parse_boards('2连板') == 2
    assert parse_boards('7板') == 7
    assert parse_boards('首板') == 1
    assert parse_boards(None) is None
    assert parse_boards('') is None
    assert parse_boards('未知') is None


def test_hhmmss():
    assert hhmmss(92500) == '09:25:00'
    assert hhmmss(101345) == '10:13:45'
    assert hhmmss(None) is None
    assert hhmmss(999999) is None


def test_collect_groups_and_orders(store):
    result = run_today(store)
    assert result['date'] == DAY
    assert result['stats']['limit_up'] == 5 and result['stats']['limit_down'] == 2
    themes = result['themes']
    # 题材按涨停数降序：机器人(3) > 锂电池(1) = 算力租赁(1)
    assert [t['name'] for t in themes] == ['机器人', '锂电池', '算力租赁']
    robot = themes[0]
    assert robot['stock_count'] == 3
    assert robot['strength'] == 5230.0 and robot['strength_rank'] == 1
    # 题材内：连板数降序，同身位按首封时间升序
    assert [s['code'] for s in robot['stocks']] == ['300003', '300002', '300001']
    assert robot['stocks'][0]['first_time'] == '09:25:00'
    # 涨停数相同题材间按强度降序（锂电池有强度，tag 兜底组无强度排后）
    assert themes[1]['strength'] == 3100.0 and themes[2]['strength'] is None


def test_collect_merges_sources(store):
    result = run_today(store)
    robot = result['themes'][0]
    # 韭研逻辑补进题材；韭研不覆盖开盘啦原因全文（开盘啦优先）
    assert robot['logic'] == '特斯拉 Optimus 量产预期'
    sigang = next(s for s in robot['stocks'] if s['code'] == '300002')
    assert sigang['reason_full'] == '开盘啦：行星滚柱丝杠'
    assert 'jiuyangongshe' in sigang['sources']
    assert 'eastmoney' in sigang['sources'] and 'ths' in sigang['sources'] and 'kaipanla' in sigang['sources']
    assert sigang['reason'] == '机器人+丝杠'  # 同花顺标签
    assert sigang['tags'] == sorted(['机器人', '丝杠'])
    # 未分组股票按同花顺首标签兜底
    suanli = next(t for t in result['themes'] if t['name'] == '算力租赁')
    assert suanli['theme_code'] == 'tag:算力租赁' and suanli['stock_count'] == 1
    assert result['sources']['jiuyangongshe'].startswith('ok')


def test_collect_history_uses_ladder_and_sector_strength(store):
    em = FakeEM([em_row('600001', '甲', 2, '09:35:00')])
    ths = FakeTHS([ths_row('600001', '甲', '白酒+消费', '2连板', '09:35:00')])
    kpl = FakeKPL(
        themes=[{'code': '801010', 'name': '白酒', 'count': 1, 'explain': '中秋备货',
                 'stocks': [{'code': '600001', 'name': '甲', 'board_label': '2连板'}]}],
        strength={'801010': {'code': '801010', 'rank': 3, 'strength': 4100.0, 'date': HIST}})
    result = collect(store, HIST, em=em, ths=ths, kpl=kpl, jygs=FakeJYGS(usable=False), today='2026-11-11')
    assert ('ladder', HIST) in kpl.calls and not any(c[0] == 'resumption' for c in kpl.calls)
    assert ('board', HIST) not in kpl.calls                      # 历史日不走强度榜
    assert ('strength', '801010', HIST) in kpl.calls             # 逐板块回补
    theme = result['themes'][0]
    assert theme['strength'] == 4100.0 and theme['strength_rank'] == 3
    assert theme['logic'] == '中秋备货'
    assert result['sources']['jiuyangongshe'] == 'skip:未配置SESSION'


def test_collect_single_source_failure_degrades(store):
    em = FakeEM(fail=True)
    ths = FakeTHS([ths_row('300001', '电机甲', '机器人+电机')])
    kpl = FakeKPL(themes=[{'code': '801001', 'name': '机器人', 'explain': None, 'stocks': [
        {'code': '300001', 'name': '电机甲', 'boards': 1, 'board_label': '首板'}]}],
        board=[{'code': '801001', 'name': '机器人', 'strength': 100.0, 'change': 1.0, 'rank': 1}])
    result = collect(store, DAY, em=em, ths=ths, kpl=kpl, jygs=None, today=DAY)
    assert result['sources']['eastmoney'].startswith('fail')
    assert result['themes'][0]['stocks'][0]['code'] == '300001'


def test_collect_all_sources_empty_raises(store):
    with pytest.raises(SourceError):
        collect(store, DAY, em=FakeEM(), ths=FakeTHS(), kpl=FakeKPL(), jygs=None, today=DAY)


def test_collect_rewrite_overwrites(store):
    run_today(store)
    em = FakeEM([em_row('300001', '电机甲', 1, '09:32:00')])
    ths = FakeTHS([ths_row('300001', '电机甲', '机器人+电机')])
    kpl = FakeKPL(themes=[{'code': '801001', 'name': '机器人', 'explain': None, 'stocks': [
        {'code': '300001', 'name': '电机甲', 'boards': 1, 'board_label': '首板'}]}])
    result = collect(store, DAY, em=em, ths=ths, kpl=kpl, jygs=None, today=DAY)
    assert len(result['themes']) == 1 and result['themes'][0]['stock_count'] == 1
    assert dates(store) == [DAY]
    assert view(store, '2026-01-01') is None


def client(tmp_path):
    return TestClient(create_app(Store(tmp_path / 'test.db'), collect=False))


def test_api_endpoints(tmp_path):
    store = Store(tmp_path / 'test.db')
    run_today(store)
    with TestClient(create_app(store, collect=False)) as c:
        assert c.get('/api/limitup/dates').json() == {'dates': [DAY]}
        data = c.get('/api/limitup/daily', params={'date': DAY}).json()
        assert data['themes'][0]['name'] == '机器人'
        assert c.get('/api/limitup/daily', params={'date': '2026-13-40'}).status_code == 422
        assert c.get('/api/limitup/daily', params={'date': '2020-01-02'}).status_code == 404
        assert c.post('/api/limitup/collect', params={'date': '2999-01-01'}).status_code == 422


def test_api_default_date_falls_back_to_latest(tmp_path):
    store = Store(tmp_path / 'test.db')
    run_today(store)
    with TestClient(create_app(store, collect=False)) as c:
        data = c.get('/api/limitup/daily').json()   # 未传日期 → 库内最新
        assert data['date'] == DAY


def test_eastmoney_parses_pool():
    src = EastmoneySource()
    pages = iter([
        {'data': {'tc': 2, 'pool': [
            {'c': '300002', 'n': '丝杠乙', 'zdp': 20.0, 'amount': 3e8, 'ltsz': 5e9, 'tshare': 1e8,
             'hs': 12.5, 'lbc': 3, 'fbt': 93100, 'lbt': 93100, 'fund': 2e8, 'zbc': 0,
             'hybk': '通用设备', 'zttj': {'days': 3, 'ct': 3}},
        ]}},
        {'data': {'tc': 2, 'pool': [
            {'c': '300001', 'n': '电机甲', 'zdp': 10.0, 'amount': 1e8, 'ltsz': 2e9, 'tshare': 5e7,
             'hs': 3.0, 'lbc': 1, 'fbt': 93200, 'lbt': 140000, 'fund': 1e8, 'zbc': 2,
             'hybk': '电机', 'zttj': {'days': 1, 'ct': 1}},
        ]}},
    ])
    src.get_json = lambda *a, **k: next(pages)
    src.min_interval = 0
    rows = src.fetch_limit_pool(DAY)
    assert len(rows) == 2
    assert rows[0]['boards'] == 3 and rows[0]['board_label'] == '3连板'
    assert rows[0]['first_time'] == '09:31:00'
    assert rows[1]['board_label'] == '1连板' and rows[1]['open_num'] == 2


def test_ths_parses_pool():
    src = ThsSource()
    src.get_json = lambda *a, **k: {'data': {'info': [
        {'code': '300002', 'name': '丝杠乙', 'reason_type': '机器人+丝杠',
         'high_days': '3天3板', 'first_limit_up_time': 1794274260, 'last_limit_up_time': 1794274260,
         'open_num': 0, 'order_amount': 2e8, 'limit_up_type': '换手板',
         'limit_up_suc_rate': 88.0, 'change_rate': 20.0, 'turnover_rate': 12.0},
    ], 'page': {'total': 1}}}
    src.min_interval = 0
    rows = src.fetch_limit_pool(DAY)
    assert rows[0]['reason'] == '机器人+丝杠' and rows[0]['high_days'] == '3天3板'
    assert rows[0]['first_time'] == '09:31:00'


def test_kaipanla_parses_board_and_resumption():
    src = KaipanlaSource()

    def fake_post(url, payload):
        if payload['a'] == 'RealRankingInfo':
            return {'list': [['801001', '机器人', '5230', '2.1'], ['801002', '锂电池', '3100', '-0.5']]}
        if payload['a'] == 'GetPlateInfo_w38':
            return {'nums': {'ZT': 5, 'DT': 2, 'ZBL': '21%', 'SZJS': 3200, 'XDJS': 1800},
                    'list': [{'ZSCode': '801001', 'ZSName': '机器人', 'TCExplain': ' 产业催化 ',
                              'StockList': [['300002', '丝杠乙', '', '', '20.0', '', 1794274260, 3, 2e8,
                                             '3天3板', '', '机器人、丝杠', '', '', '', '', '', '涨停原因全文']]}]}
        if payload['a'] == 'GetPlate_Info_QJ':
            return {'errcode': '0', 'List': ['3', '4100'], 'Date': HIST}
        if payload['a'] == 'GetYTFP_BKHX':
            return {'List': [{'Count': 1, 'Reason': ' 中秋备货 ', 'ZSCode': '801010', 'ZSName': '白酒',
                              'TD': [{'TDType': 1, 'Stock': [{'StockID': '600001', 'StockName': '甲', 'Tips': ' 2连板 '}]}]}]}
        raise AssertionError(payload)

    src._post = fake_post
    src.min_interval = 0
    board = src.fetch_strength_board(DAY)
    assert board[0] == {'code': '801001', 'name': '机器人', 'strength': 5230.0, 'change': 2.1, 'rank': 1}
    resumed = src.fetch_resumption(DAY)
    assert resumed['stats']['limit_up'] == 5
    stock = resumed['themes'][0]['stocks'][0]
    assert stock['code'] == '300002' and stock['boards'] == 3 and stock['first_time'] == '09:31:00'
    assert stock['tags'] == ['机器人', '丝杠'] and stock['reason_full'] == '涨停原因全文'
    assert resumed['themes'][0]['explain'] == '产业催化'
    strength = src.fetch_sector_strength('801001', HIST)
    assert strength == {'code': '801001', 'rank': 3, 'strength': 4100.0, 'date': HIST}
    ladder = src.fetch_sector_ladder(HIST)
    assert ladder[0]['stocks'][0] == {'code': '600001', 'name': '甲', 'board_label': '2连板'}
    assert ladder[0]['explain'] == '中秋备货'


def test_jiuyangongshe_runner_and_parsing():
    calls = []

    def fake_run(cmd, **kwargs):
        payload = json.loads(cmd[2])
        calls.append(payload)

        class Proc:
            stderr = ''
        if payload['op'] == 'fields':
            Proc.stdout = json.dumps({'ok': True, 'data': [
                {'action_field_id': 'f1', 'name': '机器人', 'count': 3, 'reason': ' 量产预期 '}]})
        else:
            Proc.stdout = json.dumps({'ok': True, 'data': {'list': [
                {'code': 'sz300002', 'name': '丝杠乙', 'article': {'action_info': {
                    'time': '09:31:00', 'num': '3天3板', 'expound': ' 核心供应商 '}}}]}})
        return Proc()

    src = JiuyangongsheSource(session='SESSION_VALUE', runner=fake_run)
    assert src.available()
    fields = src.fetch_action_fields(DAY)
    assert fields == [{'id': 'f1', 'name': '机器人', 'count': 3, 'reason': '量产预期'}]
    rows = src.fetch_action_list('f1')
    assert rows == [{'code': '300002', 'name': '丝杠乙', 'time': '09:31:00',
                     'board_label': '3天3板', 'reason': '核心供应商'}]
    assert calls[0]['session'] == 'SESSION_VALUE'

    err_src = JiuyangongsheSource(session='x', runner=lambda *a, **k: type(
        'P', (), {'stdout': json.dumps({'ok': False, 'error': 'SESSION 登录态失效'}), 'stderr': ''})())
    with pytest.raises(SourceError, match='登录态失效'):
        err_src.fetch_action_fields(DAY)
    assert not JiuyangongsheSource(session='', runner=fake_run).available()
