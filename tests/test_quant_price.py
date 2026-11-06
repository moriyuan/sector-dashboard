import json
import numpy as np
import pytest
from datetime import date

from backend.barra import router
from backend.quant_price import risk_exposures
from backend.store import Store


def test_beta_residual_momentum_and_turnover():
    rng=np.random.default_rng(20)
    benchmark=rng.normal(size=252)
    noise=rng.normal(size=252)
    returns=(.1+1.7*benchmark+noise)[:,None]
    turn=np.linspace(1,9,252)[:,None]
    actual=risk_exposures(returns,benchmark,turn)[0]
    design=np.column_stack((np.ones(252),benchmark))
    coeff=np.linalg.lstsq(design,returns[:,0],rcond=None)[0]
    assert actual[0]==pytest.approx(coeff[1])
    assert actual[2]==pytest.approx(np.sqrt(np.sum((returns[:,0]-design@coeff)**2)/250))
    assert actual[1]==pytest.approx(100*(np.prod(1+returns[-147:-21,0]/100)-1))
    assert actual[3]==pytest.approx(np.mean([np.log(turn[-h:].mean()) for h in (20,60,252)]))


def test_exposure_missingness_is_per_window():
    y=np.linspace(-1,1,252)[:,None]
    t=np.ones_like(y); y[130]=np.nan
    result=risk_exposures(y,np.linspace(0,1,252),t)[0]
    assert np.isfinite(result[0]) and np.isnan(result[1])
    t[-5:]=np.nan;y[:60]=np.nan
    result=risk_exposures(y,np.linspace(0,1,252),t)[0]
    assert np.isnan(result[[0,2,3]]).all()
    assert np.isnan(risk_exposures(y,np.zeros(252),t)[0,0])


def test_model_and_date_do_not_silently_mix(tmp_path):
    store=Store(tmp_path/'market.db');root=tmp_path/'quant-price-v2/results';root.mkdir(parents=True)
    (root/'2026-11-05.json').write_text(json.dumps({'day':'2026-11-05','version':'cn-price-volume-v2','factors':[]}))
    view=router(store).routes[0].endpoint
    assert view()['day']=='2026-11-05'
    assert 'reason' in view(date(2026,11,4),'price_volume')
    assert view(None,'archive')['version']=='cn-close-style-v1'


def test_daily_fit_uses_only_prior_membership_cap_and_style():
    from backend.quant_price import DTYPE,estimate_day
    rng=np.random.default_rng(17);n=240
    old=np.zeros(n,dtype=DTYPE)
    old['code']=np.arange(n);old['status']=1;old['close']=old['amount']=1
    old['ret']=rng.normal(size=n);old['float_cap']=np.exp(rng.normal(size=n))
    old['turn']=np.exp(rng.normal(size=n));old['pb']=np.exp(rng.normal(size=n))
    old['industry']=np.where(np.arange(n)%2,'C39','J66')
    current=old.copy();current['ret']=rng.normal(size=n)
    styles=rng.normal(size=(n,4))
    labels={'C39':'制造','J66':'金融'}
    fit=estimate_day(old,current,labels,styles)
    current['industry']='Z99';current['pb']=np.nan;current['float_cap']=10000;current['st']=1
    again=estimate_day(old,current,labels,styles)
    assert len(fit['factors'])==11 and fit['factors']==again['factors']
    current['status'][:12]=0
    assert estimate_day(old,current,labels,styles)['n']==228
