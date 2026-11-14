import { api } from "./api.js";
import { renderHistory, renderStats, resetMood, setMood } from "./render.js";
import { state } from "./state.js";
import type { Checkin, Stats } from "./types.js";

/**
 * 拉取最近记录与统计，刷新情绪轨迹页。
 *
 * updateMood 为 false 时只更新图表不动左侧星球卡——删除当前话题后，
 * 星球卡要保留用户刚看到的那条记录，避免闪回初始状态。
 */
export async function refresh(updateMood = true): Promise<void> {
  const [history, insights] = await Promise.all([
    api<{ records: Checkin[] }>("/api/history"),
    api<{ stats: Stats }>("/api/insights"),
  ]);

  state.records = history.records;
  renderHistory();
  renderStats(insights.stats);

  if (!updateMood) return;
  const latest = state.records[0];
  if (latest) setMood(latest);
  else resetMood();
}
