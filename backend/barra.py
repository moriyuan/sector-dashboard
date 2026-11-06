"""Daily, point-in-time A-share cross-sectional factor returns (percentage points)."""
from bisect import bisect_left
from collections import Counter
from datetime import date
from functools import lru_cache
from typing import Literal
import json
import math
import re

from fastapi import APIRouter
import numpy as np

from .analytics import hs_stock, number, valid_stock

VERSION = "cn-close-style-v1"
STYLES = ("规模", "账面市值比", "换手活跃度", "短期反转")
QUOTE = re.compile(r'v_(?:sh|sz)(\d{6})="([^"]*)"')
RAW_PREFIX = "public_raw:https://qt.gtimg.cn/q="


def archived_valuations(store, previous, day, prior):
    """Recover legacy fields from the matching stock quote archived before T opens."""
    result = {}
    required = {r["code"]: r for r in prior if r.get("source") == "腾讯财经"}
    if not required:
        return result
    with store.connect() as db:
        records = db.execute(
            """SELECT id,payload FROM observations WHERE key>=? AND key<?
               AND observed_at>=? AND observed_at<? ORDER BY id DESC""",
            (RAW_PREFIX, RAW_PREFIX[:-1] + ">", previous + "T15:00", day + "T09:30"))
        for record in records:
            for code, raw in QUOTE.findall(json.loads(record["payload"])["text"]):
                if code not in required:
                    continue
                fields = raw.split("~")
                prior_quote = required[code]
                stamp = prior_quote["source_time"][:19].translate(str.maketrans("", "", "-T:"))
                if (len(fields) <= 46 or fields[2] != code or fields[30] != stamp
                        or number(fields[3]) != prior_quote["price"]):
                    continue
                result[code] = (number(fields[44]), number(fields[46]), record["id"])
                del required[code]
            if not required:
                break
    return result


def fit(rows, universe, styles=STYLES):
    """Solve joint WLS, then identify country/industry via a cap-neutral constraint."""
    counts = Counter(r[0] for r in rows)
    sample = [r for r in rows if counts[r[0]] >= 5]
    industries = sorted({r[0] for r in sample})
    n = len(sample)
    result = dict(n=n, universe=universe, industry_count=len(industries),
                  coverage=n / universe if universe else None, factors=[])
    if len(industries) < 2 or n < max(100, 3 * (len(industries) + len(styles))) or n < .6 * universe:
        return dict(result, reason="回归样本、行业数或覆盖率不足")
    industry, cap, change, exposures = zip(*sample)
    cap = np.asarray(cap)
    y = np.asarray(change)
    x = np.asarray(exposures,dtype=float)
    x = np.clip(x, *np.quantile(x, [.01, .99], axis=0))
    x -= np.average(x, weights=cap, axis=0)
    sd = x.std(axis=0)
    if np.any(sd < 1e-10):
        return dict(result, reason="风格暴露缺少横截面差异")
    x /= sd
    groups = np.array(industry)[:, None] == np.array(industries)
    design = np.column_stack((groups, x))
    weight = np.sqrt(cap / cap.max())
    root = np.sqrt(weight)
    beta, _, rank, singular = np.linalg.lstsq(design * root[:, None], y * root, rcond=None)
    if rank < design.shape[1] or singular[0] / singular[-1] > 1e8:
        return dict(result, reason="暴露矩阵共线，无法区分因子")
    k = len(industries)
    market = float(np.average(beta[:k], weights=cap @ groups))
    factors = [dict(group="市场", name="市场", value=market)]
    factors += [dict(group="风格", name=name, value=float(value)) for name, value in zip(styles, beta[k:])]
    factors += [dict(group="行业", name=name, value=float(value-market), n=counts[name])
                for name, value in zip(industries, beta[:k])]
    total = np.sum(weight * (y - np.average(y, weights=weight)) ** 2)
    residual = np.sum(weight * (y - design @ beta) ** 2)
    return dict(result, factors=factors, r2=float(1-residual/total) if total > 0 else None)


def quotes_on(rows, day):
    return {r["code"]: r for r in rows if hs_stock(r) and valid_stock(r)
            and (r.get("source_time") or "")[:10] == day and r["source_time"][11:16] >= "15:00"}


def estimate(store, day, previous, prior_id, current_id):
    prior = store.snapshot("gainers", previous, "close", prior_id)["payload"]["rows"]
    latest = store.snapshot("gainers", day, "close", current_id)["payload"]["rows"]
    eligible = [r for r in quotes_on(prior, previous).values() if "ST" not in r["name"].upper()]
    current = quotes_on(latest, day)
    legacy = archived_valuations(store, previous, day, [r for r in eligible if "float_cap_yi" not in r])
    rows = []
    evidence = set()
    for r in eligible:
        code = r["code"]
        industries = r.get("industry") or []
        if code not in current or len(industries) != 1:
            continue
        cap, pb, obs = legacy.get(code, (r.get("float_cap_yi"), r.get("pb"), None))
        turnover = number(r.get("turnover"))
        if any(v is None or v <= 0 for v in (cap, pb, turnover)):
            continue
        rows.append((industries[0]["name"], cap, current[code]["change"],
                     (math.log(cap), 1/pb, math.log(turnover), -r["change"])))
        if obs is not None:
            evidence.add(obs)
    return dict(fit(rows, len(eligible)), revisions=dict(exposure=prior_id, returns=current_id,
                                                        raw=sorted(evidence)))


def router(store):
    api = APIRouter()
    cached = lru_cache(maxsize=48)(lambda *args: estimate(store, *args))

    @api.get("/api/barra")
    def daily(day: date | None = None, model: Literal["archive","price_volume"] | None = None):
        root = store.path.parent / "quant-price-v2" / "results"
        if model != "archive" and (root.is_dir() or model == "price_volume"):
            dates = sorted((p.stem for p in root.glob("????-??-??.json")), reverse=True)
            selected = day.isoformat() if day else next(iter(dates), None)
            if selected not in dates:
                return dict(day=selected, dates=dates, version="cn-price-volume-v2", factors=[],
                            reason="所选日期尚无已验证的八因子结果")
            return {**json.loads((root / (selected + ".json")).read_text()), "dates": dates}

        with store.connect() as db:
            archives = dict(db.execute("""SELECT trade_date,MAX(id) FROM snapshots
                WHERE kind='gainers' AND mode='close' GROUP BY trade_date ORDER BY trade_date DESC"""))
        selected = day.isoformat() if day else next(iter(archives), None)
        result = dict(day=selected, dates=list(archives), version=VERSION, factors=[])
        if selected not in archives:
            return dict(result, reason="所选日期尚无个股收盘归档")
        calendar = sorted(set(store.observed("calendar") or []))
        position = bisect_left(calendar, selected)
        if not (0 < position < len(calendar) and calendar[position] == selected):
            return dict(result, reason="交易日历不足，无法确认相邻交易日")
        previous = calendar[position-1]
        result["previous"] = previous
        with store.connect() as db:
            prior = db.execute("""SELECT MAX(id) FROM snapshots WHERE kind='gainers' AND mode='close'
                AND trade_date=? AND collected_at<?""", (previous, selected + "T09:30")).fetchone()[0]
        if prior is None:
            return dict(result, reason="缺少开盘前已归档的上一交易日暴露")
        return {**result, **cached(selected, previous, prior, archives[selected])}
    return api
