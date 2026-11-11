"""数据源服务层：每个渠道一个服务类，接口统一、可独立调用与测试。

- base           基类：限速、重试、统一异常
- eastmoney      东方财富涨停池（名单/连板/封板时间/炸板）
- ths            同花顺涨停揭秘（涨停原因/板型/封单）
- kaipanla       开盘啦（当日复盘分组+原因全文、历史梯队、题材强度）
- jiuyangongshe  韭研公社异动解析（题材逻辑+个股原因，需 SESSION，签名走 node 脚本）
"""
from .base import SourceClient, SourceError
from .eastmoney import EastmoneySource
from .ths import ThsSource
from .kaipanla import KaipanlaSource
from .jiuyangongshe import JiuyangongsheSource

__all__ = ['SourceClient', 'SourceError', 'EastmoneySource', 'ThsSource',
           'KaipanlaSource', 'JiuyangongsheSource']
