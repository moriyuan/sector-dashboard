"""Evidence for a closing move. Board membership never enters the join."""
import re
import json
from collections import Counter, defaultdict
from datetime import date, datetime, time
from functools import lru_cache
from .news import AMBIGUOUS_NAMES, digest, normalize_time
from .news_sources import SOURCES
from .store import TZ

RULE = 'move-news-v1'
MOVE = re.compile(r'涨停|封板|连板|天\d+板|上涨|跟涨|涨超|涨逾|大涨|拉升|冲高|涨幅(?:居前|靠前)|走高')
MARKER = re.compile(r'消息面上|(?:本次|今日|当日)?(?:上涨|涨停|异动)(?:的)?(?:原因(?:为|是|包括|在于|[：:])|主要因|源于)')
DRIVEN = re.compile(r'(?:受到?|由于)([^，,。；;！？]{4,120}?)(?:的)?(?:提振|带动|推动|刺激|影响)[，,]?')
FACT = re.compile(r'公告|签[订署]|中标|订单|收购|重组|注入|获批|批准|发[布行]|印发|推出|召开|开会|会议|倡议|公布|披露|调[涨价]|上调|加码|降息|降准|增长|增持|扭亏|净利|投产|量产|突破|展示|上线|验证|交付|补贴|出货|扩产|同比|环比|税率|豁免|实施|落地|生效|销量|禁令|获准')
DOUBT = re.compile(r'辟谣|不实|否认|澄清|并非|无关|并未|并无|未证实|尚未核实|未经证实|网传|传闻|谣言|暂无依据|未有定论|原因不明|原因尚不明确')
FUTURE = re.compile(r'明日|明天|次日|有望涨|或将涨|预计.*涨停|可能涨停|预计.*上涨')
LATE_FACT = re.compile(r'盘后|收盘后|今晚|今夜|今日晚间|当天晚间|当日晚间')
TOPIC = re.compile(r'(?:^|[，,：:])(?:早盘|午后|午间|其中|今日|今天|此前|盘中|随后)*([A-Za-z0-9\u4e00-\u9fff+]{2,15}?)(?:板块|概念|题材|方向)(?:股|盘中|午后|震荡|表现|异动|持续|集体|反复|早盘|继续|强势|日内)*(?:走高|拉升|爆发|反弹|活跃|回升|走强|领涨|上涨|大涨)')
FOCUS = re.compile(r'(?:^|[，,])其中(?:以)?([A-Za-z0-9\u4e00-\u9fff+]{2,15}?)(?:最为强势|涨势最强|表现最强)')
RETRO = re.compile(r'今日|今天|当日|早盘|午后|盘中|开盘|收盘|午盘|收评|复盘')
OLDER = re.compile(r'昨日|昨天|前日|前天|上周|上一交易日|前一交易日|上个交易日')
MOVE_GAP = re.compile(r'(?:\s|等|均|皆|也|齐|纷纷|双双|多股|个股|股价|盘中|今日|当日|早盘|午后|午盘|早间|大幅|直线|触及|逼近|一字|一度|开盘|收盘|强势|率先|集体|录得|[0-9]|[cC][mM])*')
JOIN = re.compile(r'\s*(?:、|与|及|和)\s*')
# Qualify codes by security context, checking the exchange against the snapshot.
CODE = r'(?:证券代码|股票代码|代码)\s*[：:]?\s*([036]\d{5})(?![A-Za-z0-9]|\s*\.)|[（(]([036]\d{5})[）)]|(?<![A-Za-z0-9])(SH|SZ)([036]\d{5})(?![A-Za-z0-9])|(?<![A-Za-z0-9])([036]\d{5})\.(SH|SZ)(?![A-Za-z0-9])'


@lru_cache(maxsize=3)
def matcher(directory):
    counts = Counter(n for n,c in directory)
    names = {n:c for n,c in directory if len(n)>=3 and counts[n]==1 and n not in AMBIGUOUS_NAMES and not n.isdigit()}
    pattern = CODE + ('|'+ '|'.join(map(re.escape,sorted(names,key=len,reverse=True))) if names else '')
    return re.compile(pattern,re.I), names, {c for n,c in directory}


def movers(line, matching):
    pattern,names,universe = matching
    hits=[]
    for m in pattern.finditer(line):
        code = names.get(m[0]) or m[1] or m[2] or m[4] or m[5]
        exchange=m[3] or m[6]
        if code not in universe or (exchange and exchange.upper()!=('SH' if code.startswith('6') else 'SZ')):
            continue
        hits.append((code,m.start(),m.end()))
    found=set()
    for motion in MOVE.finditer(line):
        pos=motion.start()
        for code,start,end in reversed([h for h in hits if h[2]<=pos]):
            gap=line[end:pos].strip(' ）)')
            if not MOVE_GAP.fullmatch(gap):
                break
            found.add(code)
            # Only coordinated names share this verb. A party mentioned elsewhere doesn't.
            for prev,a,b in reversed([h for h in hits if h[2]<=start]):
                if not JOIN.fullmatch(line[b:start]):break
                found.add(prev);start=a
            break
    return found


def units(text):
    """A reason and the local price-move clause must be present in the same passage."""
    sentences=[s.strip(' ，,:：') for s in re.split(r'[。！？!?；;\n]+',text) if s.strip(' ，,:：')]
    for i,line in enumerate(sentences):
        marker=MARKER.search(line)
        if marker:
            move=line[:marker.start()].strip(' ，,:：') or (sentences[i-1] if i else '')
            if marker[0]!='消息面上' and not MOVE.search(move) and marker.start():
                movement=MOVE.search(marker[0])
                if movement:move=line[:marker.start()+movement.end()]
            fact=line[marker.end():].strip(' ：:、，,')
            if len(fact)<8 and i+1<len(sentences):
                fact=(fact+'。'+sentences[i+1]).strip('。')
            yield move,fact,('context' if marker[0]=='消息面上' else 'attributed'),move
        for trigger in DRIVEN.finditer(line):
            # Two supported syntaxes: 受X影响，A上涨 / A受X影响上涨.
            after=re.split(r'[，,]',line[trigger.end():].lstrip(' ，,'),maxsplit=1)[0]
            before=re.split(r'[，,]',line[:trigger.start()])[-1]
            move=before+after
            yield move,trigger[1],'attributed',before+trigger[0]+after

        because=re.search(r'(?<!原)因(?:为)?([^，,；;！？。]{4,85}?)(?:而|从而)?(涨停|上涨|大涨|走高)',line)
        if because:
            before=re.split(r'[，,]',line[:because.start()])[-1]
            yield before+because[2],because[1],'attributed',before+because[0]


def report_passages(report, extras):
    # Domestic feeds commonly repeat the headline in 【】 before the actual flash.
    title=report['title'].strip()
    if title.startswith('【') and '】' in title:
        title=title.partition('】')[2] or title[1:-1]
    body=re.sub(r'^【[^】]{1,550}】','',report.get('summary','')).strip()
    return [(s,'快讯') for s in dict.fromkeys((body,title)) if s]+[(s,'原文补证') for s in extras if s]


def assertions(report, extras, matching, day):
    pub=datetime.fromisoformat(report['published_at']).astimezone(TZ)
    if pub.date()!=day or pub.time()<time(9,30) or DOUBT.search(report['title']):return
    for text,origin in report_passages(report,extras):
        if not MOVE.search(text) or DOUBT.search(text):continue
        for move,fact,kind,original in units(text):
            if not MOVE.search(move) or len(fact)<6 or not FACT.search(fact):continue
            if OLDER.search(move) or FUTURE.search(move) or LATE_FACT.search(fact):continue
            if pub.time()>=time(15) and re.search(r'(?:盘后|收盘后|今晚)(?:发布|披露|公告|宣布)',report['title']):continue
            if any((int(m),int(d))!=(day.month,day.day) for m,d in re.findall(r'(\d{1,2})月(\d{1,2})日',move)):continue
            if pub.time()>=time(15) and not (RETRO.search(move+' '+report['title']) or f'{day.month}月{day.day}日' in move+' '+report['title']):continue
            # An explicitly narrowed subject in a recap excludes preceding movers.
            focus=list(FOCUS.finditer(move))
            if focus:move=move[focus[-1].start():].strip('，,')
            topics=list(TOPIC.finditer(move))
            if len(topics)>1 or (focus and topics):continue
            topic=focus[-1][1] if focus else topics[0][1] if topics else None
            if topics:move=move[topics[0].start():].strip('，,')
            stocks=movers(move,matching)
            if not topic and kind=='context' and len(stocks)>1:
                stocks &= {matching[1][m[0]] for m in matching[0].finditer(fact) if m[0] in matching[1]}
            if stocks:
                yield fact,topic,kind,stocks,(move if kind=='context' else original),origin


def headline_lead(report, matching, day):
    """Juxtaposition is a lead to read, never a cause or a peer edge."""
    pub=datetime.fromisoformat(report['published_at']).astimezone(TZ)
    title=report['title']
    if pub.date()!=day or not time(9,30)<=pub.time()<time(15) or DOUBT.search(title):return
    fact=re.split(r'[，,！!。；;]',title.lstrip('【'),maxsplit=1)[0]
    if len(fact)<8 or not FACT.search(fact) or MOVE.search(fact) or LATE_FACT.search(fact):return
    for passage,origin in report_passages(report,[]):
        if DOUBT.search(passage):continue
        for line in re.split(r'[。；;！？]',passage):
            if MOVE.search(line) and not OLDER.search(line) and not FUTURE.search(line):
                stocks=movers(line,matching)
                if stocks:yield fact,line,stocks


def news_rows(news, day, cutoff):
    start=normalize_time(datetime.combine(day,time.min,TZ))
    with news.store.connect() as db:
        reports=db.execute('''SELECT event_id,payload,id FROM news_items n
            WHERE published BETWEEN ? AND ? AND seen<=? AND NOT EXISTS (
            SELECT 1 FROM news_items v WHERE v.source=n.source AND v.source_id=n.source_id
            AND v.id>n.id AND v.seen<=?) ORDER BY published,id''',(start,cutoff,cutoff,cutoff)).fetchall()
        # The enrichment table is keyed by the exact underlying report revision.
        extra=db.execute('''SELECT e.* FROM news_enrichment e WHERE e.id IN (
            SELECT MAX(e.id) FROM news_items n JOIN news_enrichment e USING(source,source_id)
            WHERE n.published BETWEEN ? AND ? AND n.seen<=? AND e.created<=?
            GROUP BY e.source,e.source_id,e.report_hash)''',(start,cutoff,cutoff,cutoff)).fetchall()
    enriched={(r['source'],r['source_id'],r['report_hash']):dict(json.loads(r['payload']),captured_at=r['created']) for r in extra}
    roots=news.event_roots(cutoff)
    for r in reports:
        report=json.loads(r['payload'])
        key=(report['source'],report['source_id'],digest({k:report.get(k) for k in ('title','summary','url')}))
        article=enriched.get(key) or {}
        yield roots.get(r['event_id'],r['event_id']),r['id'],report,article


def related_moves(news, day, now, market, notices, codes, *, preview=False):
    day=date.fromisoformat(day)
    cutoff=normalize_time(min(now,datetime.combine(day,time(23,59,59),TZ)))
    revision,_,quotes=market
    by_code={q['code']:q for q in quotes}
    ranks={q['code']:i for i,q in enumerate((q for q in quotes if not q.get('is_st')),1) if i<=100 and (q.get('change') or 0)>0}
    periods=defaultdict(set)
    for r in notices:periods[r['code']].add(r['period'])
    result={}
    for c in codes:
        change=by_code.get(c,{}).get('change')
        state='unavailable' if revision is None else 'missing_quote' if change is None else 'not_rising' if change<=0 else 'unverified'
        result[c]=dict(state=state,total=0,rows=[],causes=[],leads=[],rule=RULE)
    wanted={c for c in codes if result[c]['state']=='unverified'}
    if not wanted:return result
    matching=matcher(tuple((r['name'],r['code']) for r in quotes if r.get('name')))
    groups=defaultdict(dict); disputed=set(); leads=defaultdict(dict)
    for eid,report_revision,report,article in news_rows(news,day,cutoff):
        extras=article.get('excerpts',[]) if article.get('status')=='ok' else []
        if DOUBT.search(report['title']+' '+report.get('summary','')):disputed.add(eid)
        for fact,line,stocks in headline_lead(report,matching,day):
            for code in wanted.intersection(stocks):
                leads[code].setdefault(eid,dict(id=eid,revision=report_revision,title=report['title'],url=report['url'],
                    source=SOURCES[report['source']]['name'],published_at=report['published_at'],timing='盘中报道',
                    excerpt=line,fact=fact,origin='标题与行情并列'))
        for fact,topic,kind,stocks,move,origin in assertions(report,extras,matching,day):
            norm=re.sub(r'[\s，,。；;：“”"、]','',fact)
            # Separate articles can corroborate an exact, identifiable fact. A broad
            # theme or an unnamed company's disclosure is not a shared event key.
            specific=len(norm)>=20 and (re.search(r'《[^》]{5,}》|(?:人民银行|证监会|国务院|英伟达|国家发改委|国家新闻出版署|央视财经|美团数据)',fact)) and not re.search(r'(?:本|该)公司',fact)
            key=digest([norm,None if specific else (eid,move)])[:20]
            pub=report['published_at']
            ev=dict(id=eid,revision=report_revision,title=report['title'],url=report['url'],source=SOURCES[report['source']]['name'],
                    published_at=pub,timing='盘后回顾' if datetime.fromisoformat(pub).astimezone(TZ).time()>=time(15) else '盘中报道',
                    excerpt=move,origin=origin,fact=fact,captured_at=article.get('captured_at') if origin=='原文补证' else None)
            for code in stocks.intersection(wanted|ranks.keys()):
                groups[key].setdefault(code,[]).append((kind,topic,ev))
    for key,group in groups.items():
        for code in list(group):
            entries=[v for v in group[code] if v[2]['id'] not in disputed]
            if not entries:del group[code];continue
            # Prefer an explicit attribution and retain the exact passage for each stock.
            group[code]=min(entries,key=lambda v:(v[0]!='attributed',v[2]['published_at'],v[2]['revision']))
        for c in wanted.intersection(group):
            kind,topic,ev=group[c]
            result[c]['causes'].append(dict(key=key,level=kind,topic=topic,evidence=ev))
            for peer in (group.keys() & ranks.keys()) - {c}:
                result[c]['rows'].append((peer,key,group[peer][2]))
    for target,profile in result.items():
        excluded={a['evidence']['id'] for a in profile['causes']} | disputed
        candidates=(ev for k,ev in leads[target].items() if k not in excluded)
        ranked=sorted(candidates,key=lambda ev:(by_code.get(target,{}).get('name',target) not in ev['title'],ev['published_at']))
        distinct={}
        for ev in ranked:distinct.setdefault(re.sub(r'\s+','',ev['fact']),ev)
        profile['leads']=list(distinct.values())[:2]
        if profile['causes']:profile['state']='ok'
        profile['causes'].sort(key=lambda a:(a['level']!='attributed',a['evidence']['published_at'],a['key']))
        peers={}
        for code,key,ev in profile['rows']:
            quote=by_code[code]
            peer=peers.setdefault(code,dict(code=code,name=quote['name'],rank=ranks[code],change=quote['change'],
                periods=[p for p in ('single','three','other') if p in periods[code]],matches=[]))
            peer['matches'].append(dict(key=key,evidence=ev))
        rows=sorted(peers.values(),key=lambda r:r['rank'])
        profile.update(total=len(rows),rows=rows[:2] if preview else rows)

    return result
