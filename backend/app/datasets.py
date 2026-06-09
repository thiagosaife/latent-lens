"""Dataset upload parsing — CSV (stdlib) and Parquet (pyarrow) → the shared
`Dataset` shape the pipeline already consumes. Type inference, missingness, a
numeric (mean-imputed) feature matrix for the embedding, and a duplicate count.
Uploaded datasets are held in an in-memory registry keyed by id."""

from __future__ import annotations

import csv
import io
import secrets

import numpy as np

from .ml import Dataset

MAX_BYTES = 64 * 1024 * 1024  # 64 MB upload cap
MAX_ROWS = 200_000  # rows parsed (beyond this we truncate)
_DUP_SCAN = 50_000  # cap duplicate detection for performance
_NA = {"", "na", "n/a", "nan", "null", "none", "-"}

# Registry of uploaded datasets.
DATASETS: dict[str, Dataset] = {}


def register(ds: Dataset) -> str:
    did = "ds_" + secrets.token_hex(4)
    DATASETS[did] = ds
    return did


def get_dataset(dataset_id: str | None) -> Dataset | None:
    return DATASETS.get(dataset_id) if dataset_id else None


def preview(ds: Dataset) -> dict:
    cols = [{"name": c, "type": "numeric", "missing": ds.missing_by_col.get(c, 0.0)} for c in ds.numeric_cols]
    cols += [{"name": c, "type": "categorical", "missing": ds.missing_by_col.get(c, 0.0)} for c in ds.categorical_cols]
    return {
        "rows": ds.n,
        "numeric": len(ds.numeric_cols),
        "categorical": len(ds.categorical_cols),
        "duplicates": ds.duplicates,
        "columns": cols[:40],
    }


def parse_upload(filename: str, raw: bytes) -> Dataset:
    lower = filename.lower()
    if lower.endswith((".parquet", ".pq")):
        return _from_parquet(filename, raw)
    return _from_csv(filename, raw)


# ── CSV (stdlib) ───────────────────────────────────────────────────────────

def _is_missing(v: str) -> bool:
    return v.strip().lower() in _NA


def _from_csv(name: str, raw: bytes) -> Dataset:
    text = raw.decode("utf-8-sig", errors="replace")
    reader = csv.reader(io.StringIO(text))
    header = next(reader, None)
    if not header:
        raise ValueError("empty CSV (no header)")
    cols = [(h.strip() or f"col{i}") for i, h in enumerate(header)]

    columns: list[list[str]] = [[] for _ in cols]
    n = 0
    for row in reader:
        if n >= MAX_ROWS:
            break
        for i in range(len(cols)):
            columns[i].append(row[i] if i < len(row) else "")
        n += 1
    if n == 0:
        raise ValueError("CSV has a header but no rows")
    return _build(name, cols, columns, n)


def _build(name: str, cols: list[str], columns: list[list[str]], n: int) -> Dataset:
    numeric_cols: list[str] = []
    categorical_cols: list[str] = []
    numeric_arrays: list[np.ndarray] = []
    missing_by_col: dict[str, float] = {}
    missing_cells = 0

    for cname, values in zip(cols, columns):
        parsed = np.full(n, np.nan, dtype=np.float64)
        is_numeric = True
        seen = 0
        miss = 0
        for j, v in enumerate(values):
            if _is_missing(v):
                miss += 1
                continue
            seen += 1
            # keep scanning the whole column so missing is fully counted even
            # after a non-numeric value marks the column categorical
            if is_numeric:
                try:
                    parsed[j] = float(v)
                except ValueError:
                    is_numeric = False
        missing_cells += miss
        if miss:
            missing_by_col[cname] = round(miss / n, 3)

        if is_numeric and seen > 0:
            col_mean = np.nanmean(parsed)
            if not np.isfinite(col_mean):
                col_mean = 0.0
            numeric_arrays.append(np.where(np.isnan(parsed), col_mean, parsed).astype(np.float32))
            numeric_cols.append(cname)
        else:
            categorical_cols.append(cname)

    features = np.stack(numeric_arrays, axis=1) if numeric_arrays else np.zeros((n, 1), dtype=np.float32)
    duplicates = _count_duplicates(columns, n)
    return Dataset(name, features, n, numeric_cols, categorical_cols, missing_by_col, missing_cells, duplicates)


def _count_duplicates(columns: list[list[str]], n: int) -> int:
    seen: set[tuple] = set()
    dup = 0
    for j in range(min(n, _DUP_SCAN)):
        key = tuple(col[j] for col in columns)
        if key in seen:
            dup += 1
        else:
            seen.add(key)
    return dup


# ── Parquet (pyarrow) ──────────────────────────────────────────────────────

def _from_parquet(name: str, raw: bytes) -> Dataset:
    import pyarrow as pa
    import pyarrow.parquet as pq

    table = pq.read_table(io.BytesIO(raw))
    if table.num_rows > MAX_ROWS:
        table = table.slice(0, MAX_ROWS)
    n = table.num_rows
    if n == 0:
        raise ValueError("Parquet file has no rows")

    numeric_cols: list[str] = []
    categorical_cols: list[str] = []
    numeric_arrays: list[np.ndarray] = []
    missing_by_col: dict[str, float] = {}
    missing_cells = 0

    for field in table.schema:
        col = table.column(field.name)
        nulls = col.null_count
        missing_cells += nulls
        if nulls:
            missing_by_col[field.name] = round(nulls / n, 3)

        if pa.types.is_integer(field.type) or pa.types.is_floating(field.type) or pa.types.is_decimal(field.type):
            arr = col.cast(pa.float64()).combine_chunks().to_numpy(zero_copy_only=False).astype(np.float64)
            col_mean = np.nanmean(arr) if np.any(~np.isnan(arr)) else 0.0
            if not np.isfinite(col_mean):
                col_mean = 0.0
            numeric_arrays.append(np.where(np.isnan(arr), col_mean, arr).astype(np.float32))
            numeric_cols.append(field.name)
        else:
            categorical_cols.append(field.name)

    features = np.stack(numeric_arrays, axis=1) if numeric_arrays else np.zeros((n, 1), dtype=np.float32)

    # Duplicate detection over ALL columns (stringified, capped for performance).
    scan = table.slice(0, min(n, _DUP_SCAN))
    str_cols = [[("" if v is None else str(v)) for v in scan.column(f.name).to_pylist()] for f in table.schema]
    duplicates = _count_duplicates(str_cols, scan.num_rows)
    return Dataset(name, features, n, numeric_cols, categorical_cols, missing_by_col, missing_cells, duplicates)
