#!/usr/bin/env python3
"""为视觉读取准备图片：按 EXIF 方向摆正、规范颜色模式与格式，必要时切成不会被缩放的分片。

标准输出始终是一个 JSON 对象。原图不会被修改。
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

# 允许以 python3 -I 运行：隔离模式不会把脚本目录加入 sys.path。
sys.path.insert(0, str(Path(__file__).resolve().parent))

import image_io  # noqa: E402
import split_image  # noqa: E402

TIERS = {"high": (2576, 4784), "standard": (1568, 1568)}


class _Output:
    """首次写文件时才创建输出目录；未指定目录时新建系统临时目录。"""

    def __init__(self, requested):
        self.requested = requested
        self.path = None

    def write(self, name, image):
        if self.path is None:
            if self.requested is None:
                self.path = Path(tempfile.mkdtemp(prefix="img2md-"))
            else:
                self.requested.mkdir(parents=True, exist_ok=True)
                self.path = self.requested
        data, suffix, quality = image_io.encode_for_reading(image)
        target = self.path / f"{name}{suffix}"
        target.write_bytes(data)
        return target, quality


def _overlaps(boxes) -> bool:
    return any(
        min(a[2], b[2]) > max(a[0], b[0]) and min(a[3], b[3]) > max(a[1], b[1])
        for index, a in enumerate(boxes)
        for b in boxes[index + 1:]
    )


def prepare(input_path, output_dir=None, tier="high", max_edge=None, all_frames=False):
    source = Path(input_path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"图片不存在或不是文件: {source}")
    if tier not in TIERS:
        raise ValueError(f"未知档位 {tier!r}；可选值: {', '.join(TIERS)}")
    edge, tokens = TIERS[tier]
    if max_edge is not None:
        if max_edge < 200:
            raise ValueError("--max-edge 不能小于 200")
        edge = min(edge, max_edge)

    requested = None
    if output_dir is not None:
        requested = Path(output_dir).expanduser().resolve()
        if requested.exists() and (not requested.is_dir() or any(requested.iterdir())):
            raise ValueError(f"--output-dir 必须是不存在或为空的目录: {requested}")
    output = _Output(requested)

    notes, pages, files = [], [], []
    overlapping = False
    with image_io.open_image(source) as image:
        fmt = image.format
        frames = image_io.frame_count(image)
        unit = "页" if fmt == "TIFF" else "帧"
        expand = frames > 1 and (fmt == "TIFF" or all_frames)
        if expand:
            notes.append(f"共 {frames} {unit}，已按顺序展开。")
        elif frames > 1:
            notes.append(f"文件含 {frames} 帧，只读取第一帧；需要全部帧时加 --all-frames。")
        if fmt not in image_io.READABLE_FORMATS:
            notes.append(f"{fmt} 不是视觉输入支持的格式，已转换。")

        for index in range(frames) if expand else [0]:
            number = index + 1
            label = f"第 {number} {unit}" if expand else "图片"
            frame = image_io.read_frame(image, index)
            width, height = frame.image.size
            boxes = split_image.plan_tiles(frame.image, edge, tokens)
            pages.append({
                "page": number,
                "width": width,
                "height": height,
                "orientation_applied": frame.orientation,
                "converted_from": frame.converted_from,
            })
            if frame.orientation:
                notes.append(f"{label}已按 EXIF 方向 {frame.orientation} 摆正。")
            if frame.converted_from:
                notes.append(f"{label}的颜色模式 {frame.converted_from} 已转换为 {frame.image.mode}。")
            if len(boxes) > 1:
                notes.append(
                    f"{label}超过 {tier} 档原生分辨率（长边 {edge} px、{tokens} visual tokens），"
                    f"已切成 {len(boxes)} 个分片。"
                )
                overlapping = overlapping or _overlaps(boxes)

            passthrough = (
                frames == 1
                and fmt in image_io.READABLE_FORMATS
                and frame.orientation is None
                and frame.converted_from is None
                and len(boxes) == 1
                and source.stat().st_size <= image_io.MAX_BYTES
            )
            if passthrough:
                files.append({"path": str(source), "page": number, "box": [0, 0, width, height]})
                continue

            for tile_number, box in enumerate(boxes, 1):
                tile = frame.image if len(boxes) == 1 else frame.image.crop(box)
                path, quality = output.write(f"page{number:03d}_tile{tile_number:02d}", tile)
                if quality:
                    notes.append(f"{path.name} 以 JPEG（质量 {quality}）保存，以满足单图大小上限。")
                files.append({"path": str(path), "page": number, "box": list(box)})

    if overlapping:
        notes.append("box 有交叠的相邻分片包含重复内容，拼接时只保留一次。")

    return {
        "source": {"path": str(source), "format": fmt, "frames": frames},
        "limits": {"tier": tier, "max_edge": edge, "max_visual_tokens": tokens},
        "output_dir": str(output.path) if output.path else None,
        "pages": pages,
        "files_to_read": files,
        "notes": notes,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="为视觉读取准备图片，并在标准输出返回 JSON。")
    parser.add_argument("image", help="图片路径")
    parser.add_argument("--output-dir", help="写入生成文件的目录，必须不存在或为空；默认新建系统临时目录")
    parser.add_argument("--tier", choices=list(TIERS), default="high", help="模型原生分辨率档位")
    parser.add_argument("--max-edge", type=int, help="分片长边上限（不小于 200），例如 2000；只会收紧档位限制")
    parser.add_argument("--all-frames", action="store_true", help="展开动画或多帧文件的全部帧")
    args = parser.parse_args(argv)
    try:
        result = prepare(args.image, args.output_dir, args.tier, args.max_edge, args.all_frames)
    except (OSError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
