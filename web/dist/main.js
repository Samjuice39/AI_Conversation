"use strict";
(() => {
  // src/constants.ts
  var EMOTION_COLORS = {
    0: ["#4A7BA7", "#A9C9DE"],
    1: ["#E05A47", "#F0A899"],
    2: ["#F2C744", "#F8DF95"],
    3: ["#8C8577", "#CBC4B4"],
    4: ["#8A6E9E", "#C9BBD6"],
    5: ["#E2683C", "#F2AE8C"]
  };
  var FALLBACK_COLORS = EMOTION_COLORS[0];
  var IDLE_PLANET = "#EE9B3A";
  var IDLE_GLOW = "#F2C744";
  var IDLE_MOOD_NAME = "\u7B49\u5F85\u4F60\u7684\u5206\u4EAB";
  var IDLE_MOOD_META = "AI \u5C06\u5728\u8FD9\u91CC\u7ED8\u5236\u4F60\u7684\u5F53\u4E0B\u72B6\u6001";
  var DEFAULT_SUMMARY = "\u8FD9\u4E0D\u662F\u5FC3\u7406\u8BCA\u65AD\uFF0C\u800C\u662F\u4E00\u79CD\u57FA\u4E8E\u4F60\u4E3B\u52A8\u63CF\u8FF0\u7684\u5BF9\u8BDD\u5F0F\u7406\u89E3\u3002\u4F60\u53EF\u4EE5\u968F\u65F6\u7EA0\u6B63 AI \u7684\u5224\u65AD\u3002";
  var MAX_RECORD_MS = 6e4;
  var STREAM_IDLE_TIMEOUT_MS = 9e4;

  // src/api.ts
  async function api(path, options = {}) {
    const response = await fetch(path, options);
    const data = await response.json().catch(() => ({}));
    if (response.status === 401) {
      location.href = "/";
      throw new Error("\u8BF7\u5148\u767B\u5F55");
    }
    if (!response.ok || !data.ok) {
      throw new Error(data.error || `HTTP ${response.status}`);
    }
    return data;
  }
  function postJson(path, body, method = "POST") {
    return api(path, {
      method,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    });
  }
  async function streamChat(body, onEvent) {
    const controller = new AbortController();
    const progress = { finished: false, result: null, failure: "" };
    let timer = null;
    const stopTimer = () => {
      if (timer !== null) window.clearTimeout(timer);
      timer = null;
    };
    const arm = () => {
      stopTimer();
      timer = window.setTimeout(() => controller.abort(), STREAM_IDLE_TIMEOUT_MS);
    };
    arm();
    let response;
    try {
      response = await fetch("/api/chat/stream", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
        signal: controller.signal
      });
    } catch {
      stopTimer();
      throw new Error(controller.signal.aborted ? "\u7B49\u5F85 AI \u8D85\u65F6\uFF0C\u8BF7\u91CD\u8BD5" : "\u7F51\u7EDC\u8FDE\u63A5\u5931\u8D25\uFF0C\u8BF7\u91CD\u8BD5");
    }
    if (response.status === 401) {
      stopTimer();
      location.href = "/";
      throw new Error("\u8BF7\u5148\u767B\u5F55");
    }
    if (!response.ok || !response.body) {
      stopTimer();
      let message = `HTTP ${response.status}`;
      try {
        const detail = await response.json();
        if (detail && detail.error) message = detail.error;
      } catch {
      }
      throw new Error(message);
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    const handleLine = (line) => {
      if (!line.startsWith("data:")) return;
      let event;
      try {
        event = JSON.parse(line.slice(5).trim());
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
      for (; ; ) {
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
      throw new Error(controller.signal.aborted ? "AI \u54CD\u5E94\u8D85\u65F6\uFF0C\u8BF7\u91CD\u8BD5" : "\u8FDE\u63A5\u4E2D\u65AD\uFF0C\u8BF7\u91CD\u8BD5");
    } finally {
      stopTimer();
    }
    if (progress.failure) throw new Error(progress.failure);
    if (!progress.result) throw new Error("\u6CA1\u6709\u6536\u5230\u5B8C\u6574\u56DE\u5E94\uFF0C\u8BF7\u91CD\u8BD5");
    return progress.result;
  }
  async function transcribe(blob) {
    const form = new FormData();
    form.append("audio", blob, "voice.wav");
    form.append("language", "auto");
    const response = await fetch("/api/asr", { method: "POST", body: form });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || !data.ok) throw new Error(data.error || `HTTP ${response.status}`);
    return {
      text: (data.text || "").trim(),
      languageText: data.languageText,
      emotionText: data.emotionText
    };
  }
  async function fetchAsrStatus() {
    try {
      const data = await api("/api/asr/status");
      return data.asr ?? null;
    } catch {
      return null;
    }
  }

  // src/dom.ts
  var ESCAPE_MAP = {
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    "'": "&#39;",
    '"': "&quot;"
  };
  function $(id) {
    const element = document.getElementById(id);
    if (!element) throw new Error(`\u9875\u9762\u7F3A\u5C11\u5143\u7D20 #${id}`);
    return element;
  }
  function qsa(selector, root = document) {
    return Array.from(root.querySelectorAll(selector));
  }
  function esc(value) {
    return String(value ?? "").replace(/[&<>'"]/g, (char) => ESCAPE_MAP[char]);
  }
  function closestWith(target, selector) {
    if (!(target instanceof Element)) return null;
    return target.closest(selector);
  }

  // src/state.ts
  var state = {
    records: [],
    conversations: [],
    currentConversationId: null
  };

  // src/render.ts
  function setMood(record) {
    const palette = EMOTION_COLORS[record.emotionCode] || FALLBACK_COLORS;
    const root = document.documentElement.style;
    root.setProperty("--planet", palette[0]);
    root.setProperty("--glow", palette[1]);
    const fromModel = record.source === "llm" || record.source === "agent";
    $("moodName").textContent = `${record.emotion} \xB7 \u5F3A\u5EA6 ${record.intensity}/5`;
    $("moodMeta").textContent = `${fromModel ? "\u5927\u6A21\u578B\u4E3B\u5224\u5B9A" : "\u672C\u5730\u6F14\u793A \xB7 \u975E AI"} \xB7 \u7F6E\u4FE1\u5EA6 ${Math.round(record.confidence * 100)}%`;
    $("latestSummary").textContent = record.summary;
    const risk = $("risk");
    const high = record.riskLevel === "high";
    risk.style.display = high ? "block" : "none";
    risk.textContent = high ? record.suggestion : "";
  }
  function resetMood() {
    const root = document.documentElement.style;
    root.setProperty("--planet", IDLE_PLANET);
    root.setProperty("--glow", IDLE_GLOW);
    $("moodName").textContent = IDLE_MOOD_NAME;
    $("moodMeta").textContent = IDLE_MOOD_META;
    $("latestSummary").textContent = DEFAULT_SUMMARY;
    $("risk").style.display = "none";
  }
  function traceHtml(record) {
    const trace = Array.isArray(record.trace) ? record.trace : [];
    if (!trace.length) return "";
    const items = trace.map((step) => {
      const index = step.step ?? 0;
      if (step.action === "\u8C03\u7528\u5DE5\u5177") {
        const names = (step.tools && step.tools.length ? step.tools : step.tool ? [step.tool] : []).map((name) => `<code>${esc(name)}</code>`).join(" ");
        const args = step.args && Object.keys(step.args).length ? ` <span class="args">${esc(JSON.stringify(step.args))}</span>` : "";
        return `<li>\u7B2C ${index} \u6B65 \xB7 \u8C03\u7528\u5DE5\u5177 ${names || "\u672A\u77E5"}${args}</li>`;
      }
      if (step.action === "\u65E0\u9700\u66F4\u591A\u4FE1\u606F" || step.action === "\u4FE1\u606F\u5DF2\u8DB3\u591F") {
        return `<li>\u7B2C ${index} \u6B65 \xB7 \u5224\u65AD\u5F53\u524D\u4FE1\u606F\u5DF2\u8DB3\u591F\uFF0C\u76F4\u63A5\u56DE\u5E94</li>`;
      }
      return `<li>\u7B2C ${index} \u6B65 \xB7 ${esc(step.action || "")}</li>`;
    }).join("");
    return `<details class="agent-trace"><summary>Agent \u6267\u884C\u8F68\u8FF9 \xB7 ${trace.length} \u6B65</summary><ol>${items}</ol></details>`;
  }
  function addMessage(record) {
    const message = document.createElement("div");
    message.className = "message ai";
    const support = record.suggestion ? `<div class="support"><b>\u53EF\u4EE5\u8BD5\u8BD5\uFF1A</b>${esc(record.suggestion)}</div>` : "";
    const followUp = record.followUpQuestion ? `<div class="followup">${esc(record.followUpQuestion)}</div>` : "";
    const truncated = record.streamTruncated ? '<div class="truncated">\u8FD9\u6BB5\u56DE\u5E94\u6CA1\u6709\u5B8C\u6574\u751F\u6210\uFF0C\u5EFA\u8BAE\u91CD\u8BD5\u4E00\u6B21\u3002</div>' : "";
    const personalization = record.personalization?.hints?.length ? `<div class="personalization">\u5DF2\u6839\u636E ${record.personalization.feedbackCount ? `${record.personalization.feedbackCount} \u6B21\u53CD\u9988` : "\u8FD1\u671F\u5BF9\u8BDD\u53D8\u5316"} \u8C03\u6574\uFF1A${esc(record.personalization.summary)}</div>` : "";
    const feedback = `<div class="feedback"><button data-feedback="helpful" data-id="${record.id}">\u8FD9\u6761\u56DE\u5E94\u6709\u5E2E\u52A9</button><button data-feedback="not_helpful" data-id="${record.id}">\u4E0D\u592A\u9002\u5408\u6211</button></div>`;
    message.innerHTML = esc(record.response) + truncated + support + followUp + traceHtml(record) + personalization + feedback;
    const messages = $("messages");
    messages.appendChild(message);
    messages.scrollTop = messages.scrollHeight;
  }
  function emptyConversationView() {
    $("messages").innerHTML = '<div class="message ai">\u65B0\u8BDD\u9898\u5F00\u59CB\u4E86\u3002\u60F3\u8BF4\u4EC0\u4E48\u90FD\u884C\uFF0C\u6211\u4F1A\u53EA\u5EF6\u7EED\u8FD9\u4E2A\u8BDD\u9898\u91CC\u7684\u5185\u5BB9\u3002<small>\u72EC\u7ACB\u8BDD\u9898 \xB7 \u968F\u65F6\u5207\u6362 \xB7 \u81EA\u52A8\u4FDD\u5B58</small></div>';
  }
  function renderConversations() {
    const list = $("conversationList");
    if (!state.conversations.length) {
      list.innerHTML = '<div class="empty">\u8FD8\u6CA1\u6709\u8BDD\u9898\uFF0C\u53D1\u4E00\u6761\u6D88\u606F\u5C31\u4F1A\u521B\u5EFA\u3002</div>';
    } else {
      list.innerHTML = state.conversations.map(
        (conversation) => `<div class="conversation-item ${conversation.id === state.currentConversationId ? "active" : ""}"><button class="conversation-open" data-conversation="${conversation.id}"><span class="title">${esc(conversation.title)}</span><span class="preview">${esc(conversation.preview)}</span></button><button class="delete-topic" data-delete-conversation="${conversation.id}" title="\u5220\u9664\u8BDD\u9898" aria-label="\u5220\u9664\u8BDD\u9898 ${esc(conversation.title)}">\xD7</button></div>`
      ).join("");
    }
    const current = state.conversations.find((item) => item.id === state.currentConversationId);
    $("currentTopic").textContent = current ? current.title : "AI \u60C5\u7EEA\u652F\u6301 Agent";
  }
  function renderHistory() {
    const history = $("history");
    if (!state.records.length) {
      history.innerHTML = '<div class="empty">\u8FD8\u6CA1\u6709\u8BB0\u5F55\u3002<br>\u4ECE\u4E0A\u65B9\u7B2C\u4E00\u6BB5\u5206\u4EAB\u5F00\u59CB\u3002</div>';
      return;
    }
    history.innerHTML = state.records.slice(0, 8).map(
      (record) => `<button class="history-item" data-history="${record.id}"><div class="top"><b>${esc(record.emotion)} \xB7 ${record.intensity}/5</b><span>${esc((record.createdAt || "").slice(5, 16))}</span></div><p>${esc(record.text)}</p></button>`
    ).join("");
  }
  function renderStats(stats) {
    $("total").textContent = String(stats.total);
    const entries = Object.entries(stats.distribution || {}).sort((a, b) => b[1] - a[1]);
    $("commonMood").textContent = entries[0]?.[0] || "\u2014";
    $("helpfulRate").textContent = stats.helpfulRate == null ? "\u2014" : `${Math.round(stats.helpfulRate * 100)}%`;
    $("feedbackCount").textContent = stats.feedbackTotal ? `\u57FA\u4E8E ${stats.feedbackTotal} \u6B21\u4E3B\u52A8\u53CD\u9988` : "\u53CD\u9988\u4F1A\u7528\u4E8E\u8C03\u6574\u540E\u7EED\u5BF9\u8BDD";
    const counts = {};
    (stats.daily || []).forEach((row) => {
      counts[row.day] = (counts[row.day] || 0) + row.count;
    });
    const maxCount = Math.max(1, ...Object.values(counts));
    $("bars").innerHTML = Array.from({ length: 7 }, (_, offset) => {
      const date = /* @__PURE__ */ new Date();
      date.setDate(date.getDate() - 6 + offset);
      const key = `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
      const count = counts[key] || 0;
      const height = count ? Math.max(0.06, count / maxCount) : 0;
      return `<div class="bar-col"><div style="font-size:10px;color:#999">${count}</div><div class="bar" style="--h:${height.toFixed(3)}"></div><div class="bar-label">${date.getMonth() + 1}/${date.getDate()}</div></div>`;
    }).join("");
    $("distribution").innerHTML = entries.map(([name, count]) => `<span class="dist">${esc(name)} ${count}</span>`).join("");
  }

  // src/insights.ts
  async function refresh(updateMood = true) {
    const [history, insights] = await Promise.all([
      api("/api/history"),
      api("/api/insights")
    ]);
    state.records = history.records;
    renderHistory();
    renderStats(insights.stats);
    if (!updateMood) return;
    const latest = state.records[0];
    if (latest) setMood(latest);
    else resetMood();
  }

  // src/panes.ts
  function showPane(name) {
    const journey = name === "journey";
    const track = document.getElementById("track");
    if (track) track.classList.toggle("to-journey", journey);
    qsa(".nav-tab").forEach((button) => {
      button.classList.toggle("active", button.dataset.pane === name);
    });
  }
  function bindPaneNav() {
    qsa(".nav-tab").forEach((button) => {
      button.addEventListener("click", () => {
        const pane = button.dataset.pane;
        if (pane === "chat" || pane === "journey") showPane(pane);
      });
    });
  }

  // src/conversations.ts
  function closeTopics() {
    $("conversationDrawer").classList.remove("open");
    $("drawerScrim").classList.remove("open");
  }
  function openTopics() {
    $("conversationDrawer").classList.add("open");
    $("drawerScrim").classList.add("open");
  }
  async function switchConversation(id, focus = false) {
    const data = await api(`/api/conversations/${id}/messages`);
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
  async function loadConversations(selectLatest = false) {
    const data = await api("/api/conversations");
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
  async function removeConversation(id, trigger) {
    const topic = state.conversations.find((item) => item.id === id);
    if (!topic) return;
    if (!confirm(`\u786E\u5B9A\u5220\u9664\u8BDD\u9898\u201C${topic.title}\u201D\u5417\uFF1F\u5176\u4E2D\u7684\u6240\u6709\u5BF9\u8BDD\u4E5F\u4F1A\u6C38\u4E45\u5220\u9664\uFF0C\u65E0\u6CD5\u6062\u590D\u3002`)) return;
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
  async function createConversation() {
    const data = await api("/api/conversations", { method: "POST" });
    state.currentConversationId = data.conversation.id;
    await loadConversations(false);
    closeTopics();
    emptyConversationView();
    $("input").focus();
  }

  // src/dialogs.ts
  async function refreshAIStatus() {
    const data = await api("/api/ai/status");
    const badge = $("modelBadge");
    badge.title = "";
    if (!data.llm.enabled) {
      badge.textContent = "\u672C\u5730\u89C4\u5219\u6A21\u578B";
    } else if (data.llm.source === "own") {
      badge.textContent = `\u6211\u7684 API \xB7 ${data.llm.model}`;
    } else {
      const quota = data.llm.quota;
      badge.textContent = quota ? `\u9ED8\u8BA4\u6A21\u578B \xB7 \u4ECA\u65E5\u5269 ${quota.remaining}/${quota.limit}` : `\u9ED8\u8BA4\u6A21\u578B \xB7 ${data.llm.model}`;
      badge.title = `${data.llm.model} \xB7 \u6BCF\u65E5 0 \u70B9\u6062\u590D`;
    }
  }
  function bindReportDialog() {
    const button = $("generateReport");
    const modal = $("reportModal");
    const close = () => modal.classList.remove("open");
    button.addEventListener("click", async () => {
      button.disabled = true;
      button.textContent = "AI \u6B63\u5728\u56DE\u987E\u2026";
      try {
        const data = await api("/api/insights/ai", { method: "POST" });
        $("reportTitle").textContent = data.report.title;
        $("reportOverview").textContent = data.report.overview;
        $("reportPatterns").innerHTML = data.report.patterns.map((item) => `<li>${esc(item)}</li>`).join("");
        $("reportNext").textContent = `\u4E0B\u4E00\u6B65\uFF1A${data.report.nextStep}`;
        modal.classList.add("open");
      } catch (error) {
        alert(error instanceof Error ? error.message : String(error));
      } finally {
        button.disabled = false;
        button.textContent = "\u751F\u6210 AI \u9636\u6BB5\u56DE\u987E";
        refreshAIStatus().catch(() => {
        });
      }
    });
    $("closeReport").addEventListener("click", close);
    modal.addEventListener("click", (event) => {
      if (event.target === modal) close();
    });
  }
  function bindAccountDialog() {
    const modal = $("accountModal");
    const form = $("accountForm");
    const password = $("accountPassword");
    const confirmButton = $("confirmAccount");
    const close = () => {
      modal.classList.remove("open");
      form.reset();
    };
    $("deleteAccount").addEventListener("click", () => {
      modal.classList.add("open");
      password.focus();
    });
    $("closeAccount").addEventListener("click", close);
    $("cancelAccount").addEventListener("click", close);
    modal.addEventListener("click", (event) => {
      if (event.target === modal) close();
    });
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      confirmButton.disabled = true;
      confirmButton.textContent = "\u6B63\u5728\u6CE8\u9500\u2026";
      try {
        await api("/api/auth/account", {
          method: "DELETE",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ password: password.value })
        });
        location.href = "/";
      } catch (error) {
        alert(error instanceof Error ? error.message : String(error));
        confirmButton.disabled = false;
        confirmButton.textContent = "\u6C38\u4E45\u6CE8\u9500\u8D26\u53F7";
        password.select();
      }
    });
  }
  function bindAiSettingsDialog() {
    const modal = $("aiSettingsModal");
    const form = $("aiSettingsForm");
    const status = $("aiSettingsStatus");
    const baseUrl = $("aiBaseUrl");
    const model = $("aiModel");
    const apiKey = $("aiApiKey");
    const saveButton = $("saveAiSettings");
    let hasServerDefault = false;
    const close = () => {
      modal.classList.remove("open");
      status.textContent = "";
      apiKey.value = "";
    };
    $("openAiSettings").addEventListener("click", async () => {
      try {
        const data = await api("/api/ai/settings");
        hasServerDefault = data.settings.serverDefault;
        baseUrl.value = data.settings.baseUrl;
        model.value = data.settings.model;
        apiKey.value = "";
        apiKey.placeholder = data.settings.hasApiKey ? "\u5DF2\u4FDD\u5B58\uFF0C\u7559\u7A7A\u8868\u793A\u4E0D\u4FEE\u6539" : "sk-...";
        status.textContent = data.settings.enabled ? "\u5F53\u524D\u4F7F\u7528\u4F60\u81EA\u5DF1\u7684 API\uFF0C\u5BF9\u8BDD\u8D70\u4F60\u7684\u8D26\u53F7\u4E0E\u989D\u5EA6" : data.settings.serverDefault ? `\u5F53\u524D\u4F7F\u7528\u672C\u7AD9\u9ED8\u8BA4\u6A21\u578B\uFF08${data.settings.serverModel}\uFF09\uFF0C\u4ECA\u65E5\u5269\u4F59 ${data.settings.quota?.remaining ?? 0}/${data.settings.quota?.limit ?? 0} \u6B21\uFF0C\u6BCF\u65E5 0 \u70B9\u6062\u590D` : "\u5F53\u524D\u672A\u914D\u7F6E\u6A21\u578B\uFF0C\u5C06\u4F7F\u7528\u672C\u5730\u89C4\u5219\u6A21\u578B";
        modal.classList.add("open");
      } catch (error) {
        alert(error instanceof Error ? error.message : String(error));
      }
    });
    $("closeAiSettings").addEventListener("click", close);
    modal.addEventListener("click", (event) => {
      if (event.target === modal) close();
    });
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      saveButton.disabled = true;
      status.textContent = "\u6B63\u5728\u4FDD\u5B58\u2026";
      try {
        await postJson(
          "/api/ai/settings",
          {
            baseUrl: baseUrl.value.trim(),
            model: model.value.trim(),
            apiKey: apiKey.value.trim()
          },
          "PUT"
        );
        await refreshAIStatus();
        close();
      } catch (error) {
        status.textContent = error instanceof Error ? error.message : String(error);
      } finally {
        saveButton.disabled = false;
      }
    });
    $("clearAiSettings").addEventListener("click", async () => {
      const fallback = hasServerDefault ? "\u6062\u590D\u4F7F\u7528\u672C\u7AD9\u9ED8\u8BA4\u6A21\u578B" : "\u6539\u7528\u672C\u5730\u89C4\u5219\u6A21\u578B";
      if (!confirm(`\u6E05\u9664\u5F53\u524D\u8D26\u53F7\u4FDD\u5B58\u7684 API \u914D\u7F6E\uFF0C${fallback}\u5417\uFF1F`)) return;
      try {
        await api("/api/ai/settings", { method: "DELETE" });
        await refreshAIStatus();
        close();
      } catch (error) {
        status.textContent = error instanceof Error ? error.message : String(error);
      }
    });
  }
  function bindPersonaFields() {
    const persona = $("persona");
    const custom = $("customPersona");
    const preferredName = $("preferredName");
    const syncCustomVisibility = () => {
      custom.style.display = persona.value === "custom" ? "block" : "none";
    };
    persona.value = localStorage.getItem("emotionPersona") || "gentle";
    custom.value = localStorage.getItem("emotionCustomPersona") || "";
    preferredName.value = localStorage.getItem("emotionPreferredName") || "";
    syncCustomVisibility();
    persona.addEventListener("change", () => {
      localStorage.setItem("emotionPersona", persona.value);
      syncCustomVisibility();
    });
    custom.addEventListener("input", () => localStorage.setItem("emotionCustomPersona", custom.value));
    preferredName.addEventListener(
      "input",
      () => localStorage.setItem("emotionPreferredName", preferredName.value)
    );
  }
  function bindDialogs() {
    bindReportDialog();
    bindAccountDialog();
    bindAiSettingsDialog();
    bindPersonaFields();
  }

  // src/voice.ts
  var onlineRecognition = null;
  var recorder = null;
  var mediaStream = null;
  var chunks = [];
  var recordTimer = null;
  var pressing = false;
  var starting = false;
  var audioContextCtor = null;
  var READY_HINT = "\u672C\u5730\u8BC6\u522B\u5DF2\u5C31\u7EEA \xB7 \u6309\u4F4F\u8BF4\u8BDD\uFF0C\u97F3\u9891\u4E0D\u51FA\u672C\u673A";
  var RECOGNITION_ERRORS = {
    "not-allowed": "\u9EA6\u514B\u98CE\u6216\u8BED\u97F3\u670D\u52A1\u6743\u9650\u88AB\u62D2\u7EDD\uFF0C\u8BF7\u5728\u6D4F\u89C8\u5668\u5730\u5740\u680F\u4E2D\u5141\u8BB8\u9EA6\u514B\u98CE",
    "service-not-allowed": "\u6D4F\u89C8\u5668\u7981\u7528\u4E86\u5728\u7EBF\u8BED\u97F3\u8BC6\u522B\uFF0C\u8BF7\u6362\u7528\u6700\u65B0\u7248 Chrome \u6216 Edge",
    "no-speech": "\u6CA1\u6709\u542C\u5230\u58F0\u97F3\uFF0C\u8BF7\u9760\u8FD1\u9EA6\u514B\u98CE\u540E\u91CD\u8BD5",
    network: "\u6D4F\u89C8\u5668\u5728\u7EBF\u8BED\u97F3\u670D\u52A1\u8FDE\u63A5\u5931\u8D25\uFF0C\u8BF7\u66F4\u6362\u7F51\u7EDC\u6216\u6539\u7528 Edge\uFF1B\u4E5F\u53EF\u4EE5\u76F4\u63A5\u6587\u5B57\u8F93\u5165",
    "audio-capture": "\u6CA1\u6709\u627E\u5230\u53EF\u7528\u9EA6\u514B\u98CE\uFF0C\u8BF7\u68C0\u67E5\u8BBE\u5907\u8FDE\u63A5\u548C\u7CFB\u7EDF\u6743\u9650",
    "language-not-supported": "\u5F53\u524D\u6D4F\u89C8\u5668\u4E0D\u652F\u6301\u4E2D\u6587\u8BED\u97F3\u8BC6\u522B\uFF0C\u8BF7\u6362\u7528\u6700\u65B0\u7248 Chrome \u6216 Edge",
    aborted: "\u8BED\u97F3\u8BC6\u522B\u5DF2\u505C\u6B62"
  };
  function stopOnlineRecognition() {
    if (!onlineRecognition) return;
    try {
      onlineRecognition.stop();
    } catch {
    }
  }
  function stopRecording() {
    pressing = false;
    if (recorder && recorder.state !== "inactive") {
      try {
        recorder.stop();
      } catch {
      }
    }
  }
  function bindHoldToRecord(button, start, stop) {
    let pointerId = null;
    const release = (event) => {
      if (pointerId === null || event.pointerId !== pointerId) return;
      pointerId = null;
      stop();
    };
    button.onclick = (event) => event.preventDefault();
    button.onpointerdown = (event) => {
      if (button.disabled || pointerId !== null || event.pointerType === "mouse" && event.button !== 0) return;
      event.preventDefault();
      pointerId = event.pointerId;
      try {
        button.setPointerCapture(pointerId);
      } catch {
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
  function setupVoiceInput() {
    const button = $("voice");
    const status = $("voiceStatus");
    const RecognitionCtor = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!RecognitionCtor) {
      button.disabled = true;
      button.textContent = "\u8BED\u97F3\u4E0D\u53EF\u7528";
      status.textContent = "\u5F53\u524D\u6D4F\u89C8\u5668\u4E0D\u652F\u6301\u8BED\u97F3\u8F93\u5165\uFF0C\u5EFA\u8BAE\u4F7F\u7528 Chrome \u6216 Edge";
      return;
    }
    if (!window.isSecureContext) {
      button.disabled = true;
      button.textContent = "\u8BED\u97F3\u4E0D\u53EF\u7528";
      status.textContent = "\u8BED\u97F3\u8F93\u5165\u9700\u8981 HTTPS \u6216 localhost";
      return;
    }
    const start = () => {
      if (onlineRecognition) return;
      const recognition = new RecognitionCtor();
      onlineRecognition = recognition;
      recognition.lang = "zh-CN";
      recognition.continuous = true;
      recognition.interimResults = true;
      const input = $("input");
      const base = input.value.trim();
      let finalText = "";
      let errorText = "";
      recognition.onstart = () => {
        button.textContent = "\u677E\u5F00\u7ED3\u675F";
        button.classList.add("recording");
        status.textContent = "\u6B63\u5728\u5F55\u97F3\uFF0C\u677E\u5F00\u6216\u79FB\u51FA\u6309\u94AE\u7ED3\u675F";
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
        errorText = RECOGNITION_ERRORS[event.error] || `\u8BED\u97F3\u8BC6\u522B\u5931\u8D25\uFF08${event.error}\uFF09\uFF0C\u8BF7\u91CD\u8BD5\u6216\u76F4\u63A5\u6587\u5B57\u8F93\u5165`;
        status.textContent = errorText;
      };
      recognition.onnomatch = () => {
        status.textContent = "\u6CA1\u6709\u542C\u6E05\uFF0C\u8BF7\u518D\u8BF4\u4E00\u6B21\uFF0C\u6216\u76F4\u63A5\u6587\u5B57\u8F93\u5165";
      };
      recognition.onend = () => {
        onlineRecognition = null;
        button.textContent = "\u6309\u4F4F\u8BF4\u8BDD";
        button.classList.remove("recording");
        if (!errorText) status.textContent = finalText ? "\u5DF2\u586B\u5165\uFF0C\u53EF\u4FEE\u6539\u540E\u53D1\u9001" : "\u6CA1\u6709\u8BC6\u522B\u5230\u5185\u5BB9";
      };
      try {
        recognition.start();
      } catch {
        onlineRecognition = null;
        status.textContent = "\u8BED\u97F3\u542F\u52A8\u5931\u8D25\uFF0C\u8BF7\u91CD\u8BD5";
      }
    };
    bindHoldToRecord(button, start, stopOnlineRecognition);
  }
  function encodeWav(samples, sampleRate) {
    const buffer = new ArrayBuffer(44 + samples.length * 2);
    const view = new DataView(buffer);
    const writeText = (offset2, text) => {
      for (let i = 0; i < text.length; i += 1) view.setUint8(offset2 + i, text.charCodeAt(i));
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
      view.setInt16(offset, value < 0 ? value * 32768 : value * 32767, true);
      offset += 2;
    }
    return new Blob([buffer], { type: "audio/wav" });
  }
  async function toWav(blob) {
    const Ctor = audioContextCtor;
    if (!Ctor) throw new Error("\u5F53\u524D\u6D4F\u89C8\u5668\u4E0D\u652F\u6301\u97F3\u9891\u89E3\u7801");
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
      const targetRate = 16e3;
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
      }
    }
  }
  function setupLocalVoice() {
    const button = $("voice");
    const status = $("voiceStatus");
    const Ctor = window.AudioContext || window.webkitAudioContext;
    if (!window.MediaRecorder || !Ctor) return;
    if (!window.isSecureContext) return;
    if (typeof navigator.mediaDevices?.getUserMedia !== "function") return;
    audioContextCtor = Ctor;
    const input = $("input");
    const useOnline = () => {
      setupVoiceInput();
      if (!button.disabled) button.textContent = "\u6309\u4F4F\u8BF4\u8BDD";
    };
    const upload = async (blob) => {
      const base = input.value.trim();
      status.textContent = "\u672C\u5730\u8BC6\u522B\u4E2D\u2026\uFF08\u9996\u6B21\u8981\u52A0\u8F7D\u6A21\u578B\uFF0C\u7A0D\u7B49\u51E0\u79D2\uFF09";
      try {
        const wav = await toWav(blob);
        if (wav.size < 6e3) throw new Error("\u5F55\u97F3\u592A\u77ED");
        const result = await transcribe(wav);
        if (!result.text) {
          status.textContent = "\u6CA1\u6709\u8BC6\u522B\u5230\u5185\u5BB9\uFF0C\u8BF7\u518D\u8BF4\u4E00\u6B21";
          return;
        }
        input.value = [base, result.text].filter(Boolean).join(" ");
        const meta = [result.languageText, result.emotionText].filter(Boolean).join(" \xB7 ");
        status.textContent = `\u672C\u5730\u8BC6\u522B\u5B8C\u6210${meta ? `\uFF08${meta}\uFF09` : ""}\uFF0C\u53EF\u4FEE\u6539\u540E\u53D1\u9001`;
      } catch (error) {
        useOnline();
        const message = error instanceof Error ? error.message : String(error);
        status.textContent = `\u672C\u5730\u8BC6\u522B\u5931\u8D25\uFF1A${message}\uFF0C\u4E0B\u6B21\u6309\u4F4F\u6309\u94AE\u5C06\u4F7F\u7528\u5728\u7EBF\u8BC6\u522B`;
      }
    };
    const startRec = async () => {
      if (recorder || starting) return;
      stopOnlineRecognition();
      pressing = true;
      starting = true;
      status.textContent = "\u6B63\u5728\u8BF7\u6C42\u9EA6\u514B\u98CE\u6743\u9650\u2026";
      try {
        const stream = await navigator.mediaDevices.getUserMedia({
          audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true }
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
          status.textContent = "\u5F53\u524D\u6D4F\u89C8\u5668\u65E0\u6CD5\u672C\u5730\u5F55\u97F3\uFF0C\u5DF2\u6539\u7528\u5728\u7EBF\u8BC6\u522B";
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
          button.textContent = "\u6309\u4F4F\u8BF4\u8BDD";
          button.classList.remove("recording");
          const blob = new Blob(chunks, { type: mime || "audio/webm" });
          chunks = [];
          if (!blob.size) {
            status.textContent = "\u6CA1\u6709\u5F55\u5230\u58F0\u97F3\uFF0C\u8BF7\u91CD\u8BD5";
            return;
          }
          void upload(blob);
        };
        recorder.start();
        button.textContent = "\u677E\u5F00\u7ED3\u675F";
        button.classList.add("recording");
        status.textContent = "\u6B63\u5728\u5F55\u97F3\uFF0C\u677E\u5F00\u6216\u79FB\u51FA\u6309\u94AE\u7ED3\u675F\uFF08\u6700\u957F 60 \u79D2\uFF09";
        recordTimer = window.setTimeout(stopRecording, MAX_RECORD_MS);
      } catch (error) {
        if (pressing) {
          useOnline();
          const name = error instanceof DOMException ? error.name : "\u9519\u8BEF";
          status.textContent = `\u9EA6\u514B\u98CE\u4E0D\u53EF\u7528\uFF08${name}\uFF09\uFF0C\u4E0B\u6B21\u6309\u4F4F\u6309\u94AE\u5C06\u4F7F\u7528\u5728\u7EBF\u8BC6\u522B`;
        }
      } finally {
        starting = false;
      }
    };
    const useLocal = () => {
      button.disabled = false;
      button.textContent = "\u6309\u4F4F\u8BF4\u8BDD";
      bindHoldToRecord(button, () => void startRec(), stopRecording);
    };
    useLocal();
    const pollDeps = () => {
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
            status.textContent = "\u672C\u5730\u8BED\u97F3\u4E0D\u53EF\u7528\uFF0C\u6309\u4F4F\u6309\u94AE\u5C06\u4F7F\u7528\u6D4F\u89C8\u5668\u5728\u7EBF\u8BC6\u522B";
          }
        });
      }, 5e3);
    };
    void fetchAsrStatus().then((asr) => {
      if (asr && asr.available) {
        status.textContent = asr.cached ? READY_HINT : "\u672C\u5730\u8BC6\u522B\u5C31\u7EEA\uFF0C\u4F46\u6743\u91CD\u672A\u968F\u5305\u5206\u53D1\uFF0C\u9996\u6B21\u8BC6\u522B\u4F1A\u8054\u7F51\u4E0B\u8F7D\u7EA6 900MB";
        return;
      }
      if (asr && asr.deps && asr.deps.state === "installing") {
        status.textContent = "\u6B63\u5728\u540E\u53F0\u5B89\u88C5\u672C\u5730\u8BED\u97F3\u4F9D\u8D56\uFF0C\u88C5\u597D\u540E\u6309\u4F4F\u6309\u94AE\u5373\u53EF\u8BF4\u8BDD";
        pollDeps();
        return;
      }
      useOnline();
      if (asr && asr.reason) status.textContent = `\u672C\u5730\u8BC6\u522B\u4E0D\u53EF\u7528\uFF08${asr.reason}\uFF09\uFF0C\u6309\u4F4F\u6309\u94AE\u5C06\u4F7F\u7528\u5728\u7EBF\u8BC6\u522B`;
    });
  }

  // src/chat.ts
  function buildRequest(text) {
    const sleep = $("sleep").value;
    return {
      text,
      conversationId: state.currentConversationId,
      persona: $("persona").value,
      customPersona: $("customPersona").value.trim(),
      preferredName: $("preferredName").value.trim(),
      context: $("context").value,
      sleepQuality: sleep ? Number(sleep) : null
    };
  }
  function syncAfterSend() {
    loadConversations(false).then(() => refresh(false)).catch(() => {
    });
  }
  async function sendMessage() {
    stopRecording();
    stopOnlineRecognition();
    const input = $("input");
    const text = input.value.trim();
    if (text.length < 2) return;
    const requestedConversationId = state.currentConversationId;
    const button = $("send");
    button.disabled = true;
    button.textContent = "AI \u601D\u8003\u4E2D";
    const messages = $("messages");
    const userBubble = document.createElement("div");
    userBubble.className = "message user";
    userBubble.textContent = text;
    messages.appendChild(userBubble);
    const bubble = document.createElement("div");
    bubble.className = "message ai loading";
    bubble.textContent = "\u6B63\u5728\u7ED3\u5408\u4F60\u7684\u63CF\u8FF0\u4E0E\u8FD1\u671F\u8BB0\u5FC6\u2026";
    messages.appendChild(bubble);
    input.value = "";
    messages.scrollTop = messages.scrollHeight;
    let accumulated = "";
    const release = () => {
      button.disabled = false;
      button.textContent = "\u53D1\u9001\u7ED9 AI";
    };
    const paint = (value) => {
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
          if (!accumulated) paint(`${event.message}\u2026`);
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
      bubble.textContent = `${accumulated ? `${accumulated}

` : ""}${message}`;
      input.value = text;
    } finally {
      release();
      refreshAIStatus().catch(() => {
      });
    }
  }

  // src/main.ts
  var reportError = (error) => {
    alert(error instanceof Error ? error.message : String(error));
  };
  function bindComposer() {
    const input = $("input");
    const send = $("send");
    send.addEventListener("click", () => {
      void sendMessage();
    });
    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        if (!send.disabled) void sendMessage();
      }
    });
    qsa(".quick button").forEach((button) => {
      button.addEventListener("click", () => {
        input.value = button.dataset.text || "";
        input.focus();
      });
    });
  }
  function bindTopics() {
    $("topicToggle").addEventListener("click", openTopics);
    $("drawerScrim").addEventListener("click", closeTopics);
    $("newConversation").addEventListener("click", () => {
      void createConversation().catch(reportError);
    });
    $("conversationList").addEventListener("click", (event) => {
      const deleteButton = closestWith(event.target, "[data-delete-conversation]");
      if (deleteButton) {
        void removeConversation(Number(deleteButton.dataset.deleteConversation), deleteButton).catch(reportError);
        return;
      }
      const openButton = closestWith(event.target, "[data-conversation]");
      if (openButton) {
        void switchConversation(Number(openButton.dataset.conversation), true).catch(reportError);
      }
    });
  }
  async function submitFeedback(button) {
    const id = button.dataset.id;
    const feedback = button.dataset.feedback;
    if (!id || !feedback) return;
    try {
      await postJson(`/api/checkins/${id}/feedback`, { feedback });
      const wrapper = button.parentElement;
      if (wrapper) wrapper.innerHTML = "<small>\u53CD\u9988\u5DF2\u8BB0\u4F4F\uFF0C\u4F1A\u7528\u4E8E\u540E\u7EED\u5BF9\u8BDD</small>";
      const record = state.records.find((item) => String(item.id) === id);
      if (record) record.feedback = feedback;
      const data = await api("/api/insights");
      renderStats(data.stats);
    } catch (error) {
      reportError(error);
    }
  }
  function bindMessageActions() {
    document.addEventListener("click", (event) => {
      const feedbackButton = closestWith(event.target, "[data-feedback]");
      if (feedbackButton) {
        void submitFeedback(feedbackButton);
        return;
      }
      const historyButton = closestWith(event.target, "[data-history]");
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
  async function bootstrap() {
    bindPaneNav();
    bindComposer();
    bindTopics();
    bindMessageActions();
    bindDialogs();
    setupVoiceInput();
    setupLocalVoice();
    try {
      const me = await api("/api/auth/me");
      $("username").textContent = me.username;
      const preferredName = $("preferredName");
      if (!preferredName.value && me.preferredName) preferredName.value = me.preferredName;
      await refreshAIStatus();
      await refresh();
      await loadConversations(true);
    } catch (error) {
      $("modelBadge").textContent = error instanceof Error ? error.message : String(error);
    }
  }
  void bootstrap();
})();
