import json
import os
import shutil
import threading
import wave

_MODEL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models", "vosk-model-small-cn-0.22")
_model = None
_model_lock = threading.Lock()


def _ascii_model_cache():
    """Vosk 的 Windows 原生库读不了中文路径，必要时复制到纯英文缓存目录。"""
    for root in (os.environ.get("TEMP"), os.environ.get("PUBLIC")):
        if root and root.isascii():
            return os.path.join(root, "emotion-planet-vosk-cn-0.22")
    return None


def _load_model(Model):
    if _MODEL_PATH.isascii():
        return Model(_MODEL_PATH)
    cache = _ascii_model_cache()
    if not cache:
        raise RuntimeError("项目路径含中文，且找不到可用的英文缓存目录；请把项目移到纯英文路径")
    marker = os.path.join(cache, ".ready")
    if not os.path.isfile(marker):
        staging = cache + ".tmp"
        if os.path.exists(staging):
            shutil.rmtree(staging)
        shutil.copytree(_MODEL_PATH, staging)
        open(os.path.join(staging, ".ready"), "w", encoding="utf-8").close()
        if os.path.exists(cache):
            shutil.rmtree(cache)
        os.replace(staging, cache)
    return Model(cache)


def _get_model():
    global _model
    if _model is not None:
        return _model
    with _model_lock:
        if _model is None:
            if not os.path.isdir(_MODEL_PATH):
                raise RuntimeError("离线语音模型缺失，请保留 models/vosk-model-small-cn-0.22 文件夹")
            try:
                from vosk import Model, SetLogLevel
            except ImportError:
                raise RuntimeError("缺少语音识别依赖，请执行 python -m pip install -r requirements.txt")
            SetLogLevel(-1)
            _model = _load_model(Model)
    return _model


def transcribe_wav(file_storage):
    try:
        with wave.open(file_storage.stream, "rb") as wav:
            if wav.getnchannels() != 1 or wav.getsampwidth() != 2 or wav.getframerate() != 16000:
                raise ValueError("录音格式应为 16 kHz、16 位单声道 WAV")
            duration = wav.getnframes() / wav.getframerate()
            if not 0.2 <= duration <= 30:
                raise ValueError("录音时长应在 0.2～30 秒之间")
            pcm = wav.readframes(wav.getnframes())
    except (wave.Error, EOFError):
        raise ValueError("录音文件损坏或格式不正确")

    from vosk import KaldiRecognizer
    recognizer = KaldiRecognizer(_get_model(), 16000)
    parts = []
    for offset in range(0, len(pcm), 8000):
        if recognizer.AcceptWaveform(pcm[offset:offset + 8000]):
            parts.append(json.loads(recognizer.Result()).get("text", ""))
    parts.append(json.loads(recognizer.FinalResult()).get("text", ""))
    return "".join(parts).replace(" ", "").strip()
