from datetime import date, timedelta
import json

import pytest
from backend.index_pe import summarize, day_view, parse_etf
from backend.store import Store


def test_window_ties_and_missing_pe():
    end = date(2024, 2, 29)
    rows = {(end-timedelta(days=i)).isoformat(): 12 if i <= 20 else 9 for i in range(120)}
    rows.update({'2019-02-28': 3, '2020-04-01': -1, '2020-04-02': None, '2024-03-01': 40})
    close = summarize(rows, end.isoformat(), 'close')
    assert (close['count'], close['percentile']) == (120, 82.5)
    noon = summarize(rows, end.isoformat(), 'midday')
    assert noon['date'] == '2024-02-28' and noon['percentile'] is None
    rows['2024-02-29'] = None
    assert summarize(rows, end.isoformat(), 'close')['pe'] is None


def test_archive_revision_and_backfill(tmp_path):
    store = Store(tmp_path/'pe.db')
    key = 'index-pe:v1:000300'
    with store.connect() as db:
        for stamp, rows in [('2026-11-02T19:00:00+08:00', {'2026-10-30':10,'2026-11-02':12}),
                            ('2026-11-03T19:00:00+08:00', {'2026-10-30':99,'2026-11-02':13,'2026-11-03':14})]:
            db.execute('INSERT INTO observations(key,observed_at,payload) VALUES (?,?,?)',(key,stamp,json.dumps(rows)))
    def item(day, mode):
        return next(r for r in day_view(store,day,mode) if r['code']=='000300')
    assert item('2026-11-02','close')['pe']==12 and not item('2026-11-02','close')['backfilled']
    assert item('2026-11-02','midday')['pe']==10 and item('2026-11-02','midday')['backfilled']
    assert item('2026-10-29','close')['date'] is None


def test_validate_etf_identity_and_named_fields():
    props = dict(indexInfo={'innerCode':'399001.SZSE'},compressedIndexDaily={
        'fieldNames':['equalWeightedPeTtm','date'],'values':[[12,'2026-10-30']]})
    packet = json.dumps([1,'11:'+json.dumps(['$','test',None,props])+'\n'])
    html = '<script>self.__next_f.push('+packet+')</script>'
    assert parse_etf(html,'399001') == [('2026-10-30',12)]
    with pytest.raises(ValueError): parse_etf(html,'399006')
