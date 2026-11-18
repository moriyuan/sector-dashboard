"""Preserve all collected disclosure associations before retiring return research."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.seat_stocks import archive, raw_events, current_events
from backend.store import Store

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--history',type=Path)
    parser.add_argument('--database',type=Path,action='append',default=[])
    args=parser.parse_args()
    if args.history: archive(args.output, raw_events(json.loads(args.history.read_text())))
    for path in args.database:
        if not path.is_file(): raise ValueError('source database missing')
        archive(args.output, current_events(Store(path)))
