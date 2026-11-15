# 测试脚本

独立的回归测试脚本（非 pytest，直接 `python tests/xxx.py` 运行，结束打印 PASS/FAIL 汇总）。
测试账号在结束时自动注销，不污染数据。

## 脚本清单

| 脚本 | 覆盖内容 | 运行前提 |
|---|---|---|
| `quota_test.py` | 默认模型每日额度：扣减、失败返还、超限 429、并发不破上限、跨天恢复、自有 API 不消耗、危机不被拦截 | 无（进程内 mock，临时数据库） |
| `api_test.py` | 全接口回归：鉴权越权、参数校验、危机、话题/反馈/统计、AI 设置、语音、注销、XSS | 服务运行中 |
| `ui_test.py` | 无头 Chrome 全主流程 + 四档分辨率无滚动溢出 | 服务运行中 + 本机 Chrome |
| `default_llm_test.py` | 服务端默认模型：未配置回落、自有 API 覆盖、清除回落 | 服务运行中且 `.env` 配好默认模型 |
| `voice_test.py` | 语音链路端到端：假麦克风播放样例 WAV，验证按住说话→识别→填词 | 服务运行中 + 本机 Chrome，本地 SenseVoice 权重就绪 |

## 运行方式

```bash
# 全量回归时，务必调大额度，避免测试耗光演示账号的每日 10 次
set EMOTION_LLM_DAILY_LIMIT=1000
python app.py

# 另开终端
python tests/quota_test.py        # 不需要服务
python tests/api_test.py
python tests/ui_test.py
python tests/default_llm_test.py
python tests/voice_test.py
```

浏览器 profile 与截图写入系统临时目录，不在仓库内产生文件。
