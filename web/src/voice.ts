import { fetchAsrStatus, transcribe } from "./api.js";
import { MAX_RECORD_MS } from "./constants.js";
import { $ } from "./dom.js";

/** 浏览器在线识别（Web Speech API）实例，同一时刻只允许一个。 */
let onlineRecognition: SpeechRecognitionLike | null = null;

/** 本地录音状态。 */
let recorder: MediaRecorder | null = null;
let mediaStream: MediaStream | null = null;
let chunks: Blob[] = [];
let recordTimer: number | null = null;
let pressing = false;
let starting = false;
let audioContextCtor: typeof AudioContext | null = null;

/** 本地识别就绪时的固定提示，首次加载与轮询就绪共用。 */
const READY_HINT = "本地识别已就绪 · 按住说话，音频不出本机";

/** 在线识别的错误码 → 可读提示。 */
const RECOGNITION_ERRORS: Record<string, string> = {
  "not-allowed": "麦克风或语音服务权限被拒绝，请在浏览器地址栏中允许麦克风",
  "service-not-allowed": "浏览器禁用了在线语音识别，请换用最新版 Chrome 或 Edge",
  "no-speech": "没有听到声音，请靠近麦克风后重试",
  network: "浏览器在线语音服务连接失败，请更换网络或改用 Edge；也可以直接文字输入",
  "audio-capture": "没有找到可用麦克风，请检查设备连接和系统权限",
  "language-not-supported": "当前浏览器不支持中文语音识别，请换用最新版 Chrome 或 Edge",
  aborted: "语音识别已停止",
};

/** 停掉在线识别（发送消息或开始本地录音前调用）。 */
export function stopOnlineRecognition(): void {
  if (!onlineRecognition) return;
  try {
    onlineRecognition.stop();
  } catch {
    // 已经停止时 stop() 可能抛错，忽略
  }
}

/** 停掉本地录音；onstop 里会继续走识别流程。 */
export function stopRecording(): void {
  pressing = false;
  if (recorder && recorder.state !== "inactive") {
    try {
      recorder.stop();
    } catch {
      // 录音器已被浏览器回收，忽略
    }
  }
}

/**
 * 按住说话：pointerdown 开始、pointerup / 移出 / 取消结束。
 * 同时支持键盘空格，保证不用鼠标也能录。
 */
export function bindHoldToRecord(
  button: HTMLButtonElement,
  start: () => void,
  stop: () => void,
): void {
  let pointerId: number | null = null;

  const release = (event: PointerEvent): void => {
    if (pointerId === null || event.pointerId !== pointerId) return;
    pointerId = null;
    stop();
  };

  button.onclick = (event) => event.preventDefault();
  button.onpointerdown = (event) => {
    if (button.disabled || pointerId !== null || (event.pointerType === "mouse" && event.button !== 0)) return;
    event.preventDefault();
    pointerId = event.pointerId;
    try {
      button.setPointerCapture(pointerId);
    } catch {
      // 个别浏览器不支持指针捕获，靠 pointerleave 兜底
    }
    start();
  };
  button.onpointerup = release;
  button.onpointercancel = release;
  button.onpointerleave = release;
  button.onkeydown = (event) => {
    if (event.code === "Space" && !event.repeat) {
      event.preventDefault();
      start();
    }
  };
  button.onkeyup = (event) => {
    if (event.code === "Space") {
      event.preventDefault();
      stop();
    }
  };
}

/** 在线识别兜底：本地模型不可用时使用浏览器自带能力。 */
export function setupVoiceInput(): void {
  const button = $<HTMLButtonElement>("voice");
  const status = $("voiceStatus");
  const RecognitionCtor = window.SpeechRecognition || window.webkitSpeechRecognition;

  if (!RecognitionCtor) {
    button.disabled = true;
    button.textContent = "语音不可用";
    status.textContent = "当前浏览器不支持语音输入，建议使用 Chrome 或 Edge";
    return;
  }
  if (!window.isSecureContext) {
    button.disabled = true;
    button.textContent = "语音不可用";
    status.textContent = "语音输入需要 HTTPS 或 localhost";
    return;
  }

  const start = (): void => {
    if (onlineRecognition) return;
    const recognition = new RecognitionCtor();
    onlineRecognition = recognition;
    recognition.lang = "zh-CN";
    recognition.continuous = true;
    recognition.interimResults = true;

    const input = $<HTMLTextAreaElement>("input");
    const base = input.value.trim();
    let finalText = "";
    let errorText = "";

    recognition.onstart = () => {
      button.textContent = "松开结束";
      button.classList.add("recording");
      status.textContent = "正在录音，松开或移出按钮结束";
    };
    recognition.onresult = (event) => {
      let interim = "";
      for (let i = event.resultIndex; i < event.results.length; i += 1) {
        const transcript = event.results[i][0].transcript;
        if (event.results[i].isFinal) finalText += transcript;
        else interim += transcript;
      }
      input.value = [base, finalText + interim].filter(Boolean).join(" ");
    };
    recognition.onerror = (event) => {
      errorText = RECOGNITION_ERRORS[event.error] || `语音识别失败（${event.error}），请重试或直接文字输入`;
      status.textContent = errorText;
    };
    recognition.onnomatch = () => {
      status.textContent = "没有听清，请再说一次，或直接文字输入";
    };
    recognition.onend = () => {
      onlineRecognition = null;
      button.textContent = "按住说话";
      button.classList.remove("recording");
      if (!errorText) status.textContent = finalText ? "已填入，可修改后发送" : "没有识别到内容";
    };

    try {
      recognition.start();
    } catch {
      onlineRecognition = null;
      status.textContent = "语音启动失败，请重试";
    }
  };

  bindHoldToRecord(button, start, stopOnlineRecognition);
}

/** 把 16bit 单声道采样封成 WAV，后端按 WAV 解析。 */
function encodeWav(samples: Float32Array, sampleRate: number): Blob {
  const buffer = new ArrayBuffer(44 + samples.length * 2);
  const view = new DataView(buffer);
  const writeText = (offset: number, text: string): void => {
    for (let i = 0; i < text.length; i += 1) view.setUint8(offset + i, text.charCodeAt(i));
  };

  writeText(0, "RIFF");
  view.setUint32(4, 36 + samples.length * 2, true);
  writeText(8, "WAVE");
  writeText(12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  writeText(36, "data");
  view.setUint32(40, samples.length * 2, true);

  let offset = 44;
  for (let i = 0; i < samples.length; i += 1) {
    const value = Math.max(-1, Math.min(1, samples[i]));
    view.setInt16(offset, value < 0 ? value * 0x8000 : value * 0x7fff, true);
    offset += 2;
  }
  return new Blob([buffer], { type: "audio/wav" });
}

/** 把浏览器录到的音频解码成 16kHz 单声道 WAV。 */
async function toWav(blob: Blob): Promise<Blob> {
  const Ctor = audioContextCtor;
  if (!Ctor) throw new Error("当前浏览器不支持音频解码");
  const context = new Ctor();
  try {
    const decoded = await context.decodeAudioData(await blob.arrayBuffer());
    const channels = decoded.numberOfChannels;
    const length = decoded.length;
    const mono = new Float32Array(length);
    for (let channel = 0; channel < channels; channel += 1) {
      const data = decoded.getChannelData(channel);
      for (let i = 0; i < length; i += 1) mono[i] += data[i] / channels;
    }

    const targetRate = 16000;
    const ratio = decoded.sampleRate / targetRate;
    const outLength = Math.floor(length / ratio);
    const output = new Float32Array(outLength);
    for (let i = 0; i < outLength; i += 1) {
      const position = i * ratio;
      const left = Math.floor(position);
      const right = Math.min(left + 1, length - 1);
      const weight = position - left;
      output[i] = mono[left] * (1 - weight) + mono[right] * weight;
    }
    return encodeWav(output, targetRate);
  } finally {
    try {
      await context.close();
    } catch {
      // 关闭失败不影响结果
    }
  }
}

/** 本地录音 + SenseVoice 转写。 */
export function setupLocalVoice(): void {
  const button = $<HTMLButtonElement>("voice");
  const status = $("voiceStatus");
  const Ctor = window.AudioContext || window.webkitAudioContext;

  if (!window.MediaRecorder || !Ctor) return;
  if (!window.isSecureContext) return;
  if (typeof navigator.mediaDevices?.getUserMedia !== "function") return;
  audioContextCtor = Ctor;

  const input = $<HTMLTextAreaElement>("input");

  const useOnline = (): void => {
    setupVoiceInput();
    if (!button.disabled) button.textContent = "按住说话";
  };

  const upload = async (blob: Blob): Promise<void> => {
    const base = input.value.trim();
    status.textContent = "本地识别中…（首次要加载模型，稍等几秒）";
    try {
      const wav = await toWav(blob);
      if (wav.size < 6000) throw new Error("录音太短");
      const result = await transcribe(wav);
      if (!result.text) {
        status.textContent = "没有识别到内容，请再说一次";
        return;
      }
      input.value = [base, result.text].filter(Boolean).join(" ");
      const meta = [result.languageText, result.emotionText].filter(Boolean).join(" · ");
      status.textContent = `本地识别完成${meta ? `（${meta}）` : ""}，可修改后发送`;
    } catch (error) {
      useOnline();
      const message = error instanceof Error ? error.message : String(error);
      status.textContent = `本地识别失败：${message}，下次按住按钮将使用在线识别`;
    }
  };

  const startRec = async (): Promise<void> => {
    if (recorder || starting) return;
    stopOnlineRecognition();
    pressing = true;
    starting = true;
    status.textContent = "正在请求麦克风权限…";

    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
      });
      if (!pressing) {
        stream.getTracks().forEach((track) => track.stop());
        return;
      }
      mediaStream = stream;

      const mimeTypes = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4", "audio/ogg;codecs=opus"];
      const mime = mimeTypes.filter((type) => window.MediaRecorder.isTypeSupported(type))[0] || "";

      chunks = [];
      try {
        recorder = mime ? new MediaRecorder(stream, { mimeType: mime }) : new MediaRecorder(stream);
      } catch {
        stream.getTracks().forEach((track) => track.stop());
        mediaStream = null;
        useOnline();
        status.textContent = "当前浏览器无法本地录音，已改用在线识别";
        return;
      }

      recorder.ondataavailable = (event) => {
        if (event.data && event.data.size) chunks.push(event.data);
      };
      recorder.onstop = () => {
        if (recordTimer !== null) {
          window.clearTimeout(recordTimer);
          recordTimer = null;
        }
        recorder = null;
        if (mediaStream) {
          mediaStream.getTracks().forEach((track) => track.stop());
          mediaStream = null;
        }
        button.textContent = "按住说话";
        button.classList.remove("recording");

        const blob = new Blob(chunks, { type: mime || "audio/webm" });
        chunks = [];
        if (!blob.size) {
          status.textContent = "没有录到声音，请重试";
          return;
        }
        void upload(blob);
      };

      recorder.start();
      button.textContent = "松开结束";
      button.classList.add("recording");
      status.textContent = "正在录音，松开或移出按钮结束（最长 60 秒）";
      recordTimer = window.setTimeout(stopRecording, MAX_RECORD_MS);
    } catch (error) {
      if (pressing) {
        useOnline();
        const name = error instanceof DOMException ? error.name : "错误";
        status.textContent = `麦克风不可用（${name}），下次按住按钮将使用在线识别`;
      }
    } finally {
      starting = false;
    }
  };

  const useLocal = (): void => {
    button.disabled = false;
    button.textContent = "按住说话";
    bindHoldToRecord(button, () => void startRec(), stopRecording);
  };

  useLocal();

  // 首次启动时依赖可能还在后台安装，轮询到有结论为止（最多 10 分钟）
  const pollDeps = (): void => {
    let attempts = 0;
    const timer = window.setInterval(() => {
      attempts += 1;
      if (attempts > 120) {
        window.clearInterval(timer);
        return;
      }
      void fetchAsrStatus().then((asr) => {
        if (!asr) return;
        if (asr.available) {
          window.clearInterval(timer);
          useLocal();
          status.textContent = READY_HINT;
        } else if (asr.deps && ["failed", "missing", "disabled"].includes(asr.deps.state)) {
          window.clearInterval(timer);
          useOnline();
          status.textContent = "本地语音不可用，按住按钮将使用浏览器在线识别";
        }
      });
    }, 5000);
  };

  void fetchAsrStatus().then((asr) => {
    if (asr && asr.available) {
      // 就绪提示中说明音频仅在本机识别。
      status.textContent = asr.cached ? READY_HINT : "本地识别就绪，但权重未随包分发，首次识别会联网下载约 900MB";
      return;
    }
    if (asr && asr.deps && asr.deps.state === "installing") {
      status.textContent = "正在后台安装本地语音依赖，装好后按住按钮即可说话";
      pollDeps();
      return;
    }
    useOnline();
    if (asr && asr.reason) status.textContent = `本地识别不可用（${asr.reason}），按住按钮将使用在线识别`;
  });
}
