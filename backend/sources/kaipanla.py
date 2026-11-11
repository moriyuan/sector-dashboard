"""开盘啦：题材强度榜、单板块强度历史、每日涨停复盘（题材分组+原因全文）、板块连板梯队。

实测结论（2026-11）：
- RealRankingInfo（强度榜）仅保留最近约 2 个交易日，需每日定时落库；
- GetPlate_Info_QJ（单板块强度）支持 2017 年以来的历史，供回补；
- DailyLimitResumption（复盘）只在实时主机返回当日数据，历史请求会静默返回当日数据，
  历史题材分组改用 FuPanLa/GetYTFP_BKHX（仅梯队名单与连板标注，无原因全文）。
匿名可用，安卓端公共参数即可。
"""
import uuid
from datetime import datetime

from .base import SourceClient
from ..store import TZ

_HIS = 'https://apphis.longhuvip.com/w1/api/index.php'   # 历史
_RT = 'https://apphwhq.longhuvip.com/w1/api/index.php'   # 实时/当日复盘
_HQ = 'https://apphq.longhuvip.com/w1/api/index.php'     # 行情强度榜


def _clock(raw) -> str | None:
    try:
        return datetime.fromtimestamp(int(raw), TZ).strftime('%H:%M:%S')
    except (TypeError, ValueError, OSError):
        return None


class KaipanlaSource(SourceClient):
    name = 'kaipanla'
    ua = 'Dalvik/2.1.0 (Linux; U; Android 9; SHARK PRS-A0 Build/PQ3A.190605.01141736)'

    def _post(self, url: str, payload: dict):
        data = {'PhoneOSNew': '1', 'DeviceID': uuid.uuid4().hex, 'VerSion': '5.21.0.2', **payload}
        return self.post_json(url, data=data,
                              headers={'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8'})

    def fetch_strength_board(self, day: str) -> list[dict]:
        """全板块强度榜 [{'code','name','strength','change'}]，按强度降序；仅支持最近约 2 个交易日。"""
        payload = self._post(_HQ, {'c': 'ZhiShuRanking', 'a': 'RealRankingInfo', 'apiv': 'w35',
                                   'ZSType': '7', 'st': '80', 'Index': '0', 'Order': '1', 'Date': day})
        rows = []
        for rank, r in enumerate(payload.get('list') or [], 1):
            try:
                rows.append({'code': r[0], 'name': r[1], 'strength': float(r[2]),
                             'change': float(r[3]), 'rank': rank})
            except (IndexError, TypeError, ValueError):
                continue
        return rows

    def fetch_sector_strength(self, code: str, day: str) -> dict | None:
        """单板块历史强度 {'code','strength','rank','date'}；无数据返回 None。"""
        payload = self._post(_HIS, {'a': 'GetPlate_Info_QJ', 'c': 'ZhiShuRanking', 'apiv': 'w42',
                                    'Date': day, 'PlateID': code})
        row = payload.get('List') or []
        if len(row) < 2 or payload.get('errcode') != '0':
            return None
        try:
            return {'code': code, 'rank': int(row[0]), 'strength': float(row[1]),
                    'date': payload.get('Date') or day}
        except (TypeError, ValueError):
            return None

    def fetch_resumption(self, day: str) -> dict:
        """当日涨停复盘：{'stats':{...}, 'themes':[{'code','name','stocks':[...]}]}。
        仅当日有效（实时主机忽略日期参数），调用方须校验返回日期。"""
        payload = self._post(_RT, {'a': 'GetPlateInfo_w38', 'st': '100', 'c': 'DailyLimitResumption',
                                   'Index': '0', 'apiv': 'w42', 'Day': day})
        nums = payload.get('nums') or {}
        stats = {'limit_up': nums.get('ZT'), 'limit_down': nums.get('DT'),
                 'broken_ratio': nums.get('ZBL'), 'up': nums.get('SZJS'), 'down': nums.get('XDJS')}
        themes = []
        for t in payload.get('list') or []:
            stocks = []
            for s in t.get('StockList') or []:
                try:
                    stocks.append({'code': s[0], 'name': s[1],
                                   'change': float(s[4]) if s[4] not in (None, '') else None,
                                   'first_time': _clock(s[6]),
                                   'boards': int(s[7]) if s[7] not in (None, '') else None,
                                   'seal_fund': s[8],
                                   'board_label': s[9] or None,
                                   'tags': [x for x in str(s[11] or '').split('、') if x],
                                   'reason_full': str(s[17]).strip() if len(s) > 17 and s[17] else None})
                except (IndexError, TypeError, ValueError):
                    continue
            themes.append({'code': t.get('ZSCode'), 'name': t.get('ZSName'),
                           'explain': (t.get('TCExplain') or '').strip() or None, 'stocks': stocks})
        return {'stats': stats, 'themes': themes}

    def fetch_sector_ladder(self, day: str) -> list[dict]:
        """历史板块连板梯队 [{'code','name','count','stocks':[{'code','name','board_label'}]}]。"""
        payload = self._post(_HIS, {'a': 'GetYTFP_BKHX', 'c': 'FuPanLa', 'apiv': 'w42', 'Date': day})
        themes = []
        for t in payload.get('List') or []:
            stocks = []
            for tier in t.get('TD') or []:
                for s in tier.get('Stock') or []:
                    stocks.append({'code': s.get('StockID'), 'name': s.get('StockName'),
                                   'board_label': (s.get('Tips') or '').strip() or None})
            themes.append({'code': t.get('ZSCode'), 'name': t.get('ZSName'),
                           'count': int(t.get('Count') or len(stocks)),
                           'explain': (t.get('Reason') or '').strip() or None, 'stocks': stocks})
        return themes
