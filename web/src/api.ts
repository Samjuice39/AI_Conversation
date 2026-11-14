import { STREAM_IDLE_TIMEOUT_MS } from "./constants.js";
import type { AsrStatus, ChatRequest, Checkin, StreamEvent } from "./types.js";

/** 后端统一的响应信封：成功带 ok:true，失败带 ok:false + error。 */
interface ApiEnvelope {
  ok: boolean;
  error?: string;
}

/** 本地识别返回的转写结果。 */
export interface AsrResult {
  text: string;
  languageText?: string;
  emotionText?: string;
}

/** 流式过程中收集到的结果，用对象承载以便在回调里写入。 */
interface StreamProgress {
  finished: boolean;
  result: Checkin | null;
  failure: string;
}

/**
 * 统一的 JSON 请求。
 *
 * 401 说明会话过期，直接回登录页——继续留在页面上只会让后续请求全部失败。
 */
export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(path, options);
  const data = (await response.json().catch(() => ({}))) as Partial<ApiEnvelope>;
  if (response.status === 401) {
    location.href = "/";
    throw new Error("请先登录");
  }
  if (!response.ok || !data.ok) {
    throw new Error(data.error || `HTTP ${response.status}`);
  }
  return data as T;
}

/** 提交 JSON 的便捷包装。 */
export function postJson<T>(path: string, body: unknown, method = "POST"): Promise<T> {
  return api<T>(path, {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

/**
 * 读取 SSE 流，返回最终落库的那条记录。
 *
 * 后端按 "data: {...}\n\n" 分帧，这里按空行切块再逐行解析；每收到一块就重置
 * 静默超时，因此"模型在思考"不会被误判成超时，真正卡住才会中断。
 * 中途的 status / trace / token 事件通过 onEvent 交给界面绘制。
 */
export async function streamChat(
  body: ChatRequest,
  onEvent: (event: StreamEvent) => void,
): Promise<Checkin> {
  const controller = new AbortController();
  const progress: StreamProgress = { finished: false, result: null, failure: "" };
  let timer: number | null = null;

  const stopTimer = (): void => {
    if (timer !== null) window.clearTimeout(timer);
    timer = null;
  };
  const arm = (): void => {
    stopTimer();
    timer = window.setTimeout(() => controller.abort(), STREAM_IDLE_TIMEOUT_MS);
  };
  arm();

  let response: Response;
  try {
    response = await fetch("/api/chat/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal: controller.signal,
    });
  } catch {
    stopTimer();
    throw new Error(controller.signal.aborted ? "等待 AI 超时，请重试" : "网络连接失败，请重试");
  }

  if (response.status === 401) {
    stopTimer();
    location.href = "/";
    throw new Error("请先登录");
  }
  if (!response.ok || !response.body) {
    stopTimer();
    let message = `HTTP ${response.status}`;
    try {
      const detail = (await response.json()) as { error?: string };
      if (detail && detail.error) message = detail.error;
    } catch {
      // 响应不是 JSON，保留状态码
    }
    throw new Error(message);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  const handleLine = (line: string): void => {
    if (!line.startsWith("data:")) return;
    let event: StreamEvent;
    try {
      event = JSON.parse(line.slice(5).trim()) as StreamEvent;
    } catch {
      return;
    }
    if (event.type === "final") {
      progress.result = event.result;
      progress.finished = true;
    } else if (event.type === "error") {
      progress.failure = event.message;
      progress.finished = true;
    }
    onEvent(event);
  };

  try {
    for (;;) {
      const chunk = await reader.read();
      if (chunk.done) break;
      arm();
      buffer += decoder.decode(chunk.value, { stream: true });
      let index = buffer.indexOf("\n\n");
      while (index >= 0) {
        const block = buffer.slice(0, index);
        buffer = buffer.slice(index + 2);
        block.split("\n").forEach(handleLine);
        index = buffer.indexOf("\n\n");
      }
      if (progress.finished) break;
    }
    if (!progress.finished && buffer.trim()) buffer.split("\n").forEach(handleLine);
  } catch {
    throw new Error(controller.signal.aborted ? "AI 响应超时，请重试" : "连接中断，请重试");
  } finally {
    stopTimer();
  }

  if (progress.failure) throw new Error(progress.failure);
  if (!progress.result) throw new Error("没有收到完整回应，请重试");
  return progress.result;
}

/** 上传一段 WAV 交给本地 SenseVoice 转写。 */
export async function transcribe(blob: Blob): Promise<AsrResult> {
  const form = new FormData();
  form.append("audio", blob, "voice.wav");
  form.append("language", "auto");
  const response = await fetch("/api/asr", { method: "POST", body: form });
  const data = (await response.json().catch(() => ({}))) as Partial<AsrResult> & Partial<ApiEnvelope>;
  if (!response.ok || !data.ok) throw new Error(data.error || `HTTP ${response.status}`);
  return {
    text: (data.text || "").trim(),
    languageText: data.languageText,
    emotionText: data.emotionText,
  };
}

/** 查询本地语音是否可用；查询失败时返回 null，由调用方决定降级。 */
export async function fetchAsrStatus(): Promise<AsrStatus | null> {
  try {
    const data = await api<{ asr?: AsrStatus }>("/api/asr/status");
    return data.asr ?? null;
  } catch {
    return null;
  }
}
