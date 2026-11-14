"use strict";
(() => {
  // src/dom.ts
  function $(id) {
    const element = document.getElementById(id);
    if (!element) throw new Error(`\u9875\u9762\u7F3A\u5C11\u5143\u7D20 #${id}`);
    return element;
  }

  // src/login.ts
  var mode = "login";
  function setMode(next) {
    mode = next;
    $("loginTab").classList.toggle("active", mode === "login");
    $("registerTab").classList.toggle("active", mode === "register");
    $("submitBtn").textContent = mode === "login" ? "\u8FDB\u5165\u60C5\u7EEA\u661F\u7403" : "\u521B\u5EFA\u6211\u7684\u661F\u7403";
    $("password").autocomplete = mode === "login" ? "current-password" : "new-password";
    $("status").textContent = "";
  }
  $("loginTab").addEventListener("click", () => setMode("login"));
  $("registerTab").addEventListener("click", () => setMode("register"));
  $("authForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = $("submitBtn");
    button.disabled = true;
    $("status").textContent = "";
    try {
      const response = await fetch(`/api/auth/${mode}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          username: $("username").value.trim(),
          password: $("password").value
        })
      });
      const data = await response.json();
      if (!response.ok || !data.ok) throw new Error(data.error || "\u64CD\u4F5C\u5931\u8D25");
      location.href = "/";
    } catch (error) {
      $("status").textContent = error instanceof Error ? error.message : String(error);
    } finally {
      button.disabled = false;
    }
  });
})();
