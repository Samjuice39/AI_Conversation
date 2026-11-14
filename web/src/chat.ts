import { streamChat } from "./api.js";
import { loadConversations } from "./conversations.js";
import { $ } from "./dom.js";
import { refresh } from "./insights.js";
import { addMessage, renderHistory, setMood } from "./render.js";
import { state } from "./state.js";
import { stopOnlineRecognition, stopRecording } from "./voice.js";
import type { ChatRequest, ContextKey, Persona } from "./types.js";

/** 从表单里取出本次请求参数。 */
function buildRequest(text: string): ChatRequest {
  const sleep = $<HTMLSelectElement>("sleep").value;
  return {
    text,
    conversationId: state.currentConversationId,
    persona: $<HTMLSelectElement>("persona").value as Persona,
    customPersona: $<HTMLInputElement>("customPersona").value.trim(),
    preferredName: $<HTMLInputElement>("preferredName").value.trim(),
    context: $<HTMLSelectElement>("context").value as ContextKey,
    sleepQuality: sleep ? Number(sleep) : null,
  };
}

/** 发送后刷新话题列表与图表，失败不影响主流程。 */
function syncAfterSend(): void {
  loadConversations(false)
    .then(() => refresh(false))
    .catch(() => {
      // 图表刷新失败不该弹窗打断对话
    });
}

/**
 * 发送一条消息。
 *
 * 先用一个占位气泡承接 status / token 事件，逐字渲染；收到 final 后用正式
 * 结构替换。失败时把已生成的内容保留在气泡里，并把原文放回输入框方便重试。
 */
export async function sendMessage(): Promise<void> {
  stopRecording();
  stopOnlineRecognition();

  const input = $<HTMLTextAreaElement>("input");
  const text = input.value.trim();
  if (text.length < 2) return;

  const requestedConversationId = state.currentConversationId;
  const button = $<HTMLButtonElement>("send");
  button.disabled = true;
  button.textContent = "AI 思考中";

  const messages = $("messages");
  const userBubble = document.createElement("div");
  userBubble.className = "message user";
  userBubble.textContent = text;
  messages.appendChild(userBubble);

  const bubble = document.createElement("div");
  bubble.className = "message ai loading";
  bubble.textContent = "正在结合你的描述与近期记忆…";
  messages.appendChild(bubble);

  input.value = "";
  messages.scrollTop = messages.scrollHeight;

  let accumulated = "";
  const release = (): void => {
    button.disabled = false;
    button.textContent = "发送给 AI";
  };
  const paint = (value: string): void => {
    bubble.classList.remove("loading");
    bubble.textContent = value;
    messages.scrollTop = messages.scrollHeight;
  };

  try {
    const result = await streamChat(buildRequest(text), (event) => {
      if (event.type === "token") {
        accumulated += event.text;
        paint(accumulated);
      } else if (event.type === "status") {
        if (!accumulated) paint(`${event.message}…`);
      } else if (event.type === "final") {
        release();
      }
    });

    bubble.remove();
    if (requestedConversationId === state.currentConversationId) {
      state.currentConversationId = result.conversationId;
      addMessage(result);
      setMood(result);
    }
    state.records.unshift(result);
    renderHistory();
    syncAfterSend();
  } catch (error) {
    bubble.classList.remove("loading");
    const message = error instanceof Error ? error.message : String(error);
    bubble.textContent = `${accumulated ? `${accumulated}\n\n` : ""}${message}`;
    input.value = text;
  } finally {
    release();
  }
}
