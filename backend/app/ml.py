"""Real ML for the analysis pipeline — numpy only (no pandas/sklearn/umap), so
there are no native-wheel surprises on new Python versions. PCA is computed via
SVD; clustering via a small k-means. A synthetic customer dataset with latent
segment structure stands in for an uploaded CSV (a follow-up will add upload)."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

RNG_SEED = 42
N_ROWS = 50_000
N_NUMERIC = 12
K_CLUSTERS = 6

# In-memory embedding store: pointsRef -> (n, 3) float32 [x, y, cluster].
# A real deployment would use an object store with TTL; fine in-process here.
_EMBEDDINGS: dict[str, np.ndarray] = {}


@dataclass
class Dataset:
    features: np.ndarray  # (n, N_NUMERIC) float32 with latent cluster structure
    n: int
    numeric_cols: list[str]
    categorical_cols: list[str]
    missing_by_col: dict[str, float]  # fraction missing, per flagged column
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
    return Dataset(features, n, numeric_cols, categorical_cols, missing_by_col, duplicates=0)


def profile_dataset(ds: Dataset) -> dict:
    """Real column/missingness stats over the dataset."""
    numeric = len(ds.numeric_cols)
    categorical = len(ds.categorical_cols)
    total_cells = ds.n * (numeric + categorical)
    missing_cells = sum(int(round(frac * ds.n)) for frac in ds.missing_by_col.values())
    return {
        "rows": ds.n,
        "numeric": numeric,
        "categorical": categorical,
        "missing_fraction": round(missing_cells / total_cells, 4),
        "missing_by_col": {c: round(f, 3) for c, f in ds.missing_by_col.items()},
        "flagged_high_missing": [c for c, f in ds.missing_by_col.items() if f > 0.30],
        "duplicates": ds.duplicates,
    }


def _pca_2d(features: np.ndarray) -> np.ndarray:
    """Project to the top-2 principal components via SVD; normalize to [-1, 1]."""
    x = features - features.mean(axis=0, keepdims=True)
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
    """Compute the 2D embedding + cluster labels, store by ref, return
    (pointsRef, n, cluster_sizes). The frontend fetches the points by ref."""
    coords = _pca_2d(ds.features)
    labels = _kmeans(coords, k)
    pts = np.empty((ds.n, 3), dtype=np.float32)
    pts[:, 0] = coords[:, 0]
    pts[:, 1] = coords[:, 1]
    pts[:, 2] = labels.astype(np.float32)

    ref = f"pca://{run_id}"
    _EMBEDDINGS[ref] = pts
    sizes = np.bincount(labels, minlength=k).tolist()
    return ref, ds.n, sizes


def get_points(ref: str) -> bytes | None:
    """Binary Float32 buffer [x, y, cluster] * n for `GET /api/points`."""
    pts = _EMBEDDINGS.get(ref)
    return None if pts is None else pts.tobytes()
