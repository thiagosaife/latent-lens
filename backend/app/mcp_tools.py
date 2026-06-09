"""MCP server exposing the analysis tools — the spec's reuse argument: the same
toolset (`profile_dataset`, `clean_dataset`, `reduce_dimensions`,
`cluster_segments`, `summarize_segments`) is reusable by any MCP client.

The backend connects to this server IN-PROCESS (mcp_client.py) so the tools
share the live dataset registry. It is also runnable standalone over stdio for
external clients (`python -m app.mcp_tools`) — those get the synthetic dataset,
since they don't share the upload registry.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from . import datasets as ds_mod
from . import llm, ml

mcp = FastMCP("latentlens-analysis")


def _dataset(dataset_id: str | None) -> ml.Dataset:
    ds = ds_mod.get_dataset(dataset_id)
    return ds if ds is not None else ml.generate_dataset()


@mcp.tool()
def profile_dataset(dataset_id: str | None = None) -> dict:
    """Profile a dataset: rows, numeric/categorical column counts, missingness, duplicates."""
    return ml.profile_dataset(_dataset(dataset_id))


@mcp.tool()
def clean_dataset(dataset_id: str | None = None) -> dict:
    """Report cleaning actions: total cells to impute, duplicate rows, numeric columns to standardize."""
    ds = _dataset(dataset_id)
    return {"missing_cells": ds.missing_cells, "duplicates": ds.duplicates, "numeric": len(ds.numeric_cols)}


@mcp.tool()
def reduce_dimensions(run_id: str, dataset_id: str | None = None) -> dict:
    """Project to 2D (PCA) and cluster (k-means); store the embedding by ref.
    Returns the pointsRef, point count, and cluster sizes."""
    ref, n, sizes = ml.build_embedding(run_id, _dataset(dataset_id))
    return {"pointsRef": ref, "pointCount": n, "sizes": sizes}


@mcp.tool()
def cluster_segments(points_ref: str) -> dict:
    """Per-segment sizes for a previously computed embedding."""
    return {"sizes": ml.embedding_sizes(points_ref)}


@mcp.tool()
def summarize_segments(goal: str, profile: dict, sizes: list[int]) -> dict:
    """Write a summary_card (title/body/bullets/tone) describing the discovered segments."""
    return llm.generate_segment_summary(goal, profile, sizes)


if __name__ == "__main__":
    mcp.run()  # stdio transport for external MCP clients
