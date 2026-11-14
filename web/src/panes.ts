import { qsa } from "./dom.js";

/** 两个横向面板：AI 陪伴 / 情绪轨迹。 */
export type PaneName = "chat" | "journey";

/**
 * 横向切页。
 *
 * 两个面板始终在 DOM 里，靠 track 的 translateX 平移切换，
 * 因此对话状态、滚动位置都不会因为切页而丢失。
 */
export function showPane(name: PaneName): void {
  const journey = name === "journey";
  const track = document.getElementById("track");
  if (track) track.classList.toggle("to-journey", journey);
  qsa<HTMLButtonElement>(".nav-tab").forEach((button) => {
    button.classList.toggle("active", button.dataset.pane === name);
  });
}

/** 绑定顶部导航。 */
export function bindPaneNav(): void {
  qsa<HTMLButtonElement>(".nav-tab").forEach((button) => {
    button.addEventListener("click", () => {
      const pane = button.dataset.pane;
      if (pane === "chat" || pane === "journey") showPane(pane);
    });
  });
}
