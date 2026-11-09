#!/usr/bin/env python3
"""裁剪图片中的视觉元素，或把 Markdown 与同级 img/ 目录打包为 ZIP。"""

from __future__ import annotations

import argparse
import math
import os
import sys
import zipfile
from pathlib import Path

# 允许以 python3 -I 运行：隔离模式不会把脚本目录加入 sys.path。
sys.path.insert(0, str(Path(__file__).resolve().parent))

SCALES = {"pixels": None, "unit": 1.0, "thousand": 1000.0}


def to_pixel_box(coords, width, height, coordinate_system):
    """把指定坐标系中的矩形换算为像素框；左上取下整、右下取上整。"""
    if coordinate_system not in SCALES:
        raise ValueError(f"未知坐标系 {coordinate_system!r}；可选值: {', '.join(SCALES)}")
    left, top, right, bottom = coords
    scale = SCALES[coordinate_system]
    if scale is None:
        if not (0 <= left < right <= width and 0 <= top < bottom <= height):
            raise ValueError(
                f"像素坐标必须满足 0 ≤ left < right ≤ {width} 且 0 ≤ top < bottom ≤ {height}"
            )
    else:
        if not (0 <= left < right <= scale and 0 <= top < bottom <= scale):
            raise ValueError(
                f"{coordinate_system} 坐标必须满足 0 ≤ left < right ≤ {scale:g} 且 0 ≤ top < bottom ≤ {scale:g}"
            )
        left, right = left / scale * width, right / scale * width
        top, bottom = top / scale * height, bottom / scale * height
    return (
        math.floor(left),
        math.floor(top),
        min(math.ceil(right), width),
        min(math.ceil(bottom), height),
    )


def crop_image(image_path, output_path, left, top, right, bottom, coordinate_system, page=1):
    """按与 prepare_image.py 相同的规则摆正并规范化图片后裁剪，返回像素框。"""
    import image_io  # 延迟导入，使 zip 不依赖 Pillow

    source = Path(image_path).expanduser().resolve()
    target = Path(output_path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"输入图片不存在或不是文件: {source}")
    if target.suffix.lower() not in image_io.ASSET_SUFFIXES:
        raise ValueError("输出文件扩展名必须是 .png、.jpg 或 .jpeg")

    with image_io.open_image(source) as image:
        frames = image_io.frame_count(image)
        if not 1 <= page <= frames:
            raise ValueError(f"--page 必须在 1 到 {frames} 之间")
        frame = image_io.read_frame(image, page - 1).image

    box = to_pixel_box((left, top, right, bottom), *frame.size, coordinate_system)
    target.parent.mkdir(parents=True, exist_ok=True)
    image_io.save_asset(frame.crop(box), target)
    return box


def package_zip(md_path, zip_path=None):
    """把 Markdown 放在 ZIP 根目录，并保留同级 img/ 的相对路径。"""
    md_path = Path(md_path).expanduser().resolve()
    if not md_path.is_file():
        raise FileNotFoundError(f"Markdown 文件不存在或不是文件: {md_path}")
    zip_path = md_path.with_suffix(".zip") if zip_path is None else Path(zip_path).expanduser().resolve()
    zip_path.parent.mkdir(parents=True, exist_ok=True)

    parent_dir = md_path.parent
    img_dir = parent_dir / "img"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.write(md_path, arcname=md_path.name)
        if img_dir.is_dir():
            for root, _, names in os.walk(img_dir):
                for name in sorted(names):
                    file_path = Path(root) / name
                    archive.write(file_path, arcname=file_path.relative_to(parent_dir).as_posix())
    return zip_path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="裁剪视觉元素，或打包 Markdown 与 img/。")
    commands = parser.add_subparsers(dest="command", required=True)

    crop = commands.add_parser("crop", help="裁剪区域并保存为 .png 或 .jpg")
    crop.add_argument("image", help="源图片：通常是 prepare_image.py 输出的 files_to_read 路径")
    crop.add_argument("output", help="输出路径，扩展名决定格式")
    for name in ("left", "top", "right", "bottom"):
        crop.add_argument(name, type=float)
    crop.add_argument("--coords", choices=list(SCALES), required=True, help="坐标系，必须显式指定")
    crop.add_argument("--page", type=int, default=1, help="多页或多帧源图片的页号，从 1 开始")

    pack = commands.add_parser("zip", help="打包 Markdown 与同级 img/")
    pack.add_argument("markdown")
    pack.add_argument("zip_path", nargs="?")

    args = parser.parse_args(argv)
    try:
        if args.command == "crop":
            box = crop_image(
                args.image, args.output, args.left, args.top, args.right, args.bottom,
                args.coords, args.page,
            )
            print(f"已裁剪像素区域 {list(box)} 并保存至: {Path(args.output).expanduser().resolve()}")
        else:
            print(f"已打包至: {package_zip(args.markdown, args.zip_path)}")
    except (OSError, ValueError) as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
