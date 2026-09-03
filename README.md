# newsagent · 智能交通新闻智能体

面向部门内部（≤10 人）的**智能交通领域新闻采集与周报自动化工具**。每周一自动完成：采集国内公开新闻 → 网页存档 → AI 打标分类 → 生成周报（HTML + Word 双格式）。

> 实验性质项目 · 内部使用 · 所有新闻源均为国内公开渠道，仅存档注明来源的公开内容。

---

## 功能特性

- **多源采集**：搜狐号（ITS114「智慧交通」为核心源）、国内新闻网站栏目、RSS 订阅，均可在 `config/sources.yaml` 配置增删；搜索补充为可选功能（默认关闭）
- **网页存档**：原始 HTML 快照 + 正文提取（trafilatura）双份留存，提取失败的页面保留快照并标注"待人工"
- **AI 打标**：DeepSeek 等 LLM 统一封装，输出结构化结果 —— 相关性判定（无关自动过滤）、标签（预定义标签树，禁止幻觉标签）、100 字摘要、关键词、**厂商/集成商公司名抽取**、重要度（1-3，厂商动态有下限保障）
- **每周综述**：主题分组直接复用打标一级标签（确定性、跨周可比），LLM 生成逐条要点与综述（概览/TOP5/趋势/下周关注），输出**自包含单文件 HTML**（浏览器直接打开）与 **Word** 双格式；LLM 不可用时自动降级，综述永不失败
- **跨周去重**：URL 规范化 → 标题归一化 → 内容哈希三层防线，同篇新闻不重复入档
- **检索数据层**：SQLite 索引提供 `query(周/标签/关键词/公司/重要度)` 接口，为将来 Web 检索站直接复用
- **可靠运维**：单源失败不影响整体、日志滚动留存（`data/logs/`）、失败汇总 + 可选企业微信/钉钉/飞书告警、`check_env.py` 环境自检

## 工作原理

```
[Windows 任务计划] 每周一 08:00（或手动）
        ↓
① 采集    搜狐号 / 新闻网站 / RSS 多源并行 + 三层去重
        ↓
② 存档    下载原文 HTML 快照 → 提取正文 → 文件 + SQLite 索引
        ↓
③ 分类    LLM 打标签 + 摘要 + 厂商抽取 + 相关性过滤（并行，失败重试/降级）
        ↓
④ 综述    主题=打标一级标签（确定性）→ LLM 逐条要点 → 概览/TOP5/趋势/下周关注
        ↓
⑤ 产物    data/reports/<周>/weekly-<周>.{html,docx,json}
```

LLM 通过统一抽象层接入，`config.yaml` 一行切换（`openai-compat` 国内 API / `ollama` 本地 / `mock` 无 Key 验证），打标与综述可分用不同模型。

## 快速开始

**前置**：Python 3.11+，本机可访问国内新闻站与 LLM API（GitHub 直连不稳定时，推送可用 `git -c http.proxy=http://127.0.0.1:7890 push` 走本地代理）。

```powershell
# 1) 安装
python -m venv .venv
.venv\Scripts\pip install -e .                 # 网络慢可加 -i https://pypi.tuna.tsinghua.edu.cn/simple
.venv\Scripts\pip install -e ".[dev]"          # 含 pytest（可选）

# 2) 配置 API Key
Copy-Item .env.example .env                    # 打开 .env 填入 LLM_API_KEY（DeepSeek 平台申请）

# 3) 环境自检（API 连通 / 源可达 / 目录可写）
.venv\Scripts\python scripts\check_env.py

# 4) 试跑 → 全量跑
.venv\Scripts\python scripts\run_weekly.py --limit 5   # 小规模真实验证（写入 data/）
.venv\Scripts\python scripts\run_weekly.py            # 完整跑本周
```

**常用命令**

| 命令 | 说明 |
|---|---|
| `run_weekly.py` | 完整跑当前周（采集→存档→打标→综述） |
| `run_weekly.py --limit 5` | 小规模真实验证（只处理前 5 条新条目） |
| `run_weekly.py --dry-run` | 仅采集+去重预览，不做任何写入 |
| `run_weekly.py --regen --week 2026-W09` | 跳过采集，仅重新生成指定周综述 |
| `run_weekly.py --provider mock` | 临时切 Mock（无 Key 验证流水线） |
| `check_env.py` | 六项环境自检（退出码 0/1/2） |
| `install_task.ps1` | 注册每周一 08:00 任务计划（管理员运行） |

## 目录结构

```
newsagent/
├── README.md / pyproject.toml / .env.example / .gitignore
├── config/
│   ├── config.yaml      # 主配置：LLM/采集/分类/综述/告警
│   ├── sources.yaml     # 新闻源清单（新增源改这里，多数情况零代码）
│   └── taxonomy.yaml    # 标签树（LLM 只能从树中选标签）
├── src/newsagent/
│   ├── pipeline.py      # 每周流水线编排（CLI 入口）
│   ├── collect/         # sohu_account / website / rss / search 适配器 + 去重
│   ├── archive/         # 快照下载、正文提取、SQLite 存储与检索
│   ├── classify/        # LLM 抽象（OpenAICompat/Ollama/Mock）与打标器
│   ├── report/          # 综述生成、HTML 渲染（Jinja2）、Word 导出
│   └── utils/           # 配置/日志/日期周编号/告警
├── scripts/             # run_weekly / check_env / install_task
├── tests/               # 44 个单元与集成测试（Mock LLM，不耗 API）
└── data/                # 运行产物（gitignore）：raw/ articles/ reports/ logs/ index.sqlite3
```

## 配置说明

### `config/config.yaml`（要点）

- `llm.provider`：`openai-compat`（默认，DeepSeek 等国内 API）/ `ollama` / `mock`；`llm.model` 按 API 实际模型名填写（DeepSeek 官方模型名全部为**小写**，如 `deepseek-v4-flash-vision-exp`）
- `collect.search.enabled`：搜索引擎补充（默认关闭；必应中文结果质量不稳定，实测后保留）
- `classify.vendor_importance_floor`：厂商动态新闻重要度下限，保障集成商内容占比
- `report.formats`：`["html", "docx"]` 输出格式
- `notify.enabled` + `.env` 的 `NOTIFY_WEBHOOK_URL`：失败告警

### `config/sources.yaml` —— 如何新增新闻源

| 源类型 | 所需字段 | 说明 |
|---|---|---|
| `sohu_account` | `profile_url`（+ `xpt`） | 搜狐号主页，解析服务端内嵌数据（实测 ITS114 20 条/页） |
| `website` | `list_url` + `article_selector` | 普通新闻网站栏目页，CSS 选择器选文章链接 |
| `rss` | `url` | RSS 订阅源 |

新增源时把条目加入 `sources.yaml` 并置 `enabled: true` 即可；特殊站点（SPA/公众号/需登录）若静态采集不可行，请在日志确认后联系维护者评估适配方案。已于 2026-08 实测记录：ITS114 官网（SSL 阻断）、赛文交通网（小鹅通 SPA）、中国政府采购网（反爬）暂不可用。

### `config/taxonomy.yaml`

一级标签：自动驾驶 / 车路协同·智能网联 / 智慧高速 / 智能公交·出租 / 信号与交管 / 政策法规 / 标准规范 / 产业动态·投融资 / **厂商动态**（集成商/设备商/中标与采购）/ 海外动态 / 事故与安全 / 展会会议 / 其他。可按需增删；LLM 只允许从树中选择（二级标签格式 `一级/二级`）。

## 输出产物

```
data/
├── raw/2026-W35/…html        # 原始网页快照（可随时打开核对）
├── articles/2026-W35/…json   # 正文 + 摘要 + 标签 + 厂商等元数据
├── reports/2026-W35/
│   ├── weekly-2026-W35.html  # 自包含周报（浏览器直接打开，可分享）
│   ├── weekly-2026-W35.docx  # Word 版
│   └── weekly-2026-W35.json  # 结构化中间数据（便于重新渲染/二次加工）
├── index.sqlite3             # 检索索引（未来 Web 站复用）
└── logs/                     # 滚动日志（10MB × 30 天）
```

## 部署到 Windows Server（定时自动运行）

1. 安装 Python 3.11+（勾选 Add to PATH）与 git，`git clone` 本仓库
2. `python -m venv .venv` → `.venv\Scripts\pip install -e .`
3. 复制 `.env.example` 为 `.env` 填 Key；运行 `scripts\check_env.py` 自检
4. 试运行 `scripts\run_weekly.py --limit 5` 核验产物
5. 管理员 PowerShell 执行 `scripts\install_task.ps1` 注册每周一 08:00 任务（先确认系统时区为北京时间：`tzutil /s "China Standard Time"`）
6. 把 `data\reports\` 目录共享给部门成员即可

> 云主机若无法从外部访问（RDP 可通但其他端口不通），请检查**云安全组**入方向规则（平台安全组与系统防火墙是两层）。

## 测试

```powershell
.venv\Scripts\python -m pytest -q     # 44 个用例：配置/日期/去重/存储/提取/LLM/打标/综述/流水线/告警
```

所有测试基于 Mock LLM 与临时目录，不消耗 API 额度、不访问网络。

## 已知限制与规划

- **厂商动态占比**：当前源偏政策/媒体；赛文交通网（集成商密集）因小鹅通 SPA 暂不可用，补充"集成商/中标"类源是下一步重点
- **微信生态**：公众号内容无公开接口，暂未覆盖
- **规划中**：Web 检索站（FastAPI + 现有 SQLite 查询接口，查询层已就绪）、月报/趋势分析、真实世界周报人工审核门户、Ollama 本地模型实测

## 合规说明

仅采集并存档公开新闻，保留来源与原文链接，供部门内部分析使用，不对外发布；采集设置节流与友好 User-Agent，单源失效不影响其他源。
