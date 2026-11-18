"""Shared archived daily-price format used by index resonance; no factor estimation."""
import json
import numpy as np

DTYPE = np.dtype([('code','<i4'),('status','i1'),('st','i1'),('industry','U5')] +
    [(k,'<f8') for k in ('open','high','low','close','preclose','volume','amount','turn','ret','total_cap','float_cap','pb')])

def daily_data(path):
    with np.load(path,allow_pickle=False) as inp:
        data=inp['stocks'];metadata=json.loads(str(inp['meta']))
    if data.dtype!=DTYPE or metadata['day']!=path.stem or np.any(np.diff(data['code'])<=0):
        raise ValueError(f'invalid input partition {path.stem}')
    return data,metadata
