import argparse
from pathlib import Path
from .store import Store
from .service import Collector


def main():
    parser = argparse.ArgumentParser(description='公开板块数据采集与历史回补')
    parser.add_argument('command',choices=['collect','backfill','probe','import-browser','recover'])
    parser.add_argument('--directory',type=Path,help='浏览器公开 JSON 响应归档目录')
    parser.add_argument('--limit',type=int,default=0,help='回补板块数上限，0 表示全部')
    args=parser.parse_args()
    store=Store(Path(__file__).resolve().parents[1]/'runtime/market.sqlite3')
    if args.command=='import-browser':
        if not args.directory:
            parser.error('import-browser 需要 --directory')
        from .import_browser import import_archive
        print(import_archive(store,args.directory))
        return
    collector=Collector(store)
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
