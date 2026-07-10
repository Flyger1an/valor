"""Binance deep-OI integration (2026-07-10): metrics-dump parsing (canned zip, no network) and
refresh_oi_deep's chunked/resumable backfill mechanics — merge, top-up, listing-floor markers, and
the young-coin return filter."""
import csv
import datetime as dt
import io
import os
import pathlib
import pickle
import sys
import tempfile
import zipfile

os.environ.setdefault("EVOLVER_USE_LLM", "0")
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import evolver.data.binance_dumps as BD  # noqa: E402
import research_tick as rt  # noqa: E402

D = 86_400_000


def _canned_zip(rows):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        s = io.StringIO()
        csv.writer(s).writerows(rows)
        z.writestr("m.csv", s.getvalue())
    return buf.getvalue()


def test_metrics_oi_day_parses_last_row_and_derives_close():
    rows = [["create_time", "symbol", "sum_open_interest", "sum_open_interest_value", "x"],
            ["2026-07-08 00:05:00", "BTCUSDT", "100000", "6300000000", "1"],
            ["2026-07-08 23:55:00", "BTCUSDT", "99414.319", "6303136243.26", "1"],
            ["2026-07-09 00:00:00", "BTCUSDT", "111111", "9999999999", "1"]]   # next day: ignored
    saved = BD._fetch_csv
    BD._fetch_csv = lambda url: [list(map(str, r)) for r in rows]
    try:
        close, oi = BD.metrics_oi_day("BTCUSDT", "2026-07-08")
    finally:
        BD._fetch_csv = saved
    assert abs(oi - 99414.319) < 1e-6
    assert abs(close - 6303136243.26 / 99414.319) < 1e-6      # mark = value/qty of the LAST row
    BD._fetch_csv = lambda url: (_ for _ in ()).throw(OSError("404"))
    try:
        assert BD.metrics_oi_day("NOPEUSDT", "2026-07-08") is None   # 404 -> None, never raises
    finally:
        BD._fetch_csv = saved
    return True


def test_refresh_oi_deep_chunks_floors_and_filters():
    tmp = pathlib.Path(tempfile.mkdtemp()) / "oideep.pkl"
    today = dt.datetime.now(dt.timezone.utc).date()

    def day_ms(d):
        return int(dt.datetime(d.year, d.month, d.day, tzinfo=dt.timezone.utc).timestamp() * 1000)

    # seed: coin A has 3 recent days; listing date = today-5 (anything older 404s)
    seed_days = [today - dt.timedelta(days=i) for i in (2, 3, 4)]
    tmp.write_bytes(pickle.dumps({"AAA": {day_ms(d): (100.0, 1000.0) for d in seed_days}}))
    listing = today - dt.timedelta(days=5)

    def fake_metrics(symbol, date_iso):
        d = dt.date.fromisoformat(date_iso)
        if symbol != "AAAUSDT" or d < listing or d >= today:
            return None
        return (100.0 + d.day, 1000.0 + d.day)

    saved = (rt.OI_DEEP, rt.OI_DEEP_CHUNK, rt.UNIVERSE)
    import evolver.data.binance_dumps as BDm
    savedf = BDm.metrics_oi_day
    rt.OI_DEEP, rt.OI_DEEP_CHUNK, rt.UNIVERSE = tmp, 10, ["AAA"]
    BDm.metrics_oi_day = fake_metrics
    try:
        out1 = rt.refresh_oi_deep()
        cache = pickle.loads(tmp.read_bytes())
        days = sorted(cache["AAA"])
        # top-up got today-1; backfill got today-5 (listing); older all 404'd
        assert day_ms(today - dt.timedelta(days=1)) in cache["AAA"]
        assert day_ms(listing) in cache["AAA"]
        assert len(days) == 5 and "_floors" in cache and "AAA" not in cache["_floors"]
        # second pass: whole backward chunk 404s -> floor marker set, no re-probing after
        rt.refresh_oi_deep()
        cache = pickle.loads(tmp.read_bytes())
        assert cache["_floors"].get("AAA") == min(cache["AAA"])
        out3 = rt.refresh_oi_deep()                     # floored: only top-up jobs now
        assert "_floors" not in out3                    # meta never leaks to families
        assert out3 == {} or all(len(v) >= 400 for v in out3.values())   # young-coin filter
    finally:
        rt.OI_DEEP, rt.OI_DEEP_CHUNK, rt.UNIVERSE = saved
        BDm.metrics_oi_day = savedf
    assert out1 == {}                                   # 5 days << 400 -> family sees nothing yet
    return True
