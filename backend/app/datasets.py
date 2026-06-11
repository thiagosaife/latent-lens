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
_CANDIDATE_DELIMS = [",", ";", "\t", "|"]  # delimiters we auto-detect, in tie-break order

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
        "delimiter": ds.delimiter,
        "hasHeader": ds.has_header,
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


def _is_number(v: str) -> bool:
    try:
        float(v)
        return True
    except ValueError:
        return False


def _sniff_delimiter(text: str) -> str:
    """Detect the field delimiter so a `;`-separated or tab-separated file isn't
    silently parsed as a single column. Tries csv.Sniffer, then falls back to the
    candidate that appears most consistently (in every sampled line)."""
    lines = [ln for ln in text.splitlines()[:20] if ln.strip()]
    sample = "\n".join(lines)
    try:
        guess = csv.Sniffer().sniff(sample, delimiters="".join(_CANDIDATE_DELIMS)).delimiter
        if guess in _CANDIDATE_DELIMS:
            return guess
    except csv.Error:
        pass  # Sniffer is finicky on short/clean samples — fall back to counting

    # A real delimiter appears the same number of times in every row. Rank by
    # (present in every line, highest min count, highest mean count).
    best, best_score = ",", (0, -1, -1.0)
    for d in _CANDIDATE_DELIMS:
        counts = [ln.count(d) for ln in lines]
        if not counts or max(counts) == 0:
            continue
        score = (1 if min(counts) > 0 else 0, min(counts), sum(counts) / len(counts))
        if score > best_score:
            best, best_score = d, score
    return best


def _sniff_header(text: str, delimiter: str) -> bool:
    """Decide whether row 0 is a header. csv.Sniffer first, then a fallback: it's a
    header when the first row is all-text but later rows carry numeric cells."""
    sample = "\n".join(text.splitlines()[:50])
    try:
        return csv.Sniffer().has_header(sample)
    except csv.Error:
        pass
    rows = list(csv.reader(io.StringIO(sample), delimiter=delimiter))
    if len(rows) < 2:
        return True  # can't tell from one row — assume a header (the common case)
    first_has_number = any(_is_number(v) for v in rows[0])
    body_has_number = any(_is_number(v) for r in rows[1:] for v in r)
    return (not first_has_number) and body_has_number


def _from_csv(name: str, raw: bytes) -> Dataset:
    text = raw.decode("utf-8-sig", errors="replace")
    if not text.strip():
        raise ValueError("empty CSV (no rows)")
    delimiter = _sniff_delimiter(text)
    has_header = _sniff_header(text, delimiter)

    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    first = next(reader, None)
    if first is None:
        raise ValueError("empty CSV (no rows)")
    if has_header:
        cols = [(h.strip() or f"col{i + 1}") for i, h in enumerate(first)]
    else:
        cols = [f"col{i + 1}" for i in range(len(first))]

    columns: list[list[str]] = [[] for _ in cols]
    n = 0
    # When there's no header, the first row is real data — don't drop it.
    data_rows = reader if has_header else _prepend(first, reader)
    for row in data_rows:
        if n >= MAX_ROWS:
            break
        for i in range(len(cols)):
            columns[i].append(row[i] if i < len(row) else "")
        n += 1
    if n == 0:
        raise ValueError("CSV has a header but no rows")
    return _build(name, cols, columns, n, delimiter=delimiter, has_header=has_header)


def _prepend(first, rest):
    """Yield `first`, then everything from the `rest` iterator (no header case)."""
    yield first
    yield from rest


def _build(
    name: str,
    cols: list[str],
    columns: list[list[str]],
    n: int,
    delimiter: str | None = None,
    has_header: bool = True,
) -> Dataset:
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
    return Dataset(name, features, n, numeric_cols, categorical_cols, missing_by_col, missing_cells, duplicates, delimiter, has_header)


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
