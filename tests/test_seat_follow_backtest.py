import numpy as np
from scripts.seat_follow_backtest import entry_check, simulate, limit_price, make_signals, metrics


def bar(o=10,h=10.5,l=9.8,c=10.2,pre=10,st=0,status=1):
 return dict(open=o,high=h,low=l,close=c,preclose=pre,st=st,status=status,volume=100000,amount=1000000)


def test_entry_no_limit_queue_or_suspension():
 assert entry_check('600000',bar(o=11,h=11,l=11,c=11),'2026-01-05',50)=='one_price_up'
 assert entry_check('600000',bar(o=11,h=11,l=10.2,c=10.5),'2026-01-05',50)=='open_limit_up'
 assert entry_check('600000',bar(status=0),'2026-01-05',50)=='suspended'
 assert entry_check('600000',bar(st=1),'2026-01-05',50)=='st'
 assert entry_check('688001',bar(),'2026-01-05',3)=='ipo_first_five'
 assert entry_check('600000',bar(),'2026-01-05',50)=='eligible'
 assert limit_price('600000',bar(pre=10.05),'2026-01-05',True)==11.06
 assert limit_price('300001',bar(pre=10,st=1),'2026-01-05',True)==12
 assert limit_price('600000',bar(pre=10,st=1),'2026-01-05',True)==10.5
 assert limit_price('600000',bar(pre=10,st=1),'2026-07-06',True)==11


def test_t_plus_one_adjustment_and_blocked_exit():
 dates=['2026-01-05','2026-01-06','2026-01-07','2026-01-08']
 bars=[bar(o=10,c=10.5),bar(o=10.6,c=11,pre=10.5),bar(o=10.9,c=10.8,pre=11),bar(o=10.7,c=11,pre=10.8)]
 result=simulate('600000',bars,dates,0,1)
 assert result['exit_date']==dates[1] # Never sell on purchase day.
 assert np.isclose(result['gross_return'],.1)
 split=[bar(c=10),bar(o=5,c=5.5,h=5.6,l=5,pre=5)]
 assert np.isclose(simulate('600000',split,dates[:2],0,1)['gross_return'],.1)
 locked=[bar(c=10),bar(o=9,h=9,l=9,c=9),bar(o=8.8,h=9.1,l=8.5,c=8.9,pre=9)]
 result=simulate('600000',locked,dates[:3],0,1)
 assert result['exit_date']==dates[2] and result['delay']==1
 assert result['net_return']<result['gross_return']<0
 assert simulate('600000',locked[:2],dates[:2],0,1)['state']=='unresolved_exit'
 assert simulate('600000',bars[:1],dates[:1],0,1)['state']=='right_censored'
 assert simulate('600000',[bar(),None],dates[:2],0,1)['state']=='missing_path'


def test_events_deduplicate_periods_conflicts_and_nets():
 r=dict(profile='甲',TRADE_DATE='2025-01-02 00:00:00',SECURITY_CODE='600000',SECURITY_NAME_ABBR='甲公司',NET_AMT=100,EXPLANATION='日涨幅偏离值达到7%',SECUCODE='600000.SH')
 signals,audit=make_signals([r,r,dict(r,EXPLANATION='日换手率达到20%'),dict(r,EXPLANATION='连续3个交易日涨幅偏离'),dict(r,SECURITY_CODE='600001',NET_AMT=-20)])
 assert len(signals)==2 and {r['period'] for r in signals}=={'single','three'}
 assert all(r['net']==100 for r in signals)
 signals,audit=make_signals([r,dict(r,NET_AMT=200)])
 assert signals==[] and audit['conflicting_amount']==1
 assert metrics([])['n']==0


def test_missing_exits_are_counted_and_costs_can_turn_a_flat_trade_negative():
    dates=['2026-01-05','2026-01-06','2026-01-07']
    flat=[bar(c=10),bar(c=10),bar(c=10)]
    result=simulate('600000',flat,dates,0,1)
    assert result['gross_return']==0 and -.00213<result['net_return']<-.00211
    m=metrics([dict(result,entry_date=dates[0]),dict(state='missing_path',entry_date=dates[1])])
    assert m['events']==2 and m['n']==1 and m['states']['missing_path']==1
    assert m['win_rate']==0 and m['mean_return']<0
