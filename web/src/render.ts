import { DEFAULT_SUMMARY, EMOTION_COLORS, FALLBACK_COLORS, IDLE_GLOW, IDLE_MOOD_META, IDLE_MOOD_NAME, IDLE_PLANET } from "./constants.js";
import { $, esc } from "./dom.js";
import { state } from "./state.js";
import type { Checkin, Stats, TraceStep } from "./types.js";

/** 把一条分析结果画到左侧星球卡上。 */
export function setMood(record: Checkin): void {
  const palette = EMOTION_COLORS[record.emotionCode] || FALLBACK_COLORS;
  const root = document.documentElement.style;
  root.setProperty("--planet", palette[0]);
  root.setProperty("--glow", palette[1]);

  const fromModel = record.source === "llm" || record.source === "agent";
  $("moodName").textContent = `${record.emotion} · 强度 ${record.intensity}/5`;
  $("moodMeta").textContent = `${fromModel ? "大模型主判定" : "本地演示 · 非 AI"} · 置信度 ${Math.round(record.confidence * 100)}%`;
  $("latestSummary").textContent = record.summary;

  const risk = $("risk");
  const high = record.riskLevel === "high";
  risk.style.display = high ? "block" : "none";
  risk.textContent = high ? record.suggestion : "";
}

/** 没有任何记录时，把星球卡恢复成初始状态。 */
export function resetMood(): void {
  const root = document.documentElement.style;
  root.setProperty("--planet", IDLE_PLANET);
  root.setProperty("--glow", IDLE_GLOW);
  $("moodName").textContent = IDLE_MOOD_NAME;
  $("moodMeta").textContent = IDLE_MOOD_META;
  $("latestSummary").textContent = DEFAULT_SUMMARY;
  $("risk").style.display = "none";
}

/** Agent 轨迹折叠面板。没有轨迹时返回空串，调用方直接拼接即可。 */
export function traceHtml(record: Checkin): string {
  const trace: TraceStep[] = Array.isArray(record.trace) ? record.trace : [];
  if (!trace.length) return "";

  const items = trace
    .map((step) => {
      const index = step.step ?? 0;
      if (step.action === "调用工具") {
        const names = (step.tools && step.tools.length ? step.tools : step.tool ? [step.tool] : [])
          .map((name) => `<code>${esc(name)}</code>`)
          .join(" ");
        const args =
          step.args && Object.keys(step.args).length
            ? ` <span class="args">${esc(JSON.stringify(step.args))}</span>`
            : "";
        return `<li>第 ${index} 步 · 调用工具 ${names || "未知"}${args}</li>`;
      }
      if (step.action === "无需更多信息" || step.action === "信息已足够") {
        return `<li>第 ${index} 步 · 判断当前信息已足够，直接回应</li>`;
      }
      return `<li>第 ${index} 步 · ${esc(step.action || "")}</li>`;
    })
    .join("");

  return `<details class="agent-trace"><summary>Agent 执行轨迹 · ${trace.length} 步</summary><ol>${items}</ol></details>`;
}

/** 渲染一条 AI 回应（含建议、追问、轨迹、个性化说明与反馈按钮）。 */
export function addMessage(record: Checkin): void {
  const message = document.createElement("div");
  message.className = "message ai";

  const support = record.suggestion
    ? `<div class="support"><b>可以试试：</b>${esc(record.suggestion)}</div>`
    : "";
  const followUp = record.followUpQuestion
    ? `<div class="followup">${esc(record.followUpQuestion)}</div>`
    : "";
  const personalization = record.personalization?.hints?.length
    ? `<div class="personalization">已根据 ${
        record.personalization.feedbackCount
          ? `${record.personalization.feedbackCount} 次反馈`
          : "近期对话变化"
      } 调整：${esc(record.personalization.summary)}</div>`
    : "";
  const feedback = `<div class="feedback"><button data-feedback="helpful" data-id="${record.id}">这条回应有帮助</button><button data-feedback="not_helpful" data-id="${record.id}">不太适合我</button></div>`;

  message.innerHTML =
    esc(record.response) + support + followUp + traceHtml(record) + personalization + feedback;

  const messages = $("messages");
  messages.appendChild(message);
  messages.scrollTop = messages.scrollHeight;
}

/** 新话题的空对话提示。 */
export function emptyConversationView(): void {
  $("messages").innerHTML =
    '<div class="message ai">新话题开始了。想说什么都行，我会只延续这个话题里的内容。<small>独立话题 · 随时切换 · 自动保存</small></div>';
}

/** 话题抽屉里的列表。 */
export function renderConversations(): void {
  const list = $("conversationList");
  if (!state.conversations.length) {
    list.innerHTML = '<div class="empty">还没有话题，发一条消息就会创建。</div>';
  } else {
    list.innerHTML = state.conversations
      .map(
        (conversation) =>
          `<div class="conversation-item ${conversation.id === state.currentConversationId ? "active" : ""}">` +
          `<button class="conversation-open" data-conversation="${conversation.id}">` +
          `<span class="title">${esc(conversation.title)}</span>` +
          `<span class="preview">${esc(conversation.preview)}</span></button>` +
          `<button class="delete-topic" data-delete-conversation="${conversation.id}" title="删除话题" aria-label="删除话题 ${esc(conversation.title)}">×</button>` +
          `</div>`,
      )
      .join("");
  }

  const current = state.conversations.find((item) => item.id === state.currentConversationId);
  $("currentTopic").textContent = current ? current.title : "AI 情绪支持 Agent";
}

/** 情绪轨迹页的"最近片段"列表。 */
export function renderHistory(): void {
  const history = $("history");
  if (!state.records.length) {
    history.innerHTML = '<div class="empty">还没有记录。<br>从上方第一段分享开始。</div>';
    return;
  }
  history.innerHTML = state.records
    .slice(0, 8)
    .map(
      (record) =>
        `<button class="history-item" data-history="${record.id}">` +
        `<div class="top"><b>${esc(record.emotion)} · ${record.intensity}/5</b>` +
        `<span>${esc((record.createdAt || "").slice(5, 16))}</span></div>` +
        `<p>${esc(record.text)}</p></button>`,
    )
    .join("");
}

/** 顶部三个统计卡 + 7 天柱状图 + 情绪分布。 */
export function renderStats(stats: Stats): void {
  $("total").textContent = String(stats.total);

  const entries = Object.entries(stats.distribution || {}).sort((a, b) => b[1] - a[1]);
  $("commonMood").textContent = entries[0]?.[0] || "—";
  $("helpfulRate").textContent = stats.helpfulRate == null ? "—" : `${Math.round(stats.helpfulRate * 100)}%`;
  $("feedbackCount").textContent = stats.feedbackTotal
    ? `基于 ${stats.feedbackTotal} 次主动反馈`
    : "反馈会用于调整后续对话";

  const counts: Record<string, number> = {};
  (stats.daily || []).forEach((row) => {
    counts[row.day] = (counts[row.day] || 0) + row.count;
  });

  // 柱高用相对比例（--h:0~1），由 CSS 按图表实际高度换算，卡片被拉高时柱子跟着长
  const maxCount = Math.max(1, ...Object.values(counts));
  $("bars").innerHTML = Array.from({ length: 7 }, (_, offset) => {
    const date = new Date();
    date.setDate(date.getDate() - 6 + offset);
    const key = `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
    const count = counts[key] || 0;
    const height = count ? Math.max(0.06, count / maxCount) : 0;
    return (
      `<div class="bar-col"><div style="font-size:10px;color:#999">${count}</div>` +
      `<div class="bar" style="--h:${height.toFixed(3)}"></div>` +
      `<div class="bar-label">${date.getMonth() + 1}/${date.getDate()}</div></div>`
    );
  }).join("");

  $("distribution").innerHTML = entries
    .map(([name, count]) => `<span class="dist">${esc(name)} ${count}</span>`)
    .join("");
}
