"""Conservative event grouping and evidence contracts; no inferred supplier identities."""
import json
import re
from datetime import datetime, timedelta

VERSION = 'news-v3'
EXTRA_TERMS = {
    'ai': ['TPU', 'GPU', 'Google Cloud', 'AWS', 'AI漫剧', '物理AI', '智驾', '云计算', '数据中心'],
    'chips': ['HBM', 'DDR5', 'DRAM', 'NAND', '存储芯片', '封装', '电子布', 'T布'],
    'robot': ['执行器', '减速器', '丝杠', '傅利叶'],
    'energy': ['磷酸铁锂', '正极材料', '负极材料'],
    'health': ['同位素', '锗镓发生器', '创新药'],
    'resources': ['钨', '焦煤', '煤炭', '天然气', '锂矿'],
}


def term_match(text, word):
    # Python \b treats Han and Latin letters alike. Bound Latin tokens by Latin
    # alphanumerics instead, so AI漫剧 matches but retail does not match AI.
    return bool(re.search(r'(?<![a-zA-Z0-9])'+re.escape(word)+r'(?![a-zA-Z0-9])', text, re.I)) if word.isascii() else word.lower() in text.lower()


def detect_themes(text):
    from .news import THEMES
    out=[]
    for theme, (_, terms) in THEMES.items():
        subject=text
        if theme=='consumer':
            subject=re.sub(r'消费电子|消费级|消费者财务|消费贷款|零售贷款|零售银行|consumer electronics|retail banking', '', subject, flags=re.I)
        if any(term_match(subject,w) for w in terms+EXTRA_TERMS.get(theme,[])):
            out.append(theme)
    return out


def normalized(text):
    text=re.sub(r'(财联社|新浪财经|东方财富)\d*月?\d*日?电[，,:：]?|据报道|消息称|报道称|表示|称|回应|：|持续', '', text)
    text=text.replace('优质投资标的','投资标的')
    return re.sub(r'[^a-z0-9\u4e00-\u9fff]', '', text.lower())


def stage(text):
    if re.search(r'否认|辟谣|不属实|撤回|澄清|denies|denied|retract',text,re.I):return 'denial'
    if re.search(r'传闻|消息称|据报道|暂不方便|reportedly|rumou?r|^传',text,re.I):return 'rumor'
    if re.search(r'确认|证实|confirm',text,re.I):return 'confirmed'
    return 'statement'


def same_event(a,b):
    """Same narrow fact within 36h; revisions/denials never silently overwrite it."""
    from .news import normalize_time
    if abs((datetime.fromisoformat(normalize_time(a['published_at']))-datetime.fromisoformat(normalize_time(b['published_at']))).total_seconds())>36*3600:return False
    def headline(r):
        match=re.match(r'^【([^】]+)】',r['title'])
        return match[1] if match else r['title']
    ta,tb=headline(a),headline(b)
    def report_stage(r,title):
        s=stage(title)
        return stage(r.get('summary','')) if s=='statement' else s
    sa,sb=report_stage(a,ta),report_stage(b,tb)
    if sa!=sb:return False
    # Period, reported price and progress stage changes are separate observations.
    periods=lambda t:set(re.findall(r'[一二三四1234]季度|20\d{2}年|\d+月|Q[1-4]',t,re.I))
    if periods(ta)!=periods(tb):return False
    phase=lambda t:set(re.findall(r'测试|试用|小批量|批量供货|量产|签约|计划|预计|拟',t))
    if phase(ta)!=phase(tb):return False
    if any(w in ta+tb for w in ['涨停','涨幅','现报','跌幅','期货']):
        if normalized(ta)!=normalized(tb):return False
    na,nb=normalized(ta),normalized(tb)
    if len(na)>=14 and na==nb:return True
    # Equal summaries are reliable even when the vendor truncates its headline.
    ca,cb=normalized(a.get('summary','')),normalized(b.get('summary',''))
    if len(ca)>=40 and ca==cb:return True
    # Identical announced investment headline clause identifies the event; keep
    # differing detail amounts as unresolved source claims within the event.
    clauses=lambda t:re.split(r'\s+(?=\d)|[；;]',t)[0]
    if re.search(r'投资\d+(?:\.\d+)?万亿韩元',ta) and normalized(clauses(ta))==normalized(clauses(tb)):return True
    # Explicit bilingual subject pair + same product + same action, never just AI.
    def signature(t):
        return (term_match(t,'Meta'),bool(re.search(r'傅利叶|Fourier',t,re.I)),bool(re.search(r'人形机器人|humanoid',t,re.I)),bool(re.search(r'合作|partner|collaborat',t,re.I)))
    return all(signature(ta)) and all(signature(tb))


def research_priority(reports,themes):
    text=' '.join(r['title']+' '+r.get('summary','') for r in reports)
    low=re.search(r'改名|更名|关系满意度|亲密关系|地外|extraterrestrial|conference call|电话会议安排|ETF.*涨',text,re.I)
    material=re.search(r'FOMC|利率决议|供不应求|供给偏紧|供应偏紧|量产|批量供货|订单|业绩|产能|投资\d|资本开支|收购|中标|合作.*机器人|robot.*partner|财报|涨价|降价',text,re.I)
    focus=bool(not low and material)
    return dict(level='focus' if focus else 'brief',reason='涉及政策、供需或公司经营变化，需补证与比较' if focus else '一般快讯或弱业务线索，先保留事实',version=VERSION)


def conflicts(reports):
    # Flag differing amounts with the same unit; do not resolve them by voting.
    byunit={}
    for r in reports:
        text=r['title']+' '+r.get('summary','')
        for number,unit in re.findall(r'(\d+(?:\.\d+)?)\s*(万亿韩元|万亿美元|万亿元|亿元|亿美元|万亿)',text):
            if unit=='万亿':unit='万亿韩元' if '韩元' in text else '万亿（币种未明确）'
            byunit.setdefault(unit,{}).setdefault(r['source'],set()).add(number)
    result=[]
    for unit,sources in byunit.items():
        if len(sources)>1 and len({tuple(sorted(x)) for x in sources.values()})>1:
            result.append(dict(kind='amount_scope',text='来源金额或统计范围不同，需核对原始文件，尚未判定哪项正确',unit=unit,values={s:sorted(v) for s,v in sources.items()}))
    return result


def validate_research(data,evidence):
    if not isinstance(data,dict) or data.get('level') not in ('focus','brief'):raise ValueError('invalid research level')
    for field in ('new_facts','mechanisms'):
        rows=data.get(field)
        if not isinstance(rows,list) or len(rows)>8:raise ValueError('invalid research claims')
        for row in rows:
            if not isinstance(row,dict) or not isinstance(row.get('text'),str) or not 0<len(row['text'])<=1000:raise ValueError('invalid claim')
            refs=row.get('evidence_ids')
            if not isinstance(refs,list) or not refs or any(not isinstance(x,str) or x not in evidence for x in refs):raise ValueError('unreferenced claim')
            if field=='mechanisms' and row.get('kind') not in ('fact','inference'):raise ValueError('unmarked inference')
            if field=='mechanisms' and row.get('kind')=='fact' and re.search(r'意味着|显示|说明|反映|有望|可能',row['text']):raise ValueError('interpretation marked fact')
            if re.search(r'由.*(?:1[—–\-至]9月|前三季度).*(?:升至|降至|增加至|下降至).*第三季度',row['text']):raise ValueError('incomparable periods')
    for field in ('baseline','market_context'):
        if not isinstance(data.get(field),str) or not 0<len(data[field])<=1500:raise ValueError('missing research baseline')
    for field in ('counterarguments','watchpoints'):
        if not isinstance(data.get(field),list) or not 1<=len(data[field])<=8 or not all(isinstance(s,str) and 0<len(s)<=700 for s in data[field]):raise ValueError('missing research conditions')
    return data


class ResearchStore:
    def init_research(self):
        with self.store.connect() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS news_event_links(child TEXT PRIMARY KEY,parent TEXT,created TEXT,reason TEXT);
            CREATE INDEX IF NOT EXISTS news_link_parent ON news_event_links(parent,created);
            CREATE TABLE IF NOT EXISTS news_enrichment(id INTEGER PRIMARY KEY,source TEXT,source_id TEXT,report_hash TEXT,created TEXT,payload TEXT);
            CREATE INDEX IF NOT EXISTS news_enrichment_lookup ON news_enrichment(source,source_id,created);
            ''')

    def event_roots(self,cutoff):
        with self.store.connect() as db:links={r['child']:r['parent'] for r in db.execute('SELECT * FROM news_event_links WHERE created<=?',(cutoff,))}
        def root(eid):
            seen=set()
            while eid in links and eid not in seen:seen.add(eid);eid=links[eid]
            return eid
        return {eid:root(eid) for eid in links}

    def merge_events(self,now=None):
        from .news import normalize_time
        from .store import now_iso
        stamp=normalize_time(now or now_iso());cutoff=normalize_time(datetime.fromisoformat(stamp)-timedelta(days=3))
        with self.store.connect() as db:
            rows=db.execute('SELECT n.* FROM news_items n JOIN (SELECT source,source_id,MAX(id) id FROM news_items WHERE seen<=? AND published>=? GROUP BY source,source_id) v ON n.id=v.id ORDER BY n.id',(stamp,cutoff)).fetchall()
        # Skip unchanged input within a process; links remain durable on restart.
        watermark=max((r['id'] for r in rows),default=0)
        if getattr(self,'_merged_watermark',None)==watermark:return 0
        roots=self.event_roots(stamp);buckets={};seen=set();merged=0
        for row in rows:
            eid=roots.get(row['event_id'],row['event_id']);item=json.loads(row['payload'])
            if eid in seen:continue
            seen.add(eid)
            match=re.match(r'^【([^】]+)】',item['title']);title=match[1] if match else item['title']
            key='meta-fourier' if term_match(title,'Meta') and re.search(r'傅利叶|Fourier',title,re.I) else normalized(title)[:4]
            for other,previous in reversed(buckets.get(key,[])):
                if same_event(item,previous):
                    with self.store.connect() as db:
                        cur=db.execute('INSERT OR IGNORE INTO news_event_links VALUES(?,?,?,?)',(eid,other,stamp,'news-v3：相近时间、同一主体事实与状态'))
                    merged+=cur.rowcount;roots[eid]=other;break
            else:buckets.setdefault(key,[]).append((eid,item))
        self._merged_watermark=watermark
        return merged

    def save_enrichment(self,report,payload,now=None):
        from .news import normalize_time,digest
        from .store import now_iso
        key=digest({k:report.get(k) for k in ('title','summary','url')})
        with self.store.connect() as db:
            old=db.execute('SELECT payload FROM news_enrichment WHERE source=? AND source_id=? AND report_hash=? ORDER BY id DESC LIMIT 1',(report['source'],report['source_id'],key)).fetchone()
            if old and json.loads(old[0])==payload:return
            db.execute('INSERT INTO news_enrichment(source,source_id,report_hash,created,payload) VALUES(?,?,?,?,?)',(report['source'],report['source_id'],key,normalize_time(now or now_iso()),json.dumps(payload,ensure_ascii=False)))

    def enrichments(self,reports,cutoff):
        from .news import digest
        result=[]
        with self.store.connect() as db:
            for report in reports:
                if 'source' not in report or 'source_id' not in report:continue
                key=digest({k:report.get(k) for k in ('title','summary','url')})
                row=db.execute('SELECT * FROM news_enrichment WHERE source=? AND source_id=? AND report_hash=? AND created<=? ORDER BY id DESC LIMIT 1',(report['source'],report['source_id'],key,cutoff)).fetchone()
                if row:result.append(dict(id=row['id'],captured_at=row['created'],source=row['source'],**json.loads(row['payload'])))
        return result
