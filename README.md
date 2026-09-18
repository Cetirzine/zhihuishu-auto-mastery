# 智慧树掌握度自动刷题（AI 智慧课程版）

针对智慧树「AI 智慧课程 / 新形态课程」**掌握度测试**的本地自动化工具：AI 答题（支持 DeepSeek / Gemini 双供应商，图片题走视觉模型）+ 本地题库越刷越准 + WebGUI 一键操作，自动把每个知识点刷到 100% 掌握度。

> [!WARNING]
> 本项目仅供学习交流，请遵守平台使用条款，合理使用产生的后果由使用者自行承担。

## ✨ 特性

- **🖥️ WebGUI**：本地网页控制台（`http://127.0.0.1:8787`），一键登录 / 课程检测 / 开始停止 / 实时日志 / 在线配置
- **📚 自动课程发现**：自动列出账号下全部 AI 智慧课程与各知识点掌握度（支持多门课）
- **🤖 双 AI 供应商**：DeepSeek（`deepseek-flash`）与 Gemini（`gemini-flash-lite-latest`，国内需代理）可随时切换，一家失败自动切另一家
- **🖼️ 图片题支持**：题干含图片时自动截图交给视觉模型作答
- **💾 本地题库**：题目 ID + 题干指纹双键；答过即存，重复题零调用秒答；提交后自动从结果页采集全部正确答案（`optionDtos.isCorrect`）入库
- **🎯 刷到 100%**：每轮提交后复查 `highMasteryScore`，未达标自动重测；连续多轮全对仍不涨则止损跳下一个知识点
- **⚡ 极速作答**：题库命中的卷子整套 10 秒内完成
- **🔐 登录记住**：浏览器登录态持久化，登录一次永久生效

## 🚀 快速开始

### 1. 安装依赖

- Python 3.10+
- Edge 浏览器

```bash
pip install playwright aiohttp
```

### 2. 初始化配置

```bash
copy config.example.json config.json
# 编辑 config.json，填入 deepseek_api_key 或 gemini_api_key（至少一个）
```

### 3. 启动 WebGUI

```bash
python webgui.py
```

浏览器打开 <http://127.0.0.1:8787>：

1. 点 **🔑 登录智慧树** —— 弹出的浏览器里登录你的账号（之后自动记住）
2. 课程自动检测 —— 显示每门课的知识点数 / 未达标数 / 平均掌握度
3. 点课程卡片上的 **▶ 开始刷课**（或先 **预演** 看看 AI 答题效果，不提交）
4. 实时日志区围观进度，随时可 **⏹ 停止**

### 命令行用法（可选）

```bash
# 单知识点 / 整课模式（URL 为该课程任一知识点的 learnPage 地址）
python shuati.py --url "<learnPage地址>" --all
python shuati.py --url "<...>" --all --provider deepseek --concurrency 2 --dry-run
```

## ⚙️ 配置说明（config.json）

| 键 | 说明 |
|---|---|
| `provider` | 主 AI 供应商：`gemini` / `deepseek` |
| `deepseek_api_key` / `gemini_api_key` | API Key（国内 Gemini 需配代理） |
| `gemini_proxy` | Gemini API 代理地址 |
| `target_mastery` | 目标掌握度（默认 100） |
| `max_passes_per_point` | 单知识点最多刷几轮（防死循环，默认 8） |
| `max_perfect_streak` | 连续 N 轮全对仍不达标则止损（默认 3） |
| `min_seconds_per_question` | 每题最短间隔秒数（防过快） |
| `concurrency` | 并发知识点数 1-3（默认 1 最稳） |
| `ai_timeout` | AI 调用超时秒数（默认 25） |

## 🧠 工作原理

1. **接口嗅探**：密钥签名接口无法直接调用，全部通过 Playwright 被动监听页面 XHR 获取结构化数据
   - 题目：`/gateway/t/v1/question/getExamQuestionInfo`（题干/选项/题型，图片走 `dataFileVos`）
   - 掌握度：`/stu/exam/questions-paper` → `highMasteryScore`
   - 正确答案：结果页 `/stu/exam/questions-paper-result-page` → `optionDtos[].isCorrect`
   - 课程/知识点树：`list-knowledge-theme`、`get-graph-map-tree-with-mastery`
2. **DOM 作答**：点击选项元素（`i.iconfont` / `.el-checkbox__input`）、翻页（`.next-topic.next-t`）、提交（顶部 `span.reviewDone`），与真人操作路径一致
3. **两跳开卷**：learnPage「去提升」→ 掌握度历史页「去提升」→ 考试页
4. **自愈闭环**：AI 首答（可能错）→ 提交 → 结果页采集正确答案入库 → 下一轮题库秒答全对 → 掌握度爬升至 100%
5. **断点续刷**：平台自动恢复未提交的卷子；本地题库/配置全部持久化

## 📁 项目结构

```
webgui.py           WebGUI 服务器（aiohttp）
shuati.py           刷题主流程（可独立 CLI 运行）
ai_client.py        AI 双供应商客户端（文本/视觉）
question_bank.py    本地题库（ID+指纹双键）
open_browser.py     记录型浏览器（登录/抓包调试用）
static/index.html   WebGUI 前端
config.example.json 配置模板
```

## 🙏 致谢

- [ocsjs/ocsjs](https://github.com/ocsjs/ocsjs) —— 页面选择器与答题流程参考

## 📄 许可

[MIT](LICENSE)
