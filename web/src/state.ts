import type { Checkin, Conversation } from "./types.js";

/**
 * 页面级共享状态。
 *
 * 跨模块共享的三份数据：最近记录、话题列表、当前话题 id。
 * 渲染函数从 state 读，网络层写回。
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
