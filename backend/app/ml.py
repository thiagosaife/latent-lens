"""Real ML for the analysis pipeline — numpy only (no pandas/sklearn/umap), so
there are no native-wheel surprises on new Python versions. PCA is computed via
SVD; clustering via a small k-means. A synthetic dataset is the default; uploaded
CSV/Parquet datasets (see datasets.py) flow through the same Dataset shape."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

RNG_SEED = 42
N_ROWS = 50_000
N_NUMERIC = 12
K_CLUSTERS = 6
MAX_EMBED_POINTS = 100_000  # subsample the embedding above this for responsiveness

# In-memory embedding store: pointsRef -> (n, 3) float32 [x, y, cluster].
_EMBEDDINGS: dict[str, np.ndarray] = {}


@dataclass
class Dataset:
    name: str
    features: np.ndarray  # (n, n_numeric) float32, NaN-imputed — the embedding input
    n: int
    numeric_cols: list[str]
    categorical_cols: list[str]
    missing_by_col: dict[str, float]  # fraction missing, per column that has any
    missing_cells: int  # total missing cells across all columns
    duplicates: int


def generate_dataset(seed: int = RNG_SEED, n: int = N_ROWS, k: int = K_CLUSTERS) -> Dataset:
    """Gaussian blobs in feature space → genuine separable segments to find."""
    rng = np.random.default_rng(seed)
    centers = rng.normal(0.0, 4.0, size=(k, N_NUMERIC))
    spread = rng.uniform(0.6, 1.4, size=(k, N_NUMERIC))
    labels = rng.integers(0, k, size=n)
    features = (centers[labels] + rng.normal(0.0, 1.0, size=(n, N_NUMERIC)) * spread[labels]).astype(np.float32)

    numeric_cols = [f"feat_{i}" for i in range(N_NUMERIC - 4)] + ["recency", "spend", "tenure", "last_login"]
    categorical_cols = ["signup_source", "plan", "region", "device"]
    missing_by_col = {"signup_source": 0.41, "last_login": 0.08}
    missing_cells = sum(int(round(frac * n)) for frac in missing_by_col.values())
    return Dataset("synthetic (50k)", features, n, numeric_cols, categorical_cols, missing_by_col, missing_cells, duplicates=0)


def profile_dataset(ds: Dataset) -> dict:
    """Column/missingness stats over the dataset (works for synthetic or uploaded)."""
    numeric = len(ds.numeric_cols)
    categorical = len(ds.categorical_cols)
    total_cells = ds.n * max(numeric + categorical, 1)
    return {
        "name": ds.name,
        "rows": ds.n,
        "numeric": numeric,
        "categorical": categorical,
        "missing_fraction": round(ds.missing_cells / total_cells, 4) if total_cells else 0.0,
        "missing_cells": ds.missing_cells,
        "missing_by_col": ds.missing_by_col,
        "flagged_high_missing": [c for c, f in ds.missing_by_col.items() if f > 0.30],
        "duplicates": ds.duplicates,
    }


def _pca_2d(features: np.ndarray) -> np.ndarray:
    """Project to the top-2 principal components via SVD; normalize to [-1, 1].
    Always returns (n, 2), padding when the data has fewer than 2 columns."""
    x = features - features.mean(axis=0, keepdims=True)
    if x.shape[1] < 2:
        pad = np.zeros((len(x), 2 - x.shape[1]), dtype=x.dtype)
        x = np.column_stack([x, pad])
    _, _, vt = np.linalg.svd(x, full_matrices=False)
    proj = x @ vt[:2].T
    scale = float(np.max(np.abs(proj))) or 1.0
    return (proj / scale).astype(np.float32)


def _kmeans(points: np.ndarray, k: int, iters: int = 12, seed: int = RNG_SEED) -> np.ndarray:
    rng = np.random.default_rng(seed)
    centroids = points[rng.choice(len(points), size=k, replace=False)].copy()
    labels = np.zeros(len(points), dtype=np.int64)
    for _ in range(iters):
        dist = ((points[:, None, :] - centroids[None, :, :]) ** 2).sum(-1)
        labels = dist.argmin(1)
        for c in range(k):
            sel = points[labels == c]
            if len(sel):
                centroids[c] = sel.mean(0)
    return labels


def build_embedding(run_id: str, ds: Dataset, k: int = K_CLUSTERS) -> tuple[str, int, list[int]]:
    """Standardize → PCA(2D) → k-means; store points by ref. Subsamples above
    MAX_EMBED_POINTS so huge uploads stay responsive."""
    feats = ds.features
    if ds.n > MAX_EMBED_POINTS:
        idx = np.random.default_rng(RNG_SEED).choice(ds.n, MAX_EMBED_POINTS, replace=False)
        feats = feats[idx]
    n_pts = len(feats)

    mu = feats.mean(0, keepdims=True)
    sd = feats.std(0, keepdims=True)
    sd[sd == 0] = 1.0
    coords = _pca_2d((feats - mu) / sd)
    labels = _kmeans(coords, k)

    pts = np.empty((n_pts, 3), dtype=np.float32)
    pts[:, 0] = coords[:, 0]
    pts[:, 1] = coords[:, 1]
    pts[:, 2] = labels.astype(np.float32)

    ref = f"pca://{run_id}"
    _EMBEDDINGS[ref] = pts
    sizes = np.bincount(labels, minlength=k).tolist()
    return ref, n_pts, sizes


def get_points(ref: str) -> bytes | None:
    """Binary Float32 buffer [x, y, cluster] * n for `GET /api/points`."""
    pts = _EMBEDDINGS.get(ref)
    return None if pts is None else pts.tobytes()


def embedding_sizes(ref: str, k: int = K_CLUSTERS) -> list[int]:
    """Per-cluster counts for a stored embedding (the 3rd column holds labels)."""
    pts = _EMBEDDINGS.get(ref)
    if pts is None:
        return []
    return np.bincount(pts[:, 2].astype(np.int64), minlength=k).tolist()


def run_kmeans_on(ref: str, k: int = K_CLUSTERS) -> list[int]:
    """Run k-means over a stored embedding's 2D coordinates → per-cluster sizes.

    A genuine clustering pass (not a read-back of the colors computed at reduce
    time) — same coords + seed, so it's deterministic and consistent."""
    pts = _EMBEDDINGS.get(ref)
    if pts is None:
        return []
    labels = _kmeans(pts[:, :2], k)
    return np.bincount(labels, minlength=k).tolist()
