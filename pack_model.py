# -*- coding: utf-8 -*-
"""把本机 modelscope 缓存里的语音模型导出到项目 models/ 目录。

导出之后项目就是自包含的：把整个文件夹打包发给别人，对方装好
funasr / torch 就能直接用本地识别，不用联网，也不用再下 900MB 权重。

    python pack_model.py             # 导出到 ./models
    python pack_model.py --force     # 目标已存在时覆盖
    python pack_model.py --download  # 本机缓存也没有时，先联网下载再导出
    python pack_model.py --dest D:\\某处\\models

导出完成后，sensevoice_asr.py 会自动优先使用项目内的模型，
/api/asr/status 里的 source 会变成 "bundled"。
"""

import argparse
import os
import shutil
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

# (modelscope 缓存目录名, 项目内目录名, hub 上的模型 id)
TARGETS = [
    ("iic--SenseVoiceSmall", "SenseVoiceSmall", "iic/SenseVoiceSmall"),
    ("iic--speech_fsmn_vad_zh-cn-16k-common-pytorch", "fsmn-vad",
     "iic/speech_fsmn_vad_zh-cn-16k-common-pytorch"),
]


def human(num):
    for unit in ("B", "KB", "MB", "GB"):
        if num < 1024 or unit == "GB":
            return f"{num:.1f}{unit}" if unit != "B" else f"{num}B"
        num /= 1024


def cache_root():
    return Path(os.environ.get("MODELSCOPE_CACHE") or (Path.home() / ".cache" / "modelscope"))


def find_snapshot(cache_name):
    """缓存里找带 model.pt 的那个快照目录。"""
    snapshots = cache_root() / "models" / cache_name / "snapshots"
    if not snapshots.is_dir():
        return None
    for sub in snapshots.iterdir():
        if (sub / "model.pt").is_file():
            return sub
    return None


def dir_size(path):
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def export_one(src, dst, force):
    if dst.exists():
        if not force:
            print(f"  [跳过] {dst.name} 已存在（加 --force 覆盖）")
            return False
        shutil.rmtree(dst)
    print(f"  [复制] {src}  ->  {dst}  ({human(dir_size(src))})")
    shutil.copytree(src, dst)
    return True


def main():
    parser = argparse.ArgumentParser(
        description="导出本地语音模型到项目 models/ 目录",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--dest", default=str(BASE_DIR / "models"), help="目标目录")
    parser.add_argument("--force", action="store_true", help="覆盖已存在的目标目录")
    parser.add_argument("--download", action="store_true", help="本机没有缓存时先联网下载")
    args = parser.parse_args()

    dest = Path(args.dest).resolve()
    print(f"缓存目录：{cache_root()}")
    print(f"导出到：  {dest}\n")

    copied, failed = [], []
    for cache_name, local_name, model_id in TARGETS:
        src = find_snapshot(cache_name)
        if src is None and args.download:
            print(f"  [下载] {model_id} ...")
            try:
                from modelscope import snapshot_download
                snapshot_download(model_id)
            except Exception as exc:  # noqa: BLE001
                failed.append(f"{local_name}: 下载失败 {exc}")
                continue
            src = find_snapshot(cache_name)

        if src is None:
            failed.append(f"{local_name}: 缓存里没有找到，请先运行一次识别或加 --download")
            continue

        try:
            if export_one(src, dest / local_name, args.force):
                copied.append(local_name)
        except Exception as exc:  # noqa: BLE001
            failed.append(f"{local_name}: 复制失败 {exc}")

    print()
    if copied:
        total = dir_size(dest)
        print(f"完成：已导出 {', '.join(copied)}，models/ 共 {human(total)}")
        print("现在可以整个文件夹打包发送；对方装好 funasr/torch 即可离线识别。")
    if failed:
        print("未完成的部分：")
        for item in failed:
            print(f"  - {item}")
        return 1
    return 0


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass
    sys.exit(main())
