import argparse
from pathlib import Path
from .store import Store
from .service import Collector


def main():
    parser = argparse.ArgumentParser(description='公开板块数据采集与历史回补')
    parser.add_argument('command',choices=['collect','backfill','probe','import-browser','recover','collect-ths','collect-lhb','collect-etf'])
    parser.add_argument('--quotes-only',action='store_true',help='collect-ths 只回补指数报价和日线，成分由后续批次补齐')
    parser.add_argument('--date',help='龙虎榜交易日期 YYYY-MM-DD，默认今日')
    parser.add_argument('--directory',type=Path,help='浏览器公开 JSON 响应归档目录')
    parser.add_argument('--limit',type=int,default=0,help='回补板块数上限，0 表示全部')
    parser.add_argument('--days',type=int,default=1,help='宽基 ETF 回补交易日数（1–60）')
    args=parser.parse_args()
    store=Store(Path(__file__).resolve().parents[1]/'runtime/market.sqlite3')
    if args.command=='collect-etf':
        from .etf_tracker import collect
        from .store import TZ
        from datetime import datetime
        try:
            results=collect(store,args.date or datetime.now(TZ).date().isoformat(),args.days)
        except ValueError as exc:parser.error(str(exc))
        for row in results:print(row)
        return
    if args.command=='collect-lhb':
        from datetime import date, datetime
        from .lhb import collect
        from .store import TZ
        now=datetime.now(TZ)
        day=date.fromisoformat(args.date) if args.date else now.date()
        if day>now.date() or (day==now.date() and now.strftime('%H:%M')<'15:30'):
            parser.error('请在交易日盘后采集龙虎榜')
        print(collect(store,day.isoformat()))
        return
    if args.command=='import-browser':
        if not args.directory:
            parser.error('import-browser 需要 --directory')
        from .import_browser import import_archive
        print(import_archive(store,args.directory))
        return
    collector=Collector(store)
    if args.command=='collect-ths':
        from .collect_ths import collect_ths
        collect_ths(collector,limit=args.limit,members=not args.quotes_only)
        return
    if args.command=='probe':
        for kind in ('industry','concept'):
            try:
                rows=collector.provider.boards(kind)
                print(kind,len(rows),rows[:2])
            except Exception as exc:
                print(kind,type(exc).__name__,str(exc))
        return
    if args.command=='recover':
        from .recovery import recover
        recover(collector,limit=args.limit)
        return
    active=collector.cycle(initial=True)
    if args.command=='backfill':
        if not active:
            print('仅盘后回补：请在已验证的交易日 15:30 后运行；已有归档仍可查看。')
            return
        attempts=0
        while True:
            collector.enrich_next=0
            if not collector.enrich_one():
                break
            attempts+=1
            if args.limit and attempts>=args.limit*2:
                break
            if sum(collector.failures.values())>=10:
                print('连续数据错误较多，停止回补；具体错误见 /api/status')
                break
        for kind in ('industry','concept'):
            collector.job('board_'+kind,lambda k=kind:collector.boards(k),0)
    print('采集结束；各来源结果见 /api/status 与 runtime/market.sqlite3')


if __name__=='__main__':
    main()
