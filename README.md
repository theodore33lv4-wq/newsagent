# newsagent · 智能交通新闻智能体

newsagent 是一个面向智能交通领域的新闻自动化工具。它每周定时运行一次，自动从国内多个公开渠道采集当周新闻，把原文网页与正文完整存档下来，再用大模型逐条做相关性判断、分类打标和厂商信息抽取，最后汇总成一份带类别分布、主题要点、TOP 事件与趋势分析的中文周报，同时输出 HTML 与 Word 两种格式，供部门同事快速掌握行业动态。项目定位为内部实验性工具，只采集公开新闻，并始终保留来源与原文链接。

## 一、功能

- **多源采集**：内置 7 个国内源 —— 搜狐号（ITS114 智慧交通、智能交通技术）、交通运输部、工信部、中国交通新闻网、赛文交通网、盖世汽车 RSS；源清单集中在 `config/sources.yaml`，增删与启停都不需要改代码
- **网页存档**：下载原文 HTML 快照并提取正文。页面体检排除栏目、专题、聚合贴和纯列表页，短讯交给模型判断。下载或提取失败的 URL 进入重试队列，已取得的快照会保留
- **日期与归属周**：优先使用明确的发布日期线索，按配置时区确定 ISO 周。历史和后续周的新闻归到各自的周，周报仅选择目标周。日期缺失由模型补判，默认将仍无日期的相关新闻保留待核验
- **AI 打标**：逐条输出相关性判定（无关内容自动过滤）、标签（限定在标签树内，避免模型自造）、摘要、关键词、厂商/集成商公司名、重要度 1-3，其中厂商/集成商类（含中标、订单）设有重要度下限以保障占比；**默认按 10 条一批调用**（一次请求同时完成是否新闻、发布日期、标签、摘要、厂商等多项判断）
- **每周综述**：主题直接复用打标的一级标签（确定性分组，跨周可比较）；大模型负责编写逐条要点、本周概览与要点、TOP5、趋势三块（宏观市场动态 / 重点工程与项目 / 集成商产品规划建议）以及下周关注
- **双格式周报**：自包含单文件 HTML（含类别分布条形图，原文链接可直接点开）与 Word 版
- **去重**：采集时合并规范化 URL 相同的条目，保留不同来源的存档。周报合并全文相同的稿件，也识别同标题、相邻发布日期且正文仅多出少量附加文字的跨源转载
- **可选推送**：完整生成后可通过钉钉工作通知发送 HTML 周报。运行异常可配置群机器人告警
- **运行状态**：单个来源失败时继续处理其他来源。摘要区分完成、无相关条目、部分完成和失败，采集、分类、综述降级或推送失败会返回非零退出码

## 二、工作原理

```
定时（Windows 任务计划，每周一 08:00）或手动执行
        ↓
① 采集  多源拉取候选条目 → 与历史记录比对去重（列表层顺带解析日期）
        ↓
② 存档  并发下载 HTML 快照 → 提取正文 → 新闻页体检 + 按发布周归档 → 落盘 + SQLite 索引
        ↓
③ 分类  大模型按批打标（每批 10 条，一次完成是否新闻/发布日期/标签/摘要/厂商/相关性）
        ↓
④ 综述  主题取自打标一级标签 → 一次大模型调用产出逐条要点 + 概览/TOP5/趋势/下周关注
        ↓
⑤ 产物  data/reports/<周>/weekly-<周>.{html,docx,json}
```

默认每 10 条新闻进行一次批次打标，另进行一次综述调用。批次缺项、无效输出或接口重试会增加调用次数。

**周口径**：目标周 = 今天减 7 天所在的 ISO 周。周一运行生成上一完整周的周报，一周内任意一天补跑都落在同一周，便于重跑与跨周对比。

**大模型接入**：在 `config.yaml` 中切换 `openai-compat`（默认）、`ollama`（本地模型）或 `mock`（无需 Key，用于验证流程）。打标与综述可分别指定模型。打标失败的条目保留待处理，已有可用条目时可生成周报。综述缺失会用存档摘要和统计补齐，并标记降级。

## 三、目录结构

```
newsagent/
├── config/
│   ├── config.yaml       # 主配置：大模型 / 采集 / 分类 / 综述 / 通知
│   ├── sources.yaml      # 新闻源清单（新增源改这里即可）
│   └── taxonomy.yaml     # 标签树（大模型只能从树中选择标签）
├── src/newsagent/
│   ├── pipeline.py       # 流水线编排：采集→存档→分类→综述→推送
│   ├── cli.py / __main__.py   # 命令行入口（python -m newsagent）
│   ├── collect/          # base(Article/HTTP) · sohu · websites · rss · search · dedup
│   ├── archive/          # downloader(快照+正文) · store(文件 + SQLite 检索)
│   ├── classify/         # llm(OpenAICompat/Ollama/Mock) · tagger(打标)
│   ├── report/           # generator(综述) · render(Jinja2→HTML) · export(→Word) · templates/
│   └── utils/            # config · logging · dates(周编号) · notify · dingtalk
├── scripts/
│   ├── run_weekly.py     # 每周运行入口（定时任务调用它）
│   ├── check_env.py      # 环境自检：依赖 / 配置 / 目录 / 模型 / 各源可达性 / 渲染
│   ├── install_task.ps1  # 默认注册每周一 08:00 定时任务，加 -Uninstall 可移除
│   ├── recover_filtered.py  # 按 GUID 恢复被误过滤的条目
│   └── import_articles.py    # 导入/重置某周存档条目（标签体系调整后重新打标）
├── tests/                # 单元与集成测试（模拟模型和采集服务）
├── data/                 # 运行产物（已 gitignore）
│   ├── raw/<周>/         # 原始 HTML 快照
│   ├── articles/<周>/    # 正文与元数据 JSON
│   ├── reports/<周>/     # weekly-<周>.html / .docx / .json
│   ├── index.sqlite3     # 检索索引（按周 / 标签 / 关键词 / 公司 / 重要度）
│   └── logs/             # 滚动日志
└── pyproject.toml / .env.example / README.md
```

## 四、部署

以下步骤适用于一台全新的 Windows 电脑。

**1. 安装基础环境**

- 安装 Python 3.11 或更高版本（官网下载安装包，安装时勾选 `Add python.exe to PATH`）
- 安装 Git（用于获取代码与后续更新）
- 打开 PowerShell，执行 `python --version` 与 `git --version` 确认两者可用

**2. 获取代码**

```powershell
cd C:\Users\<你的用户名>\Desktop
git clone <仓库地址> newsagent
cd newsagent
```

如果直连 GitHub 不稳定，可在 `git clone` 前临时设置代理：`git config --global http.proxy http://127.0.0.1:7890`（端口按实际代理调整）。

**3. 创建虚拟环境并安装依赖**

```powershell
python -m venv .venv
.venv\Scripts\pip install -e .
```

下载较慢时可加国内镜像：`.venv\Scripts\pip install -e . -i https://pypi.tuna.tsinghua.edu.cn/simple`

**4. 写入配置**

```powershell
Copy-Item .env.example .env
notepad .env
```

在 `.env` 中填入 `LLM_API_KEY`（DeepSeek 平台申请）。新闻源、标签树、运行参数分别在 `config/sources.yaml`、`config/taxonomy.yaml`、`config/config.yaml` 中调整。

**5. 环境自检**

```powershell
.venv\Scripts\python scripts\check_env.py
```

依次检查 Python 与依赖、配置加载、数据目录可写、大模型配置、各新闻源可达性、周报渲染是否正常，全部通过后即可运行。

**6. 试运行与正式运行**

```powershell
.venv\Scripts\python scripts\run_weekly.py --limit 5   # 只处理 5 条，核验标签与周报质量
.venv\Scripts\python scripts\run_weekly.py             # 完整运行，生成上一完整周周报
```

产物位于 `data\reports\<周>\`，HTML 双击即可在浏览器打开。

**7. 注册定时任务**

确认系统时区为北京时间（`tzutil /s "China Standard Time"`），然后以**管理员身份**打开 PowerShell：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install_task.ps1
```

新注册任务默认每周一 08:00 运行。使用 `-Time "09:00"` 可指定时间，使用 `-Uninstall` 可移除任务。已注册任务沿用注册时设置的时间。

**8. 后续更新**

```powershell
git pull
```

代码更新后定时任务无需重新注册；若某次更新新增了依赖，补执行一次 `.venv\Scripts\pip install -e .`。

**常用命令**

| 命令 | 说明 |
|---|---|
| `run_weekly.py` | 完整运行，生成上一完整周周报 |
| `run_weekly.py --limit 5` | 只处理前 5 条新条目，用于小规模验证 |
| `run_weekly.py --dry-run` | 只访问来源列表并预览去重结果，不下载正文，不写入文件或数据库 |
| `run_weekly.py --regen --week 2026-W36` | 跳过采集，重新生成指定周周报 |
| `run_weekly.py --provider mock` | 临时切换为 Mock 模型 |
| `python -m newsagent report --week 2026-W36` | 以子命令形式重新生成指定周周报 |
| `run_monthly.py --month 2026-10` | 读取已打标归档，使用配置中的模型生成指定自然月的月报 |
| `check_env.py` | 环境自检 |
| `install_task.ps1 [-Time "09:00"] [-Uninstall]` | 注册 / 调整 / 移除定时任务 |
| `run_weekly.py --week 2026-W38 --no-collect` | 只处理指定周的待分类、待核验日期条目，并生成周报 |
| `run_weekly.py --log-level WARNING` | 设置控制台日志级别 |
| `import_articles.py --week 2026-W38 [--reset]` | 导入存档并保留索引中已有修正。加 `--reset` 将归属该周的已分类条目放回待分类队列 |
| `recover_filtered.py --week 2026-W38` | 查看被过滤的条目及其 GUID 和原因 |
| `recover_filtered.py --week 2026-W38 --guid <GUID> --apply` | 恢复指定条目，可重复指定 `--guid` |
| `python -m pytest -q` | 运行单元与集成测试 |

**手动生成月报**

```powershell
.\.venv\Scripts\python.exe scripts\run_monthly.py --month 2026-10
```

月报按配置时区内的发布日期取数，只读已有归档。先确认覆盖月末的周任务已处理完。月报会提示已知待处理、日期未确认的记录以及采集覆盖情况。`--data-dir` 可指定另一份已有归档。

默认使用项目配置中的模型，可能调用 API 并产生费用。正式月报保存到 `data/reports/2026-10/monthly-2026-10.*`。加 `--provider mock` 可离线生成预览，产物名带 `-preview` 后缀。输出格式沿用 `report.formats`，JSON 始终保留。月报目前由手动命令生成。

`report.monthly_max_items` 默认给综述模型最多 60 条证据，按一级主题轮流选取。统计和附录使用当月全部去重后的相关新闻。重点项目按报道日期展示节点，HTML 正文中的引用编号可打开新闻原网页。

**数据补救与备份**。先查看清单并选中误过滤条目。有正文的条目恢复后可用 `--no-collect` 重跑，空正文条目会在正常采集时重新下载。SQLite 保存分类结果和后续日期修正，完整备份应同时包含 `data/index.sqlite3`、`data/articles/` 和 `data/raw/`。单独导入原始存档无法还原索引中的分类和后续修正。

## 五、配置要点

**`config.yaml`**。`llm.tagger_model` 与 `llm.report_model` 分别指定打标和综述模型，留空时使用 `llm.model`。`archive.strict_week` 默认开启，日期无法确认的相关新闻留待核验。关闭后，未确认日期的条目可保留在原存档周。已知日期始终按实际发布日期归周。`report.formats` 选择 HTML 或 Word，JSON 始终保留。`report.max_items` 只限制模型输入，统计和附录保留全部去重后的相关新闻。

打标默认只处理目标周，并记录每批进度和耗时。其他周条目继续排队，可按周补跑，也可启用 `classify.process_backlog` 在正常采集时处理积压。页面链接密度较高时，`archive.min_prose_chars` 指定可交给模型复核的独立正文长度。周报会检查综述中的引用编号、数值、统计年份和部分项目阶段，证据不足的表述用存档摘要替换或移除。

**`sources.yaml`**。支持 `sohu_account`、`website` 和 `rss`。网站默认识别带 `rel=next` 或标注“下一页”的链接，可用 `next_page_selector` 指定站点的下一页选择器，并用 `max_pages` 和 `limit` 限制采集量。摘要会提示分页上限、订阅保留范围或整周覆盖未经验证。

**`taxonomy.yaml`**：5 个一级标签 —— **市场与项目**（项目发布、厂商中标、市场综述）、**政策标准与行业**（政策法规、标准、赛事、展会）、**交管技术与业务**（信控、渠化、活动保障、车流引导、秩序安全）、**云脑与智能体**（大模型、智能体、数据集、仿真、数据中台）、**边缘与终端**（感知识别、信号诱导、边缘计算、具身智能、无人机低空），每个一级下约 5 个二级标签。周报的类别分布与分主题只使用一级标签，二级标签用于分类与检索；LLM 只能从树中选择标签。

**`.env`**（不纳入版本管理）：`LLM_API_KEY`；可选 `NOTIFY_WEBHOOK_URL`（群机器人告警）与 `DINGTALK_APP_KEY`、`DINGTALK_APP_SECRET`、`DINGTALK_AGENT_ID`、`DINGTALK_USERIDS`（钉钉周报推送）。

## 六、已知限制

- **整周覆盖**：未提供可用分页的列表、搜狐候选接口和 RSS 的历史保留范围由来源决定。每周运行一次时，已移出来源列表的新闻可能无法补采
- **厂商动态占比**：接入赛文交通网等渠道后已开始稳定产出，后续可继续补充集成商、招投标类新闻源
- **微信生态**：公众号正文需要 mp.weixin 直链才能存档，目前没有稳定的周期化渠道；搜狗微信搜索只能做标题层面的监测
- **钉钉推送**：代码已完成，等待企业内部应用审批发布后启用，并需适配新版开放平台接口
- **规划中**：内网 Web 检索站（可直接复用现有 SQLite 查询接口）、月报与趋势对比、人工审核与反馈入口

## 七、合规

仅采集公开新闻，保留来源与原文链接，供部门内部分析使用，不对外发布；采集过程设置请求节流与友好 User-Agent；单个源失效不影响其他源。
