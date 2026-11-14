/**
 * 后端接口的数据契约。
 *
 * 这里只描述前端真正读写的字段，字段名与 app.py 中的
 * checkin_dict / conversation_dict / build_stats 保持一致。
 * 改动后端返回结构时，先改这里，让类型检查把受影响的渲染逻辑暴露出来。
 */

/** 情绪编号，对应 6 套配色（0 平静、1 愤怒、2 开心、3 低落、4 焦虑、5 疲惫）。 */
export type EmotionCode = 0 | 1 | 2 | 3 | 4 | 5;

/** 风险等级。high 时前端会显示安全提示条。 */
export type RiskLevel = "low" | "medium" | "high";

/** 人设：温柔陪伴 / 酷酷解决 / 自定义。 */
export type Persona = "gentle" | "cool" | "custom";

/** 情境选项。 */
export type ContextKey = "daily" | "study" | "family" | "peers" | "sensory" | "other";

/** 反馈类型。 */
export type Feedback = "helpful" | "not_helpful";

/** 结果来源：大模型 / 本地规则 / Agent 取数。 */
export type ResultSource = "llm" | "rule" | "agent";

/** Agent 执行轨迹中的一步。 */
export interface TraceStep {
  step?: number;
  action?: string;
  tool?: string;
  tools?: string[];
  args?: Record<string, unknown>;
}

/** 根据历史反馈得到的个性化提示。 */
export interface Personalization {
  summary: string;
  hints?: string[];
  feedbackCount?: number;
}

/** 一条情绪记录，对应 checkins 表 + 分析结果。 */
export interface Checkin {
  id: number;
  conversationId: number | null;
  createdAt: string;
  text: string;
  context: ContextKey | string | null;
  sleepQuality: number | null;
  emotion: string;
  emotionCode: EmotionCode;
  intensity: number;
  confidence: number;
  summary: string;
  response: string;
  suggestion: string;
  followUpQuestion: string | null;
  persona: Persona | string | null;
  trace: TraceStep[];
  triggers: string[];
  riskLevel: RiskLevel;
  source: ResultSource | string;
  model: string | null;
  feedback: Feedback | null;
  feedbackAt: string | null;
  personalization?: Personalization;
  /** 已经向前端推过文字、但最终校验失败被降级时，后端会置真。 */
  streamTruncated?: boolean;
}

/** 话题（会话）。 */
export interface Conversation {
  id: number;
  title: string;
  createdAt: string;
  updatedAt: string;
  preview: string;
}

/** 图表用的按天计数。 */
export interface DailyCount {
  day: string;
  emotion_code: EmotionCode;
  count: number;
}

/** /api/insights 返回的统计块。 */
export interface Stats {
  total: number;
  distribution: Record<string, number>;
  daily: DailyCount[];
  contexts: Record<string, number>;
  feedbackTotal: number;
  helpfulRate: number | null;
}

/** AI 阶段回顾。 */
export interface Report {
  title: string;
  overview: string;
  patterns: string[];
  nextStep: string;
}

/** 本地语音识别（SenseVoice）可用性。 */
export interface AsrStatus {
  engine: string;
  available: boolean;
  loaded: boolean;
  cached: boolean;
  source: string;
  device: string;
  reason: string;
  deps: { state: "ready" | "installing" | "failed" | "missing" | "disabled" | string; message?: string };
}

/** 当前账号的大模型配置状态。 */
export interface AiStatus {
  enabled: boolean;
  provider: string;
  model: string;
  note?: string;
}

/** AI 设置表单回填的数据。 */
export interface AiSettings {
  enabled: boolean;
  provider: string;
  baseUrl: string;
  model: string;
  hasApiKey: boolean;
}

/** 发送一条消息时的请求体。 */
export interface ChatRequest {
  text: string;
  conversationId: number | null;
  persona: Persona;
  customPersona: string;
  preferredName: string;
  context: ContextKey;
  sleepQuality: number | null;
}

/** SSE 事件：status / trace / token / final / error 五类。 */
export type StreamEvent =
  | { type: "status"; stage: string; message: string }
  | { type: "trace"; step: TraceStep }
  | { type: "token"; text: string }
  | { type: "final"; result: Checkin }
  | { type: "error"; message: string };
