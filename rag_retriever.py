# coding: utf-8
"""情绪干预策略检索（RAG 的 Retrieval 部分）。

设计取舍
--------
1. 零第三方依赖：只用标准库。评委 `pip install -r requirements.txt` 后即可运行，
   不需要下载嵌入模型（sentence-transformers 会连带 torch，2 GB 起步）。
2. 中文检索用「字符 bigram + 单字」做 BM25F：
   - 中文没有天然空格，按词切分需要 jieba 之类的分词器，又是一个依赖；
   - 字符 bigram 在短文本上召回效果接近分词，且不需要词典；
   - 单字一并入索引，避免 bigram 漏掉单字关键词。
3. 字段加权（BM25F）：标题和「适用情境」权重高于正文。
   实测过不加权的纯 BM25：查询「旁边一直有人说话特别烦」会召回「给一个人发条消息」，
   因为两者共享「有人说话」这个字面片段。加权后情境词（烦躁/被打扰）才压得住。
4. 知识库规模在几十条量级，BM25F 的排序质量足够；向量检索在这个量级上
   的优势不明显，反而引入部署成本。
5. 检索结果只作为「参考素材」注入 prompt，不直接展示给用户——
   保持对话风格由 _STYLE_GUIDE 控制，不被知识库文案污染。
"""

import json
import math
import re
from pathlib import Path

KB_PATH = Path(__file__).resolve().parent / "knowledge" / "interventions.json"

# BM25 参数：k1 控制词频饱和，b 控制文档长度归一化
K1 = 1.5
B = 0.75

# 字段权重：标题与适用情境更能代表「这条策略是干什么的」
FIELD_WEIGHTS = (
    ("title", 3.0),
    ("scenario", 2.5),
    ("when", 1.5),
    ("content", 1.0),
)

_cache = {"docs": None, "idf": None, "avgdl": None, "postings": None, "lengths": None}

# 情绪码 → 该情绪的典型说法，用于查询扩展。
# 用户原话未必带情绪词（「旁边一直有人说话特别烦」只出现一个「烦」），
# 补上同义说法才能命中知识库里 scenario 字段的情绪词。
EMOTION_HINTS = {
    0: "平静 说不清 还好 一般",
    1: "焦虑 紧张 担心 慌 害怕",
    2: "开心 高兴 兴奋 好消息",
    3: "疲惫 累 没精神 困 脑子转不动",
    4: "低落 难过 失望 委屈 想哭 孤独",
    5: "烦躁 生气 烦 火大 被打扰",
}


def _tokenize(text):
    """中文切字符 bigram + 单字；英文按单词。"""
    if not text:
        return []
    text = text.lower()
    tokens = re.findall(r"[a-z]{2,}", text)
    for seg in re.findall(r"[\u4e00-\u9fff]+", text):
        for i in range(len(seg) - 1):
            tokens.append(seg[i:i + 2])
        tokens.extend(seg)
    return tokens


def _field_text(item, name):
    value = item.get(name, "")
    if isinstance(value, list):
        return " ".join(value)
    return value or ""


def _load():
    """加载知识库并构建倒排索引（首次调用时执行，之后走缓存）。"""
    if _cache["docs"] is not None:
        return

    if not KB_PATH.exists():
        _cache.update({"docs": [], "idf": {}, "avgdl": 0, "postings": {}, "lengths": []})
        return

    with KB_PATH.open(encoding="utf-8") as fh:
        docs = json.load(fh)

    postings = {}   # token -> {doc_index: weighted_tf}
    lengths = []
    df = {}

    for idx, item in enumerate(docs):
        weighted_tf = {}
        doc_len = 0
        for fname, weight in FIELD_WEIGHTS:
            tokens = _tokenize(_field_text(item, fname))
            doc_len += len(tokens)
            for t in tokens:
                weighted_tf[t] = weighted_tf.get(t, 0.0) + weight
        lengths.append(doc_len)
        for t, w in weighted_tf.items():
            postings.setdefault(t, {})[idx] = w
            df[t] = df.get(t, 0) + 1

    n_docs = len(docs)
    idf = {t: math.log(1 + (n_docs - d + 0.5) / (d + 0.5)) for t, d in df.items()}
    avgdl = (sum(lengths) / n_docs) if n_docs else 0

    _cache.update({
        "docs": docs,
        "idf": idf,
        "avgdl": avgdl,
        "postings": postings,
        "lengths": lengths,
    })


def retrieve(query, emotion_code=None, top_k=3, min_score=3.0):
    """按查询文本召回相关干预策略。

    参数
    ----
    query : 用户原话
    emotion_code : 0平静 1焦虑 2愉悦 3疲惫 4低落 5烦躁，可为 None
    top_k : 返回条数
    min_score : 低于该分数的条目不返回，避免硬塞无关内容

    返回
    ----
    list[dict]，每项含 id / title / content / when / score
    """
    _load()
    docs = _cache["docs"]
    if not docs:
        return []

    tokens = _tokenize(query or "")
    if not tokens:
        return []

    # 查询扩展：用情绪码补上同义说法，缓解「字面重合但情绪不同」的误召回
    if emotion_code is not None and emotion_code in EMOTION_HINTS:
        tokens = tokens + _tokenize(EMOTION_HINTS[emotion_code])

    scores = {}
    idf = _cache["idf"]
    postings = _cache["postings"]
    lengths = _cache["lengths"]
    avgdl = _cache["avgdl"] or 1.0

    for t in set(tokens):
        if t not in postings:
            continue
        w = idf.get(t, 0)
        for idx, tf in postings[t].items():
            dl = lengths[idx]
            denom = tf + K1 * (1 - B + B * dl / avgdl)
            scores[idx] = scores.get(idx, 0) + w * tf * (K1 + 1) / denom

    # 情绪码匹配加权：已知情绪时，同情绪的策略优先，异情绪降权但不硬性剔除
    if emotion_code is not None:
        for idx, item in enumerate(docs):
            if idx not in scores:
                continue
            if emotion_code in item.get("emotion", []):
                scores[idx] *= 1.2
            else:
                scores[idx] *= 0.45

    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    results = []
    for idx, score in ranked:
        if score < min_score:
            break
        item = docs[idx]
        results.append({
            "id": item.get("id"),
            "title": item.get("title", ""),
            "content": item.get("content", ""),
            "when": item.get("when", ""),
            "score": round(score, 3),
        })
        if len(results) >= top_k:
            break

    return results


def format_for_prompt(items):
    """把检索结果整理成适合塞进 prompt 的紧凑结构。"""
    return [{"参考做法": it["title"], "具体步骤": it["content"]} for it in items]


def describe():
    """知识库概况，供 /api/ai/status 或调试接口展示。"""
    _load()
    docs = _cache["docs"]
    by_emotion = {}
    for item in docs:
        for code in item.get("emotion", []):
            by_emotion[code] = by_emotion.get(code, 0) + 1
    return {
        "enabled": bool(docs),
        "total": len(docs),
        "by_emotion": by_emotion,
        "backend": "bm25f-char-bigram",
    }


if __name__ == "__main__":
    print(describe())
    cases = [
        ("明天要上台展示，很紧张怕忘词", 1),
        ("昨晚没睡好，今天什么都不想做", 3),
        ("旁边一直有人说话特别烦", 5),
        ("我已经很努力了但结果还是不好", 4),
        ("终于把东西做完了，特别开心", 2),
    ]
    for q, code in cases:
        print(f"\n查询（情绪码 {code}）: {q}")
        for r in retrieve(q, emotion_code=code, top_k=3):
            print(f"  [{r['score']:6.2f}] {r['title']}")
