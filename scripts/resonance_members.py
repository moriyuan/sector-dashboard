"""Export membership known by the requested open from frozen board snapshots."""
import argparse,json,re,sqlite3
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from backend.ths import detail_page
from backend.provider import DataError


def known_boards(db, day, prior):
    output=[];snapshots={}
    for kind in ('industry','concept_ths'):
        snap=db.execute("""select id,collected_at,payload from snapshots where kind=? and trade_date=?
              and mode='close' and collected_at<? order by id desc limit 1""",(kind,prior,day+'T09:30')).fetchone()
        if not snap:continue
        revision,cutoff,raw=snap;data=json.loads(raw);refs=data.get('input_refs',{});snapshots[kind]=revision
        catalog={r['code']:r for r in data['rows']}
        complete={}
        for code,row in catalog.items():
            ref=refs.get('members',{}).get(code)
            if row.get('membership_verified') and ref:
                val=db.execute('select payload from observations where id=? and key=?',(ref,'members:'+code+':'+prior)).fetchone()
                if val:complete[code]=json.loads(val[0])
        if kind=='concept_ths':
            # Recover complete page sets only, using the same close-session cutoff as the board revision.
            base='ths_raw:https://q.10jqka.com.cn/gn/detail/code/'
            records=db.execute("""select key,payload from observations where id in
                (select max(id) from observations where key>=? and key<? and observed_at between ? and ? group by key)""",
                (base,base[:-1]+'0',prior+'T15:00',min(cutoff,prior+'T23:59:59+08:00')))
            parsed={}
            for key,payload in records:
                match=re.fullmatch(r'(\d{6})/(?:page/(\d+)/)?',key[len(base):])
                if not match:continue
                code='THS'+match[1];row=catalog.get(code)
                if not row or code in complete:continue
                cap=json.loads(payload)
                if cap.get('source_url')!=key[8:] or not prior+'T15:00'<=cap.get('captured_at','')<=min(cutoff,prior+'T23:59:59+08:00'):continue
                try:page=detail_page(cap['text'],match[1],row.get('expected_count'))
                except DataError:continue
                if page['current']==int(match[2] or 1):parsed.setdefault(code,{})[page['current']]=page
            for code,pages in parsed.items():
                first=pages.get(1)
                if not first or set(pages)!=set(range(1,first['pages']+1)):continue
                if any(p['index_code']!=first['index_code'] or p['pages']!=first['pages'] for p in pages.values()):continue
                codes=[c for p in pages.values() for c in p['members']]
                expected=catalog[code].get('expected_count')
                if len(set(codes))==len(codes) and (expected is None or len(codes)==expected):complete[code]=codes
        for code,codes in complete.items():
            if len(codes)==len(set(codes)) and codes:
                output.append(dict(code=code,name=catalog[code]['name'],kind='行业' if kind=='industry' else '同花顺概念',members=codes))
    return dict(day=day,prior=prior,snapshots=snapshots,boards=output)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('db');p.add_argument('output',type=Path);p.add_argument('pairs',nargs='+');args=p.parse_args()
    args.output.mkdir(exist_ok=True,parents=True)
    with sqlite3.connect('file:'+args.db+'?mode=ro',uri=True) as db:
        for pair in args.pairs:
            day,prior=pair.split(',');data=known_boards(db,day,prior)
            (args.output/(day+'.json')).write_text(json.dumps(data,ensure_ascii=False,separators=(',',':')))
            print(day,len(data['boards']),flush=True)
