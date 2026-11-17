/** 极小的 DOM 工具集，避免为了取元素引入框架。 */

const ESCAPE_MAP: Record<string, string> = {
  "&": "&amp;",
  "<": "&lt;",
  ">": "&gt;",
  "'": "&#39;",
  '"': "&quot;",
};

/**
 * 按 id 取元素，元素不存在时直接抛错。
 */
export function $<T extends HTMLElement = HTMLElement>(id: string): T {
  const element = document.getElementById(id);
  if (!element) throw new Error(`页面缺少元素 #${id}`);
  return element as T;
}

/** 批量查询，返回强类型数组。 */
export function qsa<T extends Element = HTMLElement>(
  selector: string,
  root: ParentNode = document,
): T[] {
  return Array.from(root.querySelectorAll<T>(selector));
}

/** 转义成可直接拼进 innerHTML 的文本。 */
export function esc(value: unknown): string {
  return String(value ?? "").replace(/[&<>'"]/g, (char) => ESCAPE_MAP[char]);
}

/** 从事件目标向上找带某个 data 属性的元素。 */
export function closestWith<T extends HTMLElement>(target: EventTarget | null, selector: string): T | null {
  if (!(target instanceof Element)) return null;
  return target.closest<T>(selector);
}

/** 滚到消息列表底部。 */
export function scrollMessagesToEnd(): void {
  const messages = $("messages");
  messages.scrollTop = messages.scrollHeight;
}
