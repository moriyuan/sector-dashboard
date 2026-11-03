from datetime import datetime
import pytest
from backend.provider import Eastmoney, DataError, quote_row, source_time


class Stub(Eastmoney):
    def __init__(self, responses):
        self.responses=iter(responses)
    def get(self,*args):
        return next(self.responses)


def page(total, rows):
    return {'data':{'total':total,'diff':rows}}


def test_listing_checks_pagination_and_repeated_codes():
    s=Stub([page(3,[{'f12':'a'},{'f12':'b'}]),page(3,[{'f12':'c'}])])
    assert len(s.listing('scope'))==3
    with pytest.raises(DataError,match='重复'):
        Stub([page(3,[{'f12':'a'}]),page(3,[{'f12':'a'}])]).listing('scope')
    with pytest.raises(DataError,match='总数改变'):
        Stub([page(3,[{'f12':'a'}]),page(4,[{'f12':'b'}])]).listing('scope')
    with pytest.raises(DataError,match='数量'):
        Stub([page(2,[{'f12':'a'}]),page(2,[])]).listing('scope')


def test_decimal_units_missing_values_and_mixed_dates():
    r=quote_row({'f12':'BK0475','f14':'银行','f2':4505.8,'f3':1.98,'f6':39482278513,'f124':1793259570,'f18':'-'})
    assert r['price']==4505.8 and r['change']==1.98
    assert r['amount']==39482278513 and r['pre_close'] is None
    assert r['source_time'].startswith('2026-10-29')
    with pytest.raises(DataError,match='多个交易日期'):
        source_time([r,{'source_time':'2026-10-28T15:00:00+08:00'}])
    assert source_time([{'source_time':None}]) is None


def test_pool_date_mismatch_and_empty_vs_unavailable():
    assert Stub([{'data':{'qdate':20261029,'tc':0,'pool':[]}}]).pool('up','2026-10-29')==[]
    with pytest.raises(DataError,match='不可用'):
        Stub([{'data':None}]).pool('up','2026-10-29')
    with pytest.raises(DataError,match='日期'):
        Stub([{'data':{'qdate':20261028,'pool':[]}}]).pool('up','2026-10-29')


def test_daily_bar_validation_and_units():
    bar='2026-10-29,4429.44,4505.80,4509.11,4427.65,46392407,39482278513.00,1.84,1.98'
    r=Stub([{'data':{'code':'BK0475','klines':[bar]}}]).bars('BK0475')[0]
    assert r['close']==4505.8 and r['amount']==39482278513 and r['change']==1.98
    with pytest.raises(DataError,match='代码'):
        Stub([{'data':{'code':'BK0000','klines':[bar]}}]).bars('BK0475')


def test_index_sources_request_shanghai_star_composite(monkeypatch):
    from backend.public_data import PublicData

    codes=['000001','399001','399006','000300','000680']
    captured={}
    em=Stub([])
    def get(url,params):
        captured['secids']=params['secids'].split(',')
        return {'data':{'diff':[{'f12':c,'f13':0 if c.startswith('399') else 1} for c in codes]}}
    monkeypatch.setattr(em,'get',get)
    assert [r['code'] for r in em.indices()]==codes
    assert captured['secids']==['1.000001','0.399001','0.399006','1.000300','1.000680']

    qq=PublicData(None,target_date='2026-11-02')
    def quotes(symbols):
        assert list(symbols)==['sh000001','sz399001','sz399006','sh000300','sh000680']
        return [{'code':s[2:],'source_time':'2026-11-02T16:14:12+08:00'} for s in symbols]
    monkeypatch.setattr(qq,'quotes',quotes)
    assert [r['code'] for r in qq.indices()]==codes
