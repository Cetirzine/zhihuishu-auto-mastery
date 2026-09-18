# 智慧树掌握度自动刷题（AI 智慧课程版）

一个本地脚本，替你刷智慧树「AI 智慧课程」（新形态课程）的掌握度测试：AI 做题，做错的题自动记下正确答案，下次碰到直接用，一直刷到掌握度 100% 为止。带一个网页控制台，点两下鼠标就能跑。

> [!WARNING]
> 仅供学习交流。请自行遵守平台条款，使用后果自负。

## 它能做什么

- 自动找出你账号下的 AI 智慧课程和每个知识点的掌握度，多门课都行
- 做题用 AI（DeepSeek 或 Gemini，配哪个用哪个，一家挂了自动换另一家），题目带图就截图发给视觉模型
- 答过的题存进本地题库，重复出现的题不再调 AI，整套卷子十几秒刷完
- 每次交卷后，从结果页把这套题的正确答案全部抄进题库——所以哪怕 AI 第一遍做错，第二遍就能全对
- 掌握度没到 100% 就自动再开一套，直到达标；连着几轮全对还不动分，说明这个点到顶了，自动跳下一个
- 登录一次就记住，浏览器登录态存在本地

## 准备

- Python 3.10 以上，Edge 浏览器

```bash
pip install playwright aiohttp
copy config.example.json config.json
```

编辑 `config.json`，把 `deepseek_api_key` 或 `gemini_api_key` 至少填一个。国内用 Gemini 需要代理，把 `gemini_proxy` 填上（默认写了 Clash 的 7890 端口）。默认模型是 `deepseek-flash` 和 `gemini-flash-lite-latest`，想在 `config.json` 里换也可以。

## 用法

```bash
python webgui.py
```

打开 <http://127.0.0.1:8787>：

1. 点「登录智慧树」，在弹出的浏览器里登录，之后不用再登
2. 课程列表会自动刷出来，每门课显示知识点数、未达标数、平均掌握度
3. 想先看看效果就点「预演」，只做题不交卷；正式跑点「开始刷课」
4. 右侧是实时日志，随时可以停

不想用网页也可以走命令行。`--url` 填该课程任意一个知识点的 learnPage 地址：

```bash
python shuati.py --url "<learnPage地址>" --all
python shuati.py --url "<...>" --all --provider deepseek --concurrency 2 --dry-run
```

## 配置项

| 键 | 说明 |
|---|---|
| `provider` | 主 AI 供应商：`gemini` 或 `deepseek` |
| `deepseek_api_key` / `gemini_api_key` | 两家的 Key，填一个也能跑，填两个互为备份 |
| `gemini_proxy` | 访问 Gemini 用的代理地址 |
| `target_mastery` | 目标掌握度，默认 100 |
| `max_passes_per_point` | 一个知识点最多刷几轮，默认 8，防止死循环 |
| `max_perfect_streak` | 连续几轮全对仍不涨分就放弃这个点，默认 3 |
| `min_seconds_per_question` | 每题最短停留秒数，默认 0.4 |
| `concurrency` | 同时刷几个知识点，1 到 3，默认 1 最稳 |
| `ai_timeout` | AI 调用超时秒数，默认 25 |

## 原理

平台的接口带签名，没法直接调，所以数据全靠浏览器里被动听：题目、掌握度、正确答案都是页面自己请求时截下来的。具体用了这几个接口：

- 题目内容：`/gateway/t/v1/question/getExamQuestionInfo`
- 掌握度：`/stu/exam/questions-paper` 里的 `highMasteryScore`
- 正确答案：交卷后的结果页 `/stu/exam/questions-paper-result-page`，每个选项带 `isCorrect` 标记
- 课程和知识点：`list-knowledge-theme`、`get-graph-map-tree-with-mastery`

答题不碰接口，直接点页面上的选项、翻页、点提交，和真人操作一个路径。开卷要走两跳：学习页点「去提升」到掌握度历史页，再点一次才真正开卷。

没交的卷子平台会自动恢复，所以中途停掉不丢进度。

## 文件

```
webgui.py           网页控制台的服务器
shuati.py           刷题主流程，也能单独命令行跑
ai_client.py        AI 调用，DeepSeek 和 Gemini 各一套
question_bank.py    本地题库
open_browser.py     带抓包记录的浏览器，调试用
static/index.html   控制台前端页面
config.example.json 配置模板
```

## 致谢

- [ocsjs/ocsjs](https://github.com/ocsjs/ocsjs) —— 页面选择器和答题流程参考了它的做法

## 许可

MIT
