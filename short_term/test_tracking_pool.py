from .test_full_pool_quotes import setup,complete,NOW,VIEW
from .tracking_pool import front_codes,tracking_codes
from .test_fast_preview import preview
import pytest


def test_union_adds_front_only_without_changing_recent_store(setup):
    tracker,source,registry=setup
    view={**VIEW,'stocks':[dict(code='600099',boards=3,limit_timestamp=10)]}
    assert tracker.tick(view,NOW);complete(tracker)
    assert set(source.calls)==tracking_codes(view,registry)
    assert '600099' in tracker.last_known(NOW.isoformat())
    assert '600099' not in {r['code'] for r in registry.listing()}


def test_excluded_recent_still_eligible_only_when_front(setup):
    tracker,source,registry=setup
    registry.mutate('remove',['600001'])
    view={**VIEW,'stocks':[dict(code='600001',boards=3,limit_timestamp=10)]}
    assert '600001' in tracking_codes(view,registry)
    tracker.tick(view,NOW);complete(tracker)
    assert source.calls.count('600001')==1
    tracker.tick(VIEW,NOW.replace(hour=10));complete(tracker)
    assert source.calls.count('600001')==1


def test_selection_priority_is_validated_and_does_not_fetch(setup,monkeypatch):
    tracker,source,registry=setup
    tracker.prefer('600005',VIEW)
    assert not source.calls
    with pytest.raises(ValueError):tracker.prefer('999999',VIEW)
    assert tracker.preferred=='600005'
    submitted=[];submit=tracker.workers.submit
    def record(fn,code,*args):
        submitted.append(code);return submit(fn,code,*args)
    monkeypatch.setattr(tracker.workers,'submit',record)
    tracker.tick(VIEW,NOW);complete(tracker)
    assert submitted[0]=='600005'


def test_front_rule_includes_ten_and_caps_intraday():
    view={'stocks':[dict(code=str(600000+i),boards=1,limit_timestamp=i) for i in range(10)],'tenDay':{'rows':[{'code':'600030'}]}}
    assert front_codes(view)=={str(600000+i) for i in range(8)}|{'600030'}


def test_built_page_integrates_queue_and_removes_old_reset():
    import json
    seed=json.loads((preview.ROOT/'test-seed.json').read_text(encoding='utf-8'))
    page=preview.build_page(seed).decode()
    assert page.count('function renderDetail(){')==1
    assert 'globalThis.KplTracking.front(D,state.theme)' in page
    assert "!D.stocks.some(s=>s.code===state.stock)" not in page
    assert 'data-track-stage' in page and 'prefers-reduced-motion' in page
    assert 'tab===\'recent\'&&!dialog.open' not in page
    assert 'kpl-recent-data' in page


def test_payload_computes_tracking_members_once_for_large_old_quote_cache(setup,monkeypatch):
    from . import wind_core
    tracker,source,registry=setup
    calls=[];original=wind_core.tracking_codes
    def count(view,store):
        calls.append(1);return original(view,store)
    monkeypatch.setattr(wind_core,'tracking_codes',count)
    monkeypatch.setattr(tracker,'last_known',lambda at:{str(600001+i):{'code':str(600001+i),'quote_day':'2026-11-20'} for i in range(1000)})
    service=wind_core.WindService(registry)
    try:
        payload=service.payload(VIEW,tracker)
        assert len(calls)==1
        assert len(payload['tracking_quotes'])==10
    finally:service.close()
