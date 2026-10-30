import json
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.import_browser import import_archive
from backend.provider import DataError
from backend.store import Store


def export(path, kind, code, **override):
    row=dict(f12=code,f14=kind,f2=100,f3=1,f6=1e8,f124=1793259570,f62=-1e7,f184=-10,f164=2e7,f165=20,f174=3e7,f175=30)
    query=dict(pn=1,pz=100,fltt=2,fs='m:90+'+('s:2' if kind=='industry' else 't:3')+'+f:!50')
    response={'rc':0,'data':{'total':1,'diff':[row]}}
    response['data'].update(override)
    path.write_text(json.dumps(dict(source_url='https://push2.eastmoney.com/api/qt/clist/get?'+urlencode(query),captured_at='2026-10-29T10:40:00Z',response=response)))


def test_import_exact_catalog_units_and_pinned_empty_history(tmp_path):
    directory=tmp_path/'exports';directory.mkdir()
    export(directory/'industry.json','industry','BK0001')
    export(directory/'concept.json','concept','BK0002')
    db=Store(tmp_path/'test.db')
    report=import_archive(db,directory)
    assert report['automatic_collection_verified'] is False
    original=db.snapshot('industry','2026-10-29','close')
    assert original['payload']['rows'][0]['flow']['today']==-1e7
    assert original['payload']['rows'][0]['heat'] is None
    # Later input arrivals, even within the same wall-clock second, cannot alter this revision.
    db.observe('bars:BK0001',[dict(date='2026-10-29',close=100,change=1,amount=1e8)])
    db.observe('members:BK0001:2026-10-29',['600001'])
    db.save('stocks','2026-10-29','close',{'rows':[dict(code='600001',name='后来数据',change=1)]})
    with TestClient(create_app(db,collect=False)) as c:
        got=c.get(f'/api/sectors/BK0001?date=2026-10-29&revision={original["id"]}').json()
        assert got['bars']==[] and got['members']==[]
        assert got['meta']['ingestion_method']=='manual_browser'
    assert Store(db.path).snapshot('industry','2026-10-29','close',original['id'])['sha256']==original['sha256']


def test_incomplete_page_or_overlapping_classification_rejected(tmp_path):
    directory=tmp_path/'exports';directory.mkdir()
    export(directory/'industry.json','industry','BK0001',total=2)
    export(directory/'concept.json','concept','BK0002')
    db=Store(tmp_path/'test.db')
    with pytest.raises(DataError,match='分页缺失'):
        import_archive(db,directory)
    assert not db.dates()
    export(directory/'industry.json','industry','BK0001')
    export(directory/'concept.json','concept','BK0001')
    with pytest.raises(DataError,match='分类重叠'):
        import_archive(db,directory)


def test_identical_observations_reuse_version(tmp_path):
    db=Store(tmp_path/'test.db')
    a=db.observe('members:BK0001:2026-10-29',['600001'])
    assert db.observe('members:BK0001:2026-10-29',['600001'])==a
    b=db.observe('members:BK0001:2026-10-29',['600001','600002'])
    assert b!=a
    assert db.observation_record(revision=a)['payload']==['600001']
