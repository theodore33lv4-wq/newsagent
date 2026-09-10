# newsagent · 智能交通新闻智能体

newsagent 是一个面向智能交通领域的新闻自动化工具。它每周定时运行一次，自动从国内多个公开渠道采集当周新闻，把原文网页与正文完整存档下来，再用大模型逐条做相关性判断、分类打标和厂商信息抽取，最后汇总成一份带类别分布、主题要点、TOP 事件与趋势分析的中文周报，同时输出 HTML 与 Word 两种格式，供部门同事快速掌握行业动态。项目定位为内部实验性工具，只采集公开新闻，并始终保留来源与原文链接。

## 一、功能

- **多源采集**：内置 7 个国内源 —— 搜狐号（ITS114 智慧交通、智能交通技术）、交通运输部、工信部、中国交通新闻网、赛文交通网、盖世汽车 RSS；源清单集中在 `config/sources.yaml`，增删与启停都不需要改代码
- **网页存档**：下载原文 HTML 快照并用 trafilatura 提取正文，提取失败的页面保留快照并标注，随时可以打开核对
- **AI 打标**：逐条输出相关性判定（无关内容自动过滤）、标签（限定在标签树内，避免模型自造）、摘要、关键词、厂商/集成商公司名、重要度 1-3，其中厂商动态类设有重要度下限以保障占比
- **每周综述**：主题直接复用打标的一级标签（确定性分组，跨周可比较）；大模型负责编写逐条要点、本周概览与要点、TOP5、趋势三块（宏观市场动态 / 重点工程与项目 / 集成商产品规划建议）以及下周关注
- **双格式周报**：自包含单文件 HTML（含类别分布条形图，原文链接可直接点开）与 Word 版
- **跨周去重**：URL 规范化、标题归一化、内容哈希三层比对，同一篇新闻不会重复入档
- **可选推送**：周报生成后可通过钉钉工作通知把 HTML 文件发给指定同事；运行异常可选发送群机器人告警
- **运维友好**：单个源失败不影响整体流程，日志按周滚动留存，`check_env.py` 一条命令完成环境自检，每次运行输出一段简明摘要

## 二、工作原理

```
定时（Windows 任务计划，每周一 08:30）或手动执行
        ↓
① 采集  多源拉取候选条目 → 与历史记录比对去重
        ↓
② 存档  并发下载 HTML 快照 → 提取正文 → 落盘 + 写入 SQLite 索引
        ↓
③ 分类  大模型逐条打标（并发执行，失败重试或降级），无关内容出局
        ↓
④ 综述  主题取自打标一级标签 → 大模型编写要点 → 大模型编写概览/TOP5/趋势/下周关注
        ↓
⑤ 产物  data/reports/<周>/weekly-<周>.{html,docx,json}
```

**周口径**：目标周 = 今天减 7 天所在的 ISO 周。周一运行生成上一完整周的周报，一周内任意一天补跑都落在同一周，便于重跑与跨周对比。

**大模型接入**：`config.yaml` 一行切换 `openai-compat`（DeepSeek 等国内 API，默认）、`ollama`（本地模型）或 `mock`（无需 Key，用于验证流程）；打标与综述可以分别指定模型。任意一步模型不可用都会自动降级，周报始终能够产出。

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
│   └── install_task.ps1  # 注册每周一 08:30 定时任务，加 -Uninstall 可移除
├── tests/                # 50 个单元与集成测试（使用 Mock 模型，不耗额度、不联网）
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

任务将在每周一 08:30 自动运行。把 `data\reports\` 目录共享给部门同事即可查看；需要调整时间可加 `-Time "09:00"`，移除任务加 `-Uninstall`。

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
| `run_weekly.py --dry-run` | 仅采集与去重预览，不下载、不写库 |
| `run_weekly.py --regen --week 2026-W36` | 跳过采集，重新生成指定周周报 |
| `run_weekly.py --provider mock` | 临时切换为 Mock 模型 |
| `python -m newsagent report --week 2026-W36` | 以子命令形式重新生成指定周周报 |
| `check_env.py` | 环境自检 |
| `install_task.ps1 [-Time "09:00"] [-Uninstall]` | 注册 / 调整 / 移除定时任务 |
| `python -m pytest -q` | 运行 50 个单元与集成测试 |

## 五、配置要点

**`config.yaml`**：`llm.provider` 与 `llm.model`（DeepSeek 模型名为全小写，如 `deepseek-v4-flash-vision-exp`）、`collect.*`（并发数、超时、采集节流）、`classify.*`（打标并行数、厂商重要度下限）、`report.*`（概览字数、要点条数、输入条数上限）、`notify.*`（异常告警与周报推送开关）。

**`sources.yaml`**：支持三种源类型 —— `sohu_account`（填 `profile_url` 与 `xpt`）、`website`（填 `list_url` 与 `article_selector`）、`rss`（填 `url`）。新增源只需追加一条并设置 `enabled: true`。文件顶部记录了各站点的实测结论，说明哪些渠道可用、哪些受限及原因。

**`taxonomy.yaml`**：13 个一级标签（自动驾驶、车路协同/智能网联、智慧高速、智能公交/出租、信号与交管、政策法规、标准规范、产业动态/投融资、厂商动态、海外动态、事故与安全、展会会议、其他），二级标签写作 `一级/二级`，可按需增删。

**`.env`**（不纳入版本管理）：`LLM_API_KEY`；可选 `NOTIFY_WEBHOOK_URL`（群机器人告警）与 `DINGTALK_APP_KEY`、`DINGTALK_APP_SECRET`、`DINGTALK_AGENT_ID`、`DINGTALK_USERIDS`（钉钉周报推送）。

## 六、已知限制

- **厂商动态占比**：接入赛文交通网等渠道后已开始稳定产出，后续可继续补充集成商、招投标类新闻源
- **微信生态**：公众号正文需要 mp.weixin 直链才能存档，目前没有稳定的周期化渠道；搜狗微信搜索只能做标题层面的监测
- **钉钉推送**：代码已完成，等待企业内部应用审批发布后启用，并需适配新版开放平台接口
- **规划中**：内网 Web 检索站（可直接复用现有 SQLite 查询接口）、月报与趋势对比、人工审核与反馈入口

## 七、合规

仅采集公开新闻，保留来源与原文链接，供部门内部分析使用，不对外发布；采集过程设置请求节流与友好 User-Agent；单个源失效不影响其他源。
