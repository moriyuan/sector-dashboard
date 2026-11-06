"""Public daily-summary adapters. Fixed endpoints, explicit dates and bounded paging."""
import re
from datetime import date, datetime, timedelta
from .analytics import number
from .provider import Eastmoney, DataError
from .store import TZ

ANN_URL = 'https://np-anotice-stock.eastmoney.com/api/security/ann'
FIN_URL = 'https://datacenter-web.eastmoney.com/api/data/v1/get'
CHANGE_URL = 'https://push2ex.eastmoney.com/getAllStockChanges'
TYPES = dict(zip(('8201','8202','8193','4','32','64','8207','8209','8211','8213','8215','8204','8203','8194','8','16','128','8208','8210','8212','8214','8216'),
 ('火箭发射','快速反弹','大笔买入','封涨停板','打开跌停板','有大买盘','竞价上涨','高开5日线','向上缺口','60日新高','60日大幅上涨','加速下跌','高台跳水','大笔卖出','封跌停板','打开涨停板','有大卖盘','竞价下跌','低开5日线','向下缺口','60日新低','60日大幅下跌')))
# Supplier units: monetary fields in reported currency, percentages already in percent.
FIELDS = [
 ('TOTALOPERATEREVE','营业总收入','元'),('TOTALOPERATEREVETZ','营收同比','%'),
 ('PARENTNETPROFIT','归母净利润','元'),('PARENTNETPROFITTZ','归母净利润同比','%'),
 ('KCFJCXSYJLR','扣非归母净利润','元'),('KCFJCXSYJLRTZ','扣非归母净利润同比','%'),
 ('NETCASH_OPERATE_PK','经营现金流净额','元'),('NETCASH_INVEST_PK','投资现金流净额','元'),('NETCASH_FINANCE_PK','筹资现金流净额','元'),
 ('EPSJB','基本每股收益','元/股'),('BPS','每股净资产','元/股'),('MGJYXJJE','每股经营现金流','元/股'),
 ('ROEJQ','加权净资产收益率','%'),('XSMLL','销售毛利率','%'),('XSJLL','销售净利率','%'),
 ('TOTAL_ASSETS_PK','总资产','元'),('TOTAL_EQUITY_PK','净资产（含少数股东）','元'),('LIABILITY','总负债','元'),('ZCFZL','资产负债率','%'),
 ('LD','流动比率','倍'),('SD','速动比率','倍'),('CHZZTS','存货周转天数','天'),('YSZKZZTS','应收账款周转天数','天'),
 ('DJD_TOI_YOY','单季营收同比','%'),('DJD_DPNP_YOY','单季归母净利润同比','%'),
 ('DJD_TOI_QOQ','单季营收环比','%'),('DJD_DPNP_QOQ','单季归母净利润环比','%'),
 ('NEWCAPITALADER','资本充足率','%'),('NONPERLOAN','不良贷款率','%'),('NET_INTEREST_MARGIN','净息差','%'),('SOLVENCY_AR','偿付能力充足率','%')]


def stock_code(value):
    return bool(re.fullmatch(r'(?:[03689]\d{5}|4\d{5})',str(value)))


def report_period(title):
    match = re.search(r'(20\d{2})\s*年?\s*(第一季度|一季度|第三季度|三季度|前三季度|半年度|中期|年度|年报)', title)
    if not match: return None
    year, label = match.groups()
    return year + ('-03-31' if label in ('第一季度','一季度') else '-09-30' if label in ('第三季度','三季度','前三季度') else '-06-30' if label in ('半年度','中期') else '-12-31')


def parse_notices(rows, day):
    out = {}
    for r in rows:
        title = str(r.get('title',''))
        # Category 1 includes dividends and resolutions; these are not financial reports.
        if re.search('业绩预告',title): kind = 'forecast'
        elif re.search('业绩快报',title): kind = 'preliminary'
        elif re.search(r'(?:年度|季度|半年度|中期)报告|年报',title) and not re.search('取消|预约|披露时间|问询|提示|董事会|监事会|审议|分配|分红|权益分派|募集资金',title): kind = 'report'
        else: continue
        publication = str(r.get('display_time',''))[:19]
        try: publication = datetime.strptime(publication,'%Y-%m-%d %H:%M:%S').replace(tzinfo=TZ).isoformat()
        except ValueError: publication = None
        notice_day = str(r.get('notice_date',''))[:10]
        actual_day = publication[:10] if publication else notice_day
        if actual_day != day: continue
        art = str(r.get('art_code',''))
        if not re.fullmatch(r'AN\d+',art): raise DataError('公告编号格式变化')
        for c in r.get('codes',[]):
            code = str(c.get('stock_code',''))
            if not c.get('ann_type','').startswith('A') or not stock_code(code): continue
            key = art+':'+code
            out[key] = dict(id=key,code=code,name=c.get('short_name',''),title=title,kind=kind,
                period=report_period(title),day=actual_day,notice_date=notice_day,published_at=publication,
                date_basis='实际发布时间' if publication else '公告落款日（实际发布时间未知）',
                url=f'https://data.eastmoney.com/notices/detail/{code}/{art}.html',source='东方财富公告',
                revision_notice=bool(re.search('修订|更正|补充',title)))
    return list(out.values())


def financial_metrics(row):
    currency = row.get('CURRENCY') or None
    result = [dict(id=key,label=label,value=number(row.get(key)),unit=unit,currency=currency if '元' in unit else None) for key,label,unit in FIELDS]
    # A derived ratio uses compatible cumulative figures; nonpositive profit is not a useful denominator.
    cash, profit = number(row.get('NETCASH_OPERATE_PK')), number(row.get('PARENTNETPROFIT'))
    result.append(dict(id='cash_profit',label='经营现金流 / 归母净利润',value=cash/profit if cash is not None and profit is not None and profit>0 else None,unit='倍',currency=None))
    return result


class DailyProvider(Eastmoney):
    def announcements(self, day):
        end = (date.fromisoformat(day)+timedelta(days=1)).isoformat()
        rows=[]; total=None
        for page in range(1,101):
            obj=self.get(ANN_URL,dict(sr=-1,page_size=100,page_index=page,ann_type='A',client_source='web',f_node=1,s_node=0,begin_time=day,end_time=end))
            if obj.get('success') not in (1,True): raise DataError('公告接口失败')
            data=obj['data']; expected=int(data['total_hits'])
            if total is not None and expected!=total: raise DataError('公告分页变动，等待重试')
            total=expected; chunk=data['list']; rows.extend(chunk)
            if len(rows)>=total: break
            if not chunk: raise DataError('公告分页缺失')
        else: raise DataError('公告超过分页安全上限')
        if len({r['art_code'] for r in rows})!=total: raise DataError('公告分页重复或缺失')
        return parse_notices(rows,day),dict(scanned=total,complete=True,window=[day,end])

    def table(self, report, filters):
        rows=[]; count=None
        for page in range(1,101):
            obj=self.get(FIN_URL,dict(reportName=report,columns='ALL',filter=filters,pageSize=500,pageNumber=page,sortColumns='SECURITY_CODE',sortTypes='1'))
            if not obj.get('success'):
                if obj.get('code')==9201: return []  # Supplier's explicit no-record status.
                raise DataError('财务数据接口失败')
            data=obj.get('result')
            if not data: raise DataError('财务数据结构变化')
            if count is not None and count!=int(data['count']): raise DataError('财务分页变化')
            count=int(data['count']); rows.extend(data['data'] or [])
            if page>=int(data['pages']): break
        else: raise DataError('财务分页超限')
        if len(rows)!=count: raise DataError('财务分页不完整')
        return rows

    def financials(self, notice):
        code,period=notice['code'],notice['period']
        if not stock_code(code) or not period: return None
        date.fromisoformat(period)
        rows=self.table('RPT_F10_FINANCE_MAINFINADATA',f'(SECURITY_CODE="{code}")(REPORT_DATE=\'{period}\')')
        rows=[r for r in rows if r.get('SECURITY_CODE')==code and str(r.get('REPORT_DATE',''))[:10]==period]
        if len(rows)!=1: return None
        row=rows[0]
        # Later restatements must not silently replace the figures attached to an older notice.
        if max(str(row.get('UPDATE_DATE') or '')[:10],str(row.get('NOTICE_DATE') or '')[:10])>notice['notice_date']: return None
        return dict(metrics=financial_metrics(row),currency=row.get('CURRENCY'),period=period,raw=row,
            url=f'https://data.eastmoney.com/stockdata/{code}.html',source='东方财富财务指标',
            source_notice_date=str(row.get('NOTICE_DATE',''))[:10],source_update_date=str(row.get('UPDATE_DATE',''))[:10])

    def peers(self, notice):
        code,period=notice['code'],notice['period']
        own=self.table('RPT_LICO_FN_CPD',f'(SECURITY_CODE="{code}")(REPORTDATE=\'{period}\')')
        if len(own)!=1 or not re.fullmatch(r'BK\d+',str(own[0].get('BOARD_CODE',''))): return None
        industry=own[0]['BOARD_CODE']
        rows=self.table('RPT_LICO_FN_CPD',f'(BOARD_CODE="{industry}")(REPORTDATE=\'{period}\')(NOTICE_DATE<=\'{notice["notice_date"]}\')')
        valid=[r for r in rows if r.get('SECURITY_CODE')!=code and str(r.get('UPDATE_DATE',''))[:10]<=notice['notice_date']]
        from statistics import median
        metrics=[]
        for key,label in [('YSTZ','营收同比'),('SJLTZ','归母净利润同比'),('XSMLL','销售毛利率'),('WEIGHTAVG_ROE','加权净资产收益率')]:
            vals=[number(r.get(key)) for r in valid]; vals=[v for v in vals if v is not None]
            metrics.append(dict(id='peer_'+key,label=label,value=median(vals) if len(vals)>=3 else None,unit='%',n=len(vals)))
        return dict(name=own[0].get('BOARD_NAME') or own[0].get('PUBLISHNAME'),code=industry,period=period,
            count=len(valid),metrics=metrics,rows=valid,source='东方财富同业已披露样本（不含本公司）',
            cutoff_notice_date=notice['notice_date'],url=f'https://data.eastmoney.com/bbsj/{period.replace("-","")[:6]}/yjbb.html')

    def changes(self):
        rows={}; total=None
        # Paging is per type; mixed type requests are inconsistently interpreted by upstream.
        for typ in TYPES:
            seen={}; expected=None
            for page in range(20):
                obj=self.get(CHANGE_URL,dict(type=typ,pageindex=page,pagesize=5000,ut='7eea3edcaed734bea9cbfc24409ed989',dpt='wzchanges'))
                if obj.get('rc')!=0: raise DataError('异动接口失败')
                data=obj.get('data')
                if not isinstance(data,dict) or 'tc' not in data: raise DataError('异动数据不可用')
                current=int(data['tc']); expected=current if expected is None else expected
                if current!=expected: raise DataError('异动分页滚动，等待重试')
                chunk=data.get('allstock') or []
                for r in chunk:
                    if str(r.get('t'))!=typ: raise DataError('异动类型不符')
                    signature=(str(r['c']),str(r['tm']),typ,str(r.get('i','')))
                    seen[signature]=r
                if (page+1)*5000>=expected: break
                if not chunk: raise DataError('异动分页缺失')
            else: raise DataError('异动分页超限')
            if len(seen)!=expected: raise DataError('异动分页重复或缺失')
            rows.update(seen)
        return list(rows.values())
