import { $ } from "./dom.js";

type AuthMode = "login" | "register";

let mode: AuthMode = "login";

/** 切换登录 / 注册：按钮文案、密码自动填充语义、错误提示一起切。 */
function setMode(next: AuthMode): void {
  mode = next;
  $("loginTab").classList.toggle("active", mode === "login");
  $("registerTab").classList.toggle("active", mode === "register");
  $("submitBtn").textContent = mode === "login" ? "进入情绪星球" : "创建我的星球";
  $<HTMLInputElement>("password").autocomplete = mode === "login" ? "current-password" : "new-password";
  $("status").textContent = "";
}

$("loginTab").addEventListener("click", () => setMode("login"));
$("registerTab").addEventListener("click", () => setMode("register"));

$<HTMLFormElement>("authForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = $<HTMLButtonElement>("submitBtn");
  button.disabled = true;
  $("status").textContent = "";

  try {
    const response = await fetch(`/api/auth/${mode}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        username: $<HTMLInputElement>("username").value.trim(),
        password: $<HTMLInputElement>("password").value,
      }),
    });
    const data = (await response.json()) as { ok?: boolean; error?: string };
    if (!response.ok || !data.ok) throw new Error(data.error || "操作失败");
    location.href = "/";
  } catch (error) {
    $("status").textContent = error instanceof Error ? error.message : String(error);
  } finally {
    button.disabled = false;
  }
});
