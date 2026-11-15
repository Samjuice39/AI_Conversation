import { api, postJson } from "./api.js";
import { $, esc } from "./dom.js";
import type { AiSettings, AiStatus, Report } from "./types.js";

/** 顶部徽标：默认模型 / 我的 API / 本地规则。 */
export async function refreshAIStatus(): Promise<void> {
  const data = await api<{ llm: AiStatus }>("/api/ai/status");
  const badge = $("modelBadge");
  if (!data.llm.enabled) {
    badge.textContent = "本地规则模型";
  } else if (data.llm.source === "own") {
    badge.textContent = `我的 API · ${data.llm.model}`;
  } else {
    badge.textContent = `默认模型 · ${data.llm.model}`;
  }
}

/** 阶段回顾弹窗。 */
function bindReportDialog(): void {
  const button = $<HTMLButtonElement>("generateReport");
  const modal = $("reportModal");
  const close = (): void => modal.classList.remove("open");

  button.addEventListener("click", async () => {
    button.disabled = true;
    button.textContent = "AI 正在回顾…";
    try {
      const data = await api<{ report: Report }>("/api/insights/ai", { method: "POST" });
      $("reportTitle").textContent = data.report.title;
      $("reportOverview").textContent = data.report.overview;
      $("reportPatterns").innerHTML = data.report.patterns.map((item) => `<li>${esc(item)}</li>`).join("");
      $("reportNext").textContent = `下一步：${data.report.nextStep}`;
      modal.classList.add("open");
    } catch (error) {
      alert(error instanceof Error ? error.message : String(error));
    } finally {
      button.disabled = false;
      button.textContent = "生成 AI 阶段回顾";
    }
  });

  $("closeReport").addEventListener("click", close);
  modal.addEventListener("click", (event) => {
    if (event.target === modal) close();
  });
}

/** 注销账号弹窗。 */
function bindAccountDialog(): void {
  const modal = $("accountModal");
  const form = $<HTMLFormElement>("accountForm");
  const password = $<HTMLInputElement>("accountPassword");
  const confirmButton = $<HTMLButtonElement>("confirmAccount");

  const close = (): void => {
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
    confirmButton.textContent = "正在注销…";
    try {
      await api("/api/auth/account", {
        method: "DELETE",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ password: password.value }),
      });
      location.href = "/";
    } catch (error) {
      alert(error instanceof Error ? error.message : String(error));
      confirmButton.disabled = false;
      confirmButton.textContent = "永久注销账号";
      password.select();
    }
  });
}

/** 大模型接口设置弹窗：默认用本站模型，自己的 API 是可选项。 */
function bindAiSettingsDialog(): void {
  const modal = $("aiSettingsModal");
  const form = $<HTMLFormElement>("aiSettingsForm");
  const status = $("aiSettingsStatus");
  const baseUrl = $<HTMLInputElement>("aiBaseUrl");
  const model = $<HTMLInputElement>("aiModel");
  const apiKey = $<HTMLInputElement>("aiApiKey");
  const saveButton = $<HTMLButtonElement>("saveAiSettings");
  let hasServerDefault = false;

  const close = (): void => {
    modal.classList.remove("open");
    status.textContent = "";
    apiKey.value = "";
  };

  $("openAiSettings").addEventListener("click", async () => {
    try {
      const data = await api<{ settings: AiSettings }>("/api/ai/settings");
      hasServerDefault = data.settings.serverDefault;
      baseUrl.value = data.settings.baseUrl;
      model.value = data.settings.model;
      apiKey.value = "";
      apiKey.placeholder = data.settings.hasApiKey ? "已保存，留空表示不修改" : "sk-...";
      status.textContent = data.settings.enabled
        ? "当前使用你自己的 API，对话走你的账号与额度"
        : data.settings.serverDefault
          ? `当前使用本站默认模型（${data.settings.serverModel}），无需配置`
          : "当前未配置模型，将使用本地规则模型";
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
    status.textContent = "正在保存…";
    try {
      await postJson<{ settings: AiSettings }>(
        "/api/ai/settings",
        {
          baseUrl: baseUrl.value.trim(),
          model: model.value.trim(),
          apiKey: apiKey.value.trim(),
        },
        "PUT",
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
    const fallback = hasServerDefault ? "恢复使用本站默认模型" : "改用本地规则模型";
    if (!confirm(`清除当前账号保存的 API 配置，${fallback}吗？`)) return;
    try {
      await api("/api/ai/settings", { method: "DELETE" });
      await refreshAIStatus();
      close();
    } catch (error) {
      status.textContent = error instanceof Error ? error.message : String(error);
    }
  });
}

/** 人设下拉与自定义输入框的显隐、本地记忆。 */
function bindPersonaFields(): void {
  const persona = $<HTMLSelectElement>("persona");
  const custom = $<HTMLInputElement>("customPersona");
  const preferredName = $<HTMLInputElement>("preferredName");

  const syncCustomVisibility = (): void => {
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
  preferredName.addEventListener("input", () =>
    localStorage.setItem("emotionPreferredName", preferredName.value),
  );
}

/** 绑定三个弹窗与人设字段。 */
export function bindDialogs(): void {
  bindReportDialog();
  bindAccountDialog();
  bindAiSettingsDialog();
  bindPersonaFields();
}
