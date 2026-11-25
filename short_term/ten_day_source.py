"""Read-only interval ranking adapter, confined to the independent preview."""
from datetime import date, datetime

from backend.sources.kaipanla import KaipanlaSource, checked, code_value, number, source_date
from backend.sources.base import SourceError
from backend.store import TZ


class PreviewSource(KaipanlaSource):
    @checked
    def fetch_completed_limit_group(self, day, group):
        # Historical endpoint uses PidType=5 for ALL five-board-and-higher stocks.
        if not 1<=group<=5 or day>datetime.now(TZ).date().isoformat():
            raise SourceError('历史分组或日期无效')
        def fetch(index):
            return self._post('https://apphis.longhuvip.com/w1/api/index.php',{
                'c':'HisHomeDingPan','a':'DailyLimitPerformance','Day':day,'Index':index,
                'st':self.page_size,'Order':0,'Type':4,'PidType':group,'apiv':'w31','VerSion':'5.7.0.12'})
        def extract(payload):
            source_date(payload['info'][1],day)
            return payload['info'][0]
        raw=self._pages(fetch,extract)
        rows=[]
        for r in raw:
            boards=int(r[15])
            if (group<5 and boards!=group) or (group==5 and not 5<=boards<=30):
                raise SourceError('历史分组与实际连板高度不符')
            source_date(datetime.fromtimestamp(int(r[4]),TZ).date().isoformat(),day)
            rows.append({'code':code_value(r[0]),'name':str(r[1]),'boards':boards})
        return rows

    @checked
    def fetch_ten_day_leaders(self, day):
        return self.fetch_interval_leaders(day, 10)

    @checked
    def fetch_interval_leaders(self, day, window=20, directory=False):
        if window not in (10, 20):
            raise SourceError('不支持的排名区间')
        # The history is a sequence of market sessions, not calendar weekdays.
        cached = getattr(self, '_ranking_calendar', None)
        if not cached or cached[0] != day:
            history = self._post('https://apphis.longhuvip.com/w1/api/index.php',
                {'c': 'HisHomeDingPan', 'a': 'ChangeStatistics', 'Index': 0, 'st': 30})
            days = [row['Day'] for row in history['info']]
            if days and days[0]<day and day==datetime.now(TZ).date().isoformat():
                # Historical statistics can lag the live session. The live endpoint validates Day.
                # Never add weekdays by guessing, and never synthesize a past missing session.
                self.fetch_emotion(day)
                days=[day]+days
            if len(days) < window or days != sorted(set(days), reverse=True) or days[0] != day:
                raise SourceError('最近交易日序列缺失、重复或未更新，无法确认10日区间')
            for value in days:
                date.fromisoformat(value)
            cached = (day, days)
            self._ranking_calendar = cached
        days = cached[1][:window]
        if len(days) != window:
            raise SourceError('交易日数量不足')
        start = days[-1]
        payload = self._post('https://apphwshhq.longhuvip.com/w1/api/index.php', {
            'c': 'StockLineData', 'a': 'GetInterviewsByDateStock',
            'DStart': start, 'DEnd': day, 'Order': 1, 'Type': 2,
            'Index': 0, 'st': 6000 if directory else 6, 'apiv': 'w41', 'VerSion': '5.20.0.8'})
        for key, expected in [('DStart', start), ('DEnd', day), ('Date', day), ('Day', day)]:
            if key in payload:
                source_date(payload[key], expected)
        raw = payload['List']
        count = int(payload['Count'])
        if not isinstance(raw, list) or count < 5 or len(raw) != (count if directory else min(6, count)):
            raise SourceError('区间榜返回不足或数量不符，无法确认前5名')
        rows = []
        for row in raw:
            price, change = number(row[2]), number(row[3])
            if price <= 0 or change <= -100:
                raise SourceError('区间榜数值异常')
            rows.append({'code': code_value(row[0]), 'name': str(row[1]),
                'price': price, 'return_'+str(window)+'d': change, 'theme': str(row[10]),
                'rank': len(rows) + 1})
        values = [r['return_'+str(window)+'d'] for r in rows]
        if len({r['code'] for r in rows}) != len(rows):
            raise SourceError('区间榜证券重复，不能核验全集')
        locally_sorted = False
        if values != sorted(values, reverse=True):
            if not directory:
                raise SourceError('区间榜未按涨幅降序返回，局部页面不能证明前五')
            # With the ENTIRE unique universe verified, vendor tail inversions are harmless:
            # sort the actual returned values, retain vendor positions, never alter prices/returns.
            rows.sort(key=lambda r:(-r['return_'+str(window)+'d'],r['code']))
            for rank, row in enumerate(rows,1):
                row['vendor_position']=row['rank'];row['rank']=rank
            locally_sorted = True
        return {'rows': rows[:5], 'directory': rows if directory else [], 'start': start, 'end': day,
            'trading_days': list(reversed(days)), 'universe_count': count,
            'scope': '开盘啦全市场区间榜，含ST，沿用供应商股票范围',
            'basis': f'含首尾{window}个交易日，沿用开盘啦区间涨幅口径；复权口径未单独披露',
            'sorting_basis': '完整目录按返回涨幅本地重排，保留原位置' if locally_sorted else '供应商顺序已核验',
            'source_time': None, 'raw': payload}
