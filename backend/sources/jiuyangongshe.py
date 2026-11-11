"""韭研公社异动解析：题材板块逻辑 + 个股涨停原因（需要登录 SESSION）。

web 接口要求 token+timestamp 签名头（混淆算法随站点发版轮换），
签名在 node 沙箱中完成（sources/jygs/fetch.mjs），本类只做子进程编排与结果解析。
SESSION 读取顺序：环境变量 JIYAN_SESSION > runtime/jiuyang_session.txt（不入库不进 git）。
"""
import json
import os
import subprocess
from pathlib import Path

from .base import SourceClient, SourceError

_SCRIPT = Path(__file__).resolve().parent / 'jygs' / 'fetch.mjs'
_SESSION_FILE = Path(__file__).resolve().parents[2] / 'runtime' / 'jiuyang_session.txt'


class JiuyangongsheSource(SourceClient):
    name = 'jiuyangongshe'

    def __init__(self, session: str | None = None, timeout: float | None = None,
                 script: Path = _SCRIPT, runner=subprocess.run):
        super().__init__(timeout)
        self._session = session
        self._script = script
        self._runner = runner  # 可注入 fake，便于测试

    @property
    def session(self) -> str:
        if self._session is not None:
            return self._session
        env = os.getenv('JIYAN_SESSION', '').strip()
        if env:
            return env
        try:
            return _SESSION_FILE.read_text(encoding='utf-8').strip()
        except OSError:
            return ''

    def available(self) -> bool:
        return bool(self.session)

    def _call(self, payload: dict):
        if not self.available():
            raise SourceError('jiuyangongshe 未配置 SESSION，渠道停用')
        try:
            proc = self._runner(
                ['node', str(self._script), json.dumps({**payload, 'session': self.session})],
                capture_output=True, text=True, timeout=max(120, self.timeout * 6))
        except (OSError, subprocess.TimeoutExpired) as e:
            raise SourceError(f'jiuyangongshe 签名进程失败: {e}')
        try:
            result = json.loads(proc.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError):
            raise SourceError(f'jiuyangongshe 输出无法解析: {proc.stdout[-200:]} {proc.stderr[-200:]}')
        if not result.get('ok'):
            raise SourceError(f"jiuyangongshe {result.get('error', '未知错误')}")
        return result.get('data')

    def fetch_action_fields(self, day: str) -> list[dict]:
        """当日异动板块 [{'id','name','count','reason'}]；reason 为该题材炒作逻辑。"""
        data = self._call({'op': 'fields', 'date': day}) or []
        return [{'id': r.get('action_field_id'), 'name': r.get('name'),
                 'count': r.get('count') or 0, 'reason': (r.get('reason') or '').strip() or None}
                for r in data if r.get('name') and r.get('action_field_id')]

    def fetch_action_list(self, field_id: str) -> list[dict]:
        """板块下个股异动 [{'code','name','time','board_label','reason'}]；code 为 6 位数字。"""
        data = self._call({'op': 'list', 'field_id': field_id}) or []
        rows = data.get('list') if isinstance(data, dict) else data
        stocks = []
        for r in rows or []:
            info = ((r.get('article') or {}).get('action_info')) or {}
            code = str(r.get('code') or '')
            code = code[2:] if code[:2] in ('sh', 'sz', 'bj') else code
            stocks.append({'code': code, 'name': r.get('name'),
                           'time': info.get('time'),
                           'board_label': info.get('num'),
                           'reason': (info.get('expound') or '').strip() or None})
        return [s for s in stocks if s['code'] and s['name']]
