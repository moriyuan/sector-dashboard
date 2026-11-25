"""Verified 801225 membership. Array positions apply only to the W8 schema."""
from datetime import date
from backend.sources.kaipanla import code_value, number
from .ten_day_source import PreviewSource


class WindSource(PreviewSource):
    def wind(self, expected=None):
        rows, seen, day, total, stocks = [], set(), None, None, None
        for offset in range(0, 1001, 60):
            p = self._app('ZhiShuRanking', 'ZhiShuStockList_W8', PlateID='801225',
                Date='', RStart='0925', REnd='1500', Index=offset, st=60, Type=6,
                Order=1, TSZB=0, old=1, IsZZ=0, IsKZZType=0, TSZB_Type=0,
                filterType=0, apiv='w42', VerSion='5.21.0.2')
            days = p.get('Day')
            if not isinstance(days, list) or len(days) != 1:
                raise ValueError('风向标交易日缺失')
            current = date.fromisoformat(days[0]).isoformat()
            if expected and current != expected:
                raise ValueError('风向标交易日与市场交易日不一致')
            if day and day != current:
                raise ValueError('风向标分页跨日')
            day = current
            raw = p.get('list')
            if not isinstance(raw, list):
                raise ValueError('风向标列表缺失')
            if total is None:
                total = int(p['Count']); stocks = p.get('Stocks')
                if not 1 <= total <= 1000:
                    raise ValueError('风向标空池或数量异常，保留上次名单')
            if not raw:
                if len(rows) != total:
                    raise ValueError('风向标分页提前结束')
                break
            for r in raw:
                if not isinstance(r, list) or len(r) < 24:
                    raise ValueError('风向标字段结构异常')
                code = code_value(r[0])
                if code in seen or not isinstance(r[1], str) or not r[1].strip():
                    raise ValueError('风向标重复证券或名称缺失')
                price, change = number(r[5]), number(r[6])
                if price <= 0 or change <= -100:
                    raise ValueError('风向标价格异常')
                seen.add(code)
                rows.append(dict(code=code, name=r[1], theme=str(r[4]), price=price,
                    change=change, limit_label=str(r[23]), quote_day=day,
                    source_time=None, time_basis='名单报价，未提供逐股行情时点'))
            if len(rows) >= total:
                if len(rows) != total:
                    raise ValueError('风向标数量不符')
                break
        if len(rows) != total or not isinstance(stocks, list) or set(stocks) != seen or len(stocks) != total:
            raise ValueError('风向标总数与Stocks成员不一致')
        return dict(day=day, rows=rows, total=total, source='开盘啦801225',
            complete=True, verified=True, scope='已核验801225池；四分类并集未单独核验')
