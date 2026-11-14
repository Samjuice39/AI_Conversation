import { api, postJson } from "./api.js";
import { sendMessage } from "./chat.js";
import {
  closeTopics,
  createConversation,
  loadConversations,
  openTopics,
  removeConversation,
  switchConversation,
} from "./conversations.js";
import { bindDialogs, refreshAIStatus } from "./dialogs.js";
import { $, closestWith, qsa } from "./dom.js";
import { refresh } from "./insights.js";
import { bindPaneNav } from "./panes.js";
import { renderStats, setMood } from "./render.js";
import { state } from "./state.js";
import { setupLocalVoice, setupVoiceInput } from "./voice.js";
import type { Feedback, Stats } from "./types.js";

const reportError = (error: unknown): void => {
  alert(error instanceof Error ? error.message : String(error));
};

/** 输入框、发送按钮、快捷短语。 */
function bindComposer(): void {
  const input = $<HTMLTextAreaElement>("input");
  const send = $<HTMLButtonElement>("send");

  send.addEventListener("click", () => {
    void sendMessage();
  });

  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      if (!send.disabled) void sendMessage();
    }
  });

  qsa<HTMLButtonElement>(".quick button").forEach((button) => {
    button.addEventListener("click", () => {
      input.value = button.dataset.text || "";
      input.focus();
    });
  });
}

/** 话题抽屉：开关、新建、切换、删除。 */
function bindTopics(): void {
  $("topicToggle").addEventListener("click", openTopics);
  $("drawerScrim").addEventListener("click", closeTopics);
  $("newConversation").addEventListener("click", () => {
    void createConversation().catch(reportError);
  });

  $("conversationList").addEventListener("click", (event) => {
    const deleteButton = closestWith<HTMLButtonElement>(event.target, "[data-delete-conversation]");
    if (deleteButton) {
      void removeConversation(Number(deleteButton.dataset.deleteConversation), deleteButton).catch(reportError);
      return;
    }
    const openButton = closestWith<HTMLElement>(event.target, "[data-conversation]");
    if (openButton) {
      void switchConversation(Number(openButton.dataset.conversation), true).catch(reportError);
    }
  });
}

/** 单条反馈：记下后立刻更新"回应有帮助"统计。 */
async function submitFeedback(button: HTMLButtonElement): Promise<void> {
  const id = button.dataset.id;
  const feedback = button.dataset.feedback as Feedback | undefined;
  if (!id || !feedback) return;

  try {
    await postJson(`/api/checkins/${id}/feedback`, { feedback });
    const wrapper = button.parentElement;
    if (wrapper) wrapper.innerHTML = "<small>反馈已记住，会用于后续对话</small>";

    const record = state.records.find((item) => String(item.id) === id);
    if (record) record.feedback = feedback;

    const data = await api<{ stats: Stats }>("/api/insights");
    renderStats(data.stats);
  } catch (error) {
    reportError(error);
  }
}

/**
 * 消息区的点击委托。
 *
 * 消息是动态插入的，逐条绑定会随对话增长不断叠加监听器，
 * 因此统一在 document 上按 data 属性分发。
 */
function bindMessageActions(): void {
  document.addEventListener("click", (event) => {
    const feedbackButton = closestWith<HTMLButtonElement>(event.target, "[data-feedback]");
    if (feedbackButton) {
      void submitFeedback(feedbackButton);
      return;
    }

    const historyButton = closestWith<HTMLButtonElement>(event.target, "[data-history]");
    if (!historyButton) return;
    const id = historyButton.dataset.history;
    const record = state.records.find((item) => String(item.id) === id);
    if (!record) return;
    if (record.conversationId) {
      void switchConversation(record.conversationId, true).catch(reportError);
    } else {
      setMood(record);
    }
  });

  $("logout").addEventListener("click", async () => {
    try {
      await api("/api/auth/logout", { method: "POST" });
    } finally {
      location.href = "/";
    }
  });
}

/** 首屏：读账号、拉统计、打开最近话题。 */
async function bootstrap(): Promise<void> {
  bindPaneNav();
  bindComposer();
  bindTopics();
  bindMessageActions();
  bindDialogs();
  setupVoiceInput();
  setupLocalVoice();

  try {
    const me = await api<{ username: string; preferredName?: string | null }>("/api/auth/me");
    $("username").textContent = me.username;

    const preferredName = $<HTMLInputElement>("preferredName");
    if (!preferredName.value && me.preferredName) preferredName.value = me.preferredName;

    await refreshAIStatus();
    await refresh();
    await loadConversations(true);
  } catch (error) {
    $("modelBadge").textContent = error instanceof Error ? error.message : String(error);
  }
}

void bootstrap();
