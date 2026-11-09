"""规划分片：尽量在空白处切开图片，使每个分片都不超过模型的原生分辨率。"""

from __future__ import annotations

import math

import numpy as np

import image_io

PATCH = 28
MIN_EDGES = 2


def fits(width: int, height: int, max_edge: int, max_tokens: int) -> bool:
    tokens = math.ceil(width / PATCH) * math.ceil(height / PATCH)
    return max(width, height) <= max_edge and tokens <= max_tokens


def max_band(width: int, max_edge: int, max_tokens: int) -> int:
    """给定宽度时不会被缩放的最大高度。"""
    return min(max_edge, max_tokens // math.ceil(width / PATCH) * PATCH)


def plan_tiles(image, max_edge: int, max_tokens: int) -> list:
    """返回 (left, top, right, bottom) 分片列表，坐标以传入图片为准。"""
    width, height = image.size
    if fits(width, height, max_edge, max_tokens):
        return [(0, 0, width, height)]

    gray = np.asarray(image_io.flatten(image).convert("L"), dtype=np.int16)
    alpha = np.asarray(image.getchannel("A"), dtype=np.int16) if image.mode == "RGBA" else None
    row_edges, column_edges = _edge_maps(gray, alpha)
    unit = _typical_run(row_edges.sum(axis=1) >= MIN_EDGES)

    if width <= max_edge:
        columns, clean = [(0, width)], True
    else:
        columns, clean = plan_cuts(column_edges.sum(axis=0), max_edge, unit)

    if clean:
        # 纵向切点都在空白处（如栏间距）：各列独立按行切分，按列优先排列。
        tiles = []
        for left, right in columns:
            limit = max_band(right - left, max_edge, max_tokens)
            bands, _ = plan_cuts(row_edges[:, left:right - 1].sum(axis=1), limit, unit)
            tiles.extend((left, top, right, bottom) for top, bottom in bands)
        return tiles

    # 纵向切点穿过内容：所有列共用同一组行切点，按行优先排列，便于横向拼接整行。
    limit = max_band(max(right - left for left, right in columns), max_edge, max_tokens)
    bands, _ = plan_cuts(row_edges.sum(axis=1), limit, unit)
    return [(left, top, right, bottom) for top, bottom in bands for left, right in columns]


def plan_cuts(counts, limit: int, unit: int):
    """沿一个方向切分，使每段长度都不超过 limit。

    counts 是每一行（或列）的边缘像素数。优先在足够宽的空白处切开且不留重叠；
    找不到时在内容最少处切开，并让下一段与上一段重叠。返回 (分段列表, 是否全部落在空白处)。
    """
    length = len(counts)
    min_gap = max(16, unit // 2)
    margin = min_gap // 2
    overlap = min(max(96, 2 * unit), limit // 4)
    gaps = [
        (start + margin, end - margin)
        for start, end in _runs(counts < MIN_EDGES)
        if end - start >= min_gap
    ]

    segments, clean, start = [], True, 0
    while length - start > limit:
        remaining = length - start
        target = start + math.ceil(remaining / math.ceil(remaining / limit))
        low, high = start + limit // 2, min(start + limit, length - limit // 4)

        options = [
            min(max(target, max(low, a)), min(high, b))
            for a, b in gaps
            if max(low, a) <= min(high, b)
        ]
        if options:
            cut = min(options, key=lambda value: abs(value - target))
            segments.append((start, cut))
            start = cut
            continue

        clean = False
        a, b = max(low, target - limit // 8), min(high, target + limit // 8)
        window = counts[a:b + 1]
        candidates = np.flatnonzero(window == window.min()) + a
        cut = int(candidates[np.argmin(np.abs(candidates - target))])
        segments.append((start, cut))
        start = cut - overlap

    segments.append((start, length))
    return segments, clean


def _edge_maps(gray, alpha):
    """返回横向与纵向的强边缘布尔图，阈值随图片对比度自适应。"""
    dx = np.abs(np.diff(gray, axis=1))
    dy = np.abs(np.diff(gray, axis=0))
    sample = dx[::2, ::2]
    strong = float(np.percentile(sample, 99.5)) if sample.size else 0.0
    threshold = min(max(0.3 * strong, 6.0), 32.0)
    row_edges, column_edges = dx > threshold, dy > threshold
    if alpha is not None:
        row_edges |= np.abs(np.diff(alpha, axis=1)) > threshold
        column_edges |= np.abs(np.diff(alpha, axis=0)) > threshold
    return row_edges, column_edges


def _runs(mask):
    """返回布尔数组中连续 True 区段的 (start, end) 列表。"""
    padded = np.concatenate(([False], np.asarray(mask, dtype=bool), [False]))
    changes = np.diff(padded.astype(np.int8))
    return list(zip(np.flatnonzero(changes == 1).tolist(), np.flatnonzero(changes == -1).tolist()))


def _typical_run(content) -> int:
    """估计典型文字行高，用于判断空白是否足够宽以及重叠多少。"""
    lengths = [end - start for start, end in _runs(content) if end - start >= 3]
    return min(int(np.median(lengths)), 100) if lengths else 0
