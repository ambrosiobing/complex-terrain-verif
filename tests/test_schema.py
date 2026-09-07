import pandas as pd
import pytest

from afv.schema import MATCHED_COLUMNS, SchemaError, ScoreTable, Station, ensure_utc, validate


def test_station_roundtrip():
    s = Station("SN1", "A", 60.0, 5.0, 10.0, "Vestland", "coastal")
    row = s.to_row()
    assert row["station_id"] == "SN1" and row["elev_m"] == 10.0


def test_validate_missing_column():
    df = pd.DataFrame({"station_id": ["a"]})
    with pytest.raises(SchemaError):
        validate(df, MATCHED_COLUMNS, "matched")


def test_validate_wrong_dtype():
    df = pd.DataFrame({c: ["x"] for c in MATCHED_COLUMNS})
    with pytest.raises(SchemaError):
        validate(df, MATCHED_COLUMNS, "matched")


def test_ensure_utc_naive_and_aware():
    s = pd.Series(pd.to_datetime(["2026-09-01T00:00"]))
    assert str(ensure_utc(s).dt.tz) == "UTC"
    s2 = pd.Series(pd.to_datetime(["2026-09-01T02:00+02:00"]))
    assert ensure_utc(s2).iloc[0].hour == 0


def test_scoretable_parquet_roundtrip(tmp_path):
    rows = pd.DataFrame([["ifs", "t2m", 6, "all", "all", "mae", 1.0, 0.8, 1.2, 10]],
                        columns=["model", "variable", "lead_h", "stratum", "stratum_value", "metric", "value", "ci_lo", "ci_hi", "n"])
    st = ScoreTable(rows=rows, dataset_hash="abc", config_hash="def", afv_version="0.1.0")
    p = st.to_parquet(tmp_path / "s.parquet")
    back = ScoreTable.from_parquet(p)
    assert back.dataset_hash == "abc" and back.config_hash == "def"
    assert back.rows.iloc[0]["value"] == 1.0
    st.to_jsonl(tmp_path / "s.jsonl")
    assert (tmp_path / "s.jsonl").read_text().count("\n") == 2
