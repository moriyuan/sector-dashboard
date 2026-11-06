"""Frozen, evidence-bound group estimates; no fitting or vendor calls on API reads."""
import json
import math
from decimal import Decimal, ROUND_HALF_UP
from functools import lru_cache
from pathlib import Path
from .rankings import at_limit


@lru_cache(maxsize=1)
def model():
    return json.loads((Path(__file__).parent/'models/relay-v1.json').read_text())


def model_info():
    return {k:v for k,v in model().items() if k not in ('groups','evidence_hashes')}


def estimate(row,day,mode):
    m=model()
    result=dict(probability=None,status='unconfirmed',version=m['version'],retrospective=day<m['released_at'][:10])
    def stop(status):return dict(result,status=status)
    if mode!='close':return stop('close_only')
    if row.get('is_st') or 'ST' in row.get('name','').upper():return stop('excluded_st')
    if row.get('limit_status')=='not_sealed':return stop('not_sealed')
    if row.get('limit_status')=='source_conflict':return stop('source_conflict')
    if row.get('limit_status') not in ('sealed','sealed_height_unknown') or at_limit(row) is not True:return result
    height=row.get('boards')
    if not isinstance(height,(int,float)) or isinstance(height,bool) or not math.isfinite(height) or height<1 or int(height)!=height:return stop('unknown_height')
    height=int(height)
    if day<m['label_end']:return stop('historical_unavailable')
    if day>m['expires']:return stop('model_expired')
    code=row.get('code','')
    if not code.startswith(('0','3','6')):return stop('unsupported_regime')
    tier='20' if code.startswith(('30','688','689')) else '10'
    previous=row.get('pre_close')
    if not isinstance(previous,(int,float)) or not math.isfinite(previous) or previous<=0:return result
    expected=(Decimal(str(previous))*(Decimal('1.2') if tier=='20' else Decimal('1.1'))).quantize(Decimal('.01'),rounding=ROUND_HALF_UP)
    if Decimal(str(row['upper_limit'])).quantize(Decimal('.01'))!=expected:return stop('unsupported_regime')
    group=tier+':'+str(min(height,4));stats=m['groups'].get(group)
    if not stats or stats['n']<m['min_samples'] or stats['days']<m['min_days']:return stop('insufficient_samples')
    if stats['audit_n']<m['min_audit']:return stop('insufficient_validation')
    return dict(result,**stats,status='estimated',group=group)
