import type { Checkin, Conversation } from "./types.js";

/**
 * 页面级共享状态。
 *
 * 只有三份数据需要跨模块读写：最近记录、话题列表、当前话题 id。
 * 全部集中在这里，渲染函数从 state 读，网络层写回，避免再出现散落各处的全局变量。
 */
export interface AppState {
  records: Checkin[];
  conversations: Conversation[];
  currentConversationId: number | null;
}

export const state: AppState = {
  records: [],
  conversations: [],
  currentConversationId: null,
};
