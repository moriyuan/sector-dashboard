from datetime import date
import json
import numpy as np
import pytest

from backend.barra import STYLES, archived_valuations, fit, router
from backend.store import Store


def test_joint_regression_recovers_four_style_returns_and_neutral_industries():
    rng = np.random.default_rng(92)
    size = 400
    ids = np.arange(size) % 3
    cap = np.exp(rng.normal(size=size))
    raw = rng.normal(size=(size, 4))
    x = np.clip(raw, *np.quantile(raw, [.01, .99], axis=0))
    z = (x - np.average(x, weights=cap, axis=0)) / x.std(axis=0)
    target = np.array([.3, -.6, .02, .25])
    y = 1.15 + ids * .35 + z @ target
    result = fit([(str(i), c, value, styles) for i,c,value,styles in zip(ids,cap,y,raw)], size)
    factors = {r["name"]: r["value"] for r in result["factors"]}
    for name, value in zip(STYLES, target):
        assert factors[name] == pytest.approx(value)
    assert result["r2"] == pytest.approx(1)
    assert sum(cap[ids==i].sum() * factors[str(i)] for i in range(3)) == pytest.approx(0)
    assert factors["市场"] == pytest.approx(np.average(1.15+ids*.35, weights=cap))


def test_coverage_and_singular_exposures_are_unavailable():
    rows = [(str(i%2), 1., 0., (1., 1., 1., 1.)) for i in range(110)]
    assert fit(rows, 500)["factors"] == []
    assert "差异" in fit(rows, 110)["reason"]


def test_archive_uses_previous_session_and_excludes_later_revisions(tmp_path):
    store = Store(tmp_path/"archive.db")
    store.observe("calendar", ["2026-10-29", "2026-10-30", "2026-11-02"])
    for day,mode,stamp in [
        ("2026-10-29","close","2026-10-29T16:00"), ("2026-10-30","midday","2026-10-30T12:01"),
        ("2026-10-30","close","2026-11-02T10:00"), ("2026-11-02","close","2026-11-02T16:00")]:
        revision = store.save("gainers", day, mode, {"rows":[]})
        with store.connect() as db:
            db.execute("UPDATE snapshots SET collected_at=? WHERE id=?", (stamp,revision))
    view = router(store).routes[0].endpoint
    result = view(date(2026,11,2))
    assert result["previous"] == "2026-10-30" and "开盘前" in result["reason"]
    assert not view(date(2026,10,31))["factors"]


def test_legacy_fields_use_matching_dated_quote_before_open(tmp_path):
    store = Store(tmp_path/"raw.db")
    fields = [""]*49
    fields[2], fields[3], fields[30] = "600000","12","20261030150000"
    row = dict(code="600000",source="腾讯财经",source_time="2026-10-30T15:00:00+08:00",price=12)
    for stamp,cap in [("2026-10-30T18:00", "21"), ("2026-11-02T10:00","999")]:
        fields[44],fields[46] = cap,"0.8"
        payload=json.dumps({"text":'v_sh600000="'+'~'.join(fields)+'";'})
        with store.connect() as db:
            db.execute("INSERT INTO observations(key,observed_at,payload) VALUES(?,?,?)",
                       ("public_raw:https://qt.gtimg.cn/q=sh600000",stamp,payload))
    assert archived_valuations(store,"2026-10-30","2026-11-02",[row])["600000"][:2] == (21,.8)
