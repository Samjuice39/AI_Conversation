import { api } from "./api.js";
import { $ } from "./dom.js";
import { refresh } from "./insights.js";
import { showPane } from "./panes.js";
import { addMessage, emptyConversationView, renderConversations, setMood } from "./render.js";
import { state } from "./state.js";
import type { Checkin, Conversation } from "./types.js";

/** 关掉话题抽屉。 */
export function closeTopics(): void {
  $("conversationDrawer").classList.remove("open");
  $("drawerScrim").classList.remove("open");
}

/** 打开话题抽屉。 */
export function openTopics(): void {
  $("conversationDrawer").classList.add("open");
  $("drawerScrim").classList.add("open");
}

/** 切换到某个话题，focus 为真时顺带切回对话页。 */
export async function switchConversation(id: number, focus = false): Promise<void> {
  const data = await api<{ records: Checkin[] }>(`/api/conversations/${id}/messages`);
  state.currentConversationId = Number(id);
  closeTopics();
  renderConversations();

  const messages = $("messages");
  if (!data.records.length) {
    emptyConversationView();
  } else {
    messages.innerHTML = "";
    for (const record of data.records) {
      const bubble = document.createElement("div");
      bubble.className = "message user";
      bubble.textContent = record.text;
      messages.appendChild(bubble);
      addMessage(record);
    }
    setMood(data.records[data.records.length - 1]);
  }
  messages.scrollTop = messages.scrollHeight;
  if (focus) showPane("chat");
}

/** 拉取话题列表；selectLatest 为真时自动打开最近一个话题。 */
export async function loadConversations(selectLatest = false): Promise<void> {
  const data = await api<{ conversations: Conversation[] }>("/api/conversations");
  state.conversations = data.conversations;

  if (selectLatest && state.conversations.length) {
    await switchConversation(state.conversations[0].id);
    return;
  }
  if (state.currentConversationId && !state.conversations.some((item) => item.id === state.currentConversationId)) {
    state.currentConversationId = null;
  }
  renderConversations();
  if (!state.currentConversationId) emptyConversationView();
}

/** 删除话题（含其中所有对话）。确认框在前，接口在后。 */
export async function removeConversation(id: number, trigger?: HTMLButtonElement): Promise<void> {
  const topic = state.conversations.find((item) => item.id === id);
  if (!topic) return;
  if (!confirm(`确定删除话题“${topic.title}”吗？其中的所有对话也会永久删除，无法恢复。`)) return;

  if (trigger) trigger.disabled = true;
  try {
    await api(`/api/conversations/${id}`, { method: "DELETE" });
  } catch (error) {
    if (trigger) trigger.disabled = false;
    throw error;
  }

  const wasCurrent = id === state.currentConversationId;
  if (wasCurrent) state.currentConversationId = null;
  await loadConversations(wasCurrent);
  await refresh(wasCurrent);
  if (wasCurrent) closeTopics();
}

/** 新建空话题并聚焦输入框。 */
export async function createConversation(): Promise<void> {
  const data = await api<{ conversation: Conversation }>("/api/conversations", { method: "POST" });
  state.currentConversationId = data.conversation.id;
  await loadConversations(false);
  closeTopics();
  emptyConversationView();
  $<HTMLTextAreaElement>("input").focus();
}
