# -*- coding: utf-8 -*-
"""本地离线语音识别 —— SenseVoice-Small（FunASR）

把一段音频转成文字，同时给出语言、情绪和音频事件（笑声 / 哭声 / 掌声 等）。
全部在本机推理，不依赖浏览器或任何在线语音服务。

命令行：
    python sensevoice_asr.py samples/asr_example_zh.wav
    python sensevoice_asr.py audio.mp3 --json
    python sensevoice_asr.py audio.wav --language zh --no-vad

在代码里调用：
    from sensevoice_asr import transcribe
    result = transcribe("audio.wav")
    print(result["text"], result["emotion_text"])

依赖：funasr、torch、torchaudio（或 soundfile）。wav / mp3 / flac 可直接读，
m4a、aac、webm 这类格式需要系统里装好 ffmpeg。

模型：iic/SenseVoiceSmall，首次运行自动下载到 ~/.cache/modelscope（约 900MB），
之后完全离线。
"""

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

# SenseVoice 的输出是一串富文本标签 + 文本，例如：
#   <|zh|><|NEUTRAL|><|Speech|><|withitn|>今天天气不错
# 标签含义见模型卡：语言 5 种 + 情感 7 种 + 事件 8 种。
LANGUAGE_LABELS = {
    "zh": "中文",
    "en": "英文",
    "yue": "粤语",
    "ja": "日语",
    "ko": "韩语",
    "nospeech": "无语音",
}

EMOTION_LABELS = {
    "HAPPY": "开心",
    "SAD": "悲伤",
    "ANGRY": "愤怒",
    "NEUTRAL": "中性",
    "FEARFUL": "恐惧",
    "DISGUSTED": "厌恶",
    "SURPRISED": "惊讶",
}

# 音频事件识别仅覆盖以上几类，不做通用声音分类。
EVENT_LABELS = {
    "Speech": "语音",
    "BGM": "背景音乐",
    "Applause": "掌声",
    "Laughter": "笑声",
    "Cry": "哭声",
    "Sneeze": "喷嚏",
    "Breath": "呼吸",
    "Cough": "咳嗽",
}

LANGUAGES = ("auto", "zh", "en", "yue", "ja", "ko", "nospeech")

_TAG_RE = re.compile(r"<\|([^|]+)\|>")

BASE_DIR = Path(__file__).resolve().parent
# 随项目一起分发的模型目录。有它就完全自包含，没有就用 modelscope 的全局缓存。
BUNDLED_DIR = BASE_DIR / "models"
MODEL_ID = "iic/SenseVoiceSmall"
VAD_ID = "fsmn-vad"

# 模型加载很慢（首次要读 900MB 权重），所以按 (模型, device, use_vad) 缓存，进程内只加载一次。
_MODEL_CACHE = {}

def is_loaded():
    """模型是否已经在当前进程里加载好（首次识别完成后会变成 True）。"""
    return bool(_MODEL_CACHE)

def bundled_model_path():
    """项目内自带的 SenseVoice 目录；没有返回 None。
    有它时引用的是本地路径，不查 modelscope 缓存、也不联网。
    """
    path = BUNDLED_DIR / "SenseVoiceSmall"
    return str(path) if (path / "model.pt").is_file() else None

def bundled_vad_path():
    """项目内自带的 VAD 目录；没有返回 None。"""
    path = BUNDLED_DIR / "fsmn-vad"
    return str(path) if (path / "model.pt").is_file() else None

def local_weight_path():
    """本机 modelscope 缓存里是否已经有下载好的权重；返回快照目录或 None。
    有权重就能完全离线跑，没有则首次识别会自动联网下载（约 900MB）。
    """
    root = os.environ.get("MODELSCOPE_CACHE") or str(Path.home() / ".cache" / "modelscope")
    snapshots = Path(root) / "models" / "iic--SenseVoiceSmall" / "snapshots"
    if not snapshots.is_dir():
        return None
    for sub in snapshots.iterdir():
        if (sub / "model.pt").is_file():
            return str(sub)
    return None

def model_source():
    """模型从哪来：bundled（项目内自带）/ cache（本机缓存）/ download（首次需联网）。"""
    if bundled_model_path():
        return "bundled"
    return "cache" if local_weight_path() else "download"

def get_model(device="cpu", use_vad=True):
    """加载（或复用）SenseVoice 模型。
    优先用项目内 models/ 的权重（自包含、不联网），否则退回 modelscope 缓存
    （缓存也没有的话，首次调用会下载约 900MB）。
    """
    model_ref = bundled_model_path() or MODEL_ID
    vad_ref = bundled_vad_path() or VAD_ID
    key = (model_ref, vad_ref, device, use_vad)
    if key in _MODEL_CACHE:
        return _MODEL_CACHE[key]
    from funasr import AutoModel
    kwargs = {
        "model": model_ref,
        "trust_remote_code": True,
        "device": device,
        "disable_update": True,
    }
    if use_vad:
        # VAD 负责把长音频切成语音片段，避免整段静音或超长输入拖慢推理。
        kwargs["vad_model"] = vad_ref
        kwargs["vad_kwargs"] = {"max_single_segment_time": 30000}
    model = AutoModel(**kwargs)
    _MODEL_CACHE[key] = model
    return model

def parse_tags(raw):
    """从原始输出里抠出语言 / 情感 / 事件标签。"""
    tags = _TAG_RE.findall(raw or "")
    language = next((t for t in tags if t in LANGUAGE_LABELS), "")
    emotion = next((t for t in tags if t in EMOTION_LABELS), "")
    events = [t for t in tags if t in EVENT_LABELS]
    return {
        "language": language,
        "language_text": LANGUAGE_LABELS.get(language, ""),
        "emotion": emotion,
        "emotion_text": EMOTION_LABELS.get(emotion, ""),
        "events": events,
        "events_text": [EVENT_LABELS[e] for e in events],
    }

def transcribe(audio_path, language="auto", use_vad=True, use_itn=True, device="cpu"):
    """识别单个音频文件。
    参数：
        audio_path: 音频路径（wav / mp3 / flac 等）
        language:   auto 自动判断，或指定 zh / en / yue / ja / ko
        use_vad:    长音频切片，秒级短音频也可以关掉
        use_itn:    数字、时间等做逆文本正则化（"三百" -> "300"）
        device:     cpu 或 cuda:0
    返回 dict：text / language / language_text / emotion / emotion_text /
              events / events_text / raw / elapsed
    """
    path = Path(audio_path)
    if not path.is_file():
        raise FileNotFoundError(f"音频文件不存在：{path}")
    if language not in LANGUAGES:
        raise ValueError(f"language 只能是 {LANGUAGES}，收到 {language!r}")
    if path.suffix.lower() in {".m4a", ".aac", ".webm", ".amr"}:
        print(
            f"[提示] {path.suffix} 需要 ffmpeg 解码，没装的话先转成 wav："
            f"ffmpeg -i {path.name} -ar 16000 -ac 1 out.wav",
            file=sys.stderr,
        )
    from funasr.utils.postprocess_utils import rich_transcription_postprocess
    model = get_model(device=device, use_vad=use_vad)
    gen_kwargs = {
        "input": str(path),
        "cache": {},
        "language": language,
        "use_itn": use_itn,
        "batch_size_s": 60,
    }
    if use_vad:
        gen_kwargs["merge_vad"] = True
        gen_kwargs["merge_length_s"] = 15
    started = time.perf_counter()
    result = model.generate(**gen_kwargs)
    elapsed = round(time.perf_counter() - started, 2)
    raw = result[0].get("text", "") if result else ""
    # rich_transcription_postprocess 会把 <|zh|> 之类标签和特殊空格清理成可读文本；
    # 如果它把文本也改坏了，退回到手工去标签。
    try:
        text = rich_transcription_postprocess(raw).strip()
    except Exception:
        text = _TAG_RE.sub("", raw).replace("▁", " ").strip()
    out = {"text": text, "raw": raw, "elapsed": elapsed}
    out.update(parse_tags(raw))
    return out

def main():
    parser = argparse.ArgumentParser(
        description="SenseVoice-Small 本地语音识别",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("audio", help="音频文件路径")
    parser.add_argument("--language", default="auto", choices=LANGUAGES, help="语言，auto 为自动判断")
    parser.add_argument("--device", default="cpu", help="cpu 或 cuda:0")
    parser.add_argument("--no-vad", action="store_true", help="关闭 VAD 切片（短音频更快）")
    parser.add_argument("--no-itn", action="store_true", help="关闭数字/时间归一化")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出，方便被其他程序调用")
    args = parser.parse_args()
    result = transcribe(
        args.audio,
        language=args.language,
        use_vad=not args.no_vad,
        use_itn=not args.no_itn,
        device=args.device,
    )
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    print(f"文本：{result['text']}")
    meta = [x for x in (result["language_text"], result["emotion_text"]) if x]
    if result["events_text"]:
        meta.append("事件：" + "/".join(result["events_text"]))
    if meta:
        print("识别：" + " | ".join(meta))
    print(f"耗时：{result['elapsed']}s")

if __name__ == "__main__":
    # Windows 控制台默认 GBK，打印中文可能直接抛 UnicodeEncodeError。
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass
    main()
