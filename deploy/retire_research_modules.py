"""Targeted overlay for production and the independently maintained test branch."""
from pathlib import Path
import re
import shutil
import sys

DELETED=('backend/barra.py','backend/quant_price.py','backend/daily.py','backend/daily_sources.py',
 'backend/regulation.py','backend/seat_backtest.py','frontend/src/Barra.tsx','frontend/src/barra.css',
 'frontend/src/DailySummary.tsx','frontend/src/daily-summary.css','frontend/src/RegulationPanel.tsx',
 'frontend/src/SeatBacktest.tsx','frontend/src/seat-backtest.css','tests/test_barra.py','tests/test_quant_price.py',
 'tests/test_daily.py','tests/test_regulation.py','tests/test_seat_backtest_api.py','tests/test_seat_follow_backtest.py',
 'scripts/quant_bulk.py','scripts/quant_history_build.py','scripts/quant_history_fetch.py','scripts/quant_update.py',
 'scripts/quant_verify.py','scripts/package_seat_backtest.py','scripts/seat_follow_backtest.py',
 'deploy/quant-price/sector-quant-update.service','deploy/quant-price/sector-quant-update.timer')

def apply(root, assets=None, archive=None):
 root=Path(root);updates={}
 p=root/'backend/app.py';s=p.read_text()
 s='\n'.join(line for line in s.split('\n') if not any(x in line for x in ('from .barra import','app.include_router(barra_router','from .daily import','app.include_router(daily_router','from .seat_backtest import','app.include_router(seat_backtest_router')))
 if 'from .seat_stocks import' not in s:
  s=s.replace('    def date_value(', '    from .seat_stocks import router as seat_stocks_router\n    app.include_router(seat_stocks_router(store))\n\n    def date_value(')
 updates[p]=s
 p=root/'backend/news_worker.py';s=p.read_text()
 s=re.sub(r' from \.daily import DailyStore, collect_due, analyze_one\n.*?(?= def analysis_loop\(\):)', '', s, flags=re.S)
 s='\n'.join(line for line in s.split('\n') if not any(t in line for t in ('analyze_one(daily,worker,key)','daily_thread=None','daily_thread=threading.Thread','if daily_thread:daily_thread.join')))
 if 'daily_loop' in s or 'from .daily' in s:raise ValueError('unhandled daily worker')
 updates[p]=s
 for name in ('main.tsx','ResearchLayout.tsx','Lhb.tsx'):
  p=root/'frontend/src'/name
  if not p.exists() and (root/'frontend/dist/index.html').exists():continue
  s=p.read_text()
  if name=='main.tsx':
   s='\n'.join(line for line in s.split('\n') if not any(t in line for t in ('import Barra from','import DailySummary from','{barraView&&<Barra','{dailyView&&<DailySummary')))
   s=s.replace("import SeatBacktest from './SeatBacktest';", "import SeatStocks from './SeatStocks';")
   s=s.replace("const seatBacktestView=standalone==='#seat-backtest', dailyView=standalone==='#daily', barraView=standalone==='#barra', resonanceView=", "const seatStocksView=standalone==='#seat-stocks'||standalone==='#seat-backtest', resonanceView=")
   s=s.replace('seatBacktestView||dailyView||barraView||','seatStocksView||')
   s=s.replace('seatBacktest={seatBacktestView}', 'seatStocks={seatStocksView}')
   s=s.replace(' daily={dailyView}', '').replace(' barra={barraView}', '').replace('||dailyView','')
   s=s.replace('{seatBacktestView&&<SeatBacktest/>}','{seatStocksView&&<SeatStocks/>}')
   if any(t in s for t in ('barraView','dailyView','SeatBacktest','DailySummary')):raise ValueError('unhandled page reference')
  elif name=='ResearchLayout.tsx':
   s=s.replace("['seat-backtest','游资回测']", "['seat-stocks','游资股票']").replace("['barra','因子收益'],",'').replace("['daily','每日汇总'],",'')
   s=s.replace('seatBacktest','seatStocks').replace("id==='seat-backtest'","id==='seat-stocks'")
   s=s.replace('daily=false,barra=false,','').replace('daily?:boolean;barra?:boolean;','').replace('||daily||barra','').replace(',daily,barra,',',')
   s=s.replace("barra?id==='barra':daily?id==='daily':",'')
   if re.search(r'\b(daily|barra)\b',s):raise ValueError('unhandled navigation reference')
  else:
   s=s.replace('lhb-backtest-link','lhb-stocks-link').replace('href="#seat-backtest">查看历史跟随回测','href="#seat-stocks">查看全部游资参与股票')
  updates[p]=s
 p=root/'frontend/src/lhb.css'
 if p.exists():updates[p]=p.read_text().replace('lhb-backtest-link','lhb-stocks-link')
 p=root/'scripts/resonance_build.py'
 if p.exists():updates[p]=p.read_text().replace('from backend.quant_price import daily_data','from backend.daily_prices import daily_data')
 for p,s in updates.items():p.write_text(s)
 for name in DELETED:(root/name).unlink(missing_ok=True)
 for name in DELETED:
  p=root/name
  if p.suffix=='.py':
   for cache in (p.parent/'__pycache__').glob(p.stem+'.*.pyc'):cache.unlink()
 if assets:
  assets=Path(assets)
  for name in ('backend/seat_stocks.py','backend/daily_prices.py','frontend/src/SeatStocks.tsx',
               'frontend/src/seat-stocks.css','scripts/archive_seat_stocks.py'):
   target=root/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(assets/name,target)
 if archive:
  target=root/'research/seat-stocks-v1';target.parent.mkdir(parents=True,exist_ok=True)
  if target.is_symlink():target.unlink()
  elif target.exists():raise ValueError('archive directory exists; do not overwrite')
  target.symlink_to(Path(archive),target_is_directory=True)

if __name__=='__main__':apply(*sys.argv[1:])
