/** 情绪配色：第一个是星球主色，第二个是高光色。 */
export const EMOTION_COLORS: Record<number, readonly [string, string]> = {
  0: ["#4A7BA7", "#A9C9DE"],
  1: ["#E05A47", "#F0A899"],
  2: ["#F2C744", "#F8DF95"],
  3: ["#8C8577", "#CBC4B4"],
  4: ["#8A6E9E", "#C9BBD6"],
  5: ["#E2683C", "#F2AE8C"],
};

/** 情绪编号缺失时的兜底色。 */
export const FALLBACK_COLORS = EMOTION_COLORS[0];

/** 默认星球配色（未填写任何记录时）。 */
export const IDLE_PLANET = "#EE9B3A";
export const IDLE_GLOW = "#F2C744";

/** 空状态文案，页面初次加载与清空记录时共用。 */
export const IDLE_MOOD_NAME = "等待你的分享";
export const IDLE_MOOD_META = "AI 将在这里绘制你的当下状态";
export const DEFAULT_SUMMARY =
  "这不是心理诊断，而是一种基于你主动描述的对话式理解。你可以随时纠正 AI 的判断。";

/** 人设下拉框的合法取值。 */
export const PERSONA_VALUES = ["gentle", "cool", "custom"] as const;

/** 单条录音上限，与后端 ASR_MAX_BYTES / 前端提示保持一致。 */
export const MAX_RECORD_MS = 60_000;

/** 一次流式请求的静默超时：这么久没有收到任何数据就中断。 */
export const STREAM_IDLE_TIMEOUT_MS = 90_000;
