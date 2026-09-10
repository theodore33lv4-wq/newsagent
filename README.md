# newsagent · 智能交通新闻智能体

部门内部（≤10 人）新闻自动化工具：每周一自动采集国内智能交通公开新闻 → 网页存档 → AI 打标 → 生成周报（HTML + Word）。

> 实验性项目 · 仅内部使用 · 只存档公开新闻并保留来源与原文链接。

## 一、功能

| 功能 | 说明 |
|---|---|
| 多源采集 | 7 个国内源：搜狐号（ITS114、智能交通技术）、交通运输部、工信部、中国交通新闻网、赛文交通网、盖世汽车 RSS；源清单可配置增删 |
| 网页存档 | 原文 HTML 快照 + 正文提取（trafilatura，失败降级 bs4），提取失败仍保留快照并标注 |
| AI 打标 | 相关性过滤、标签（限标签树内，防幻觉）、摘要、关键词、**厂商/集成商公司名**、重要度 1-3（厂商动态有下限） |
| 每周综述 | 主题复用打标一级标签（确定性、跨周可比）；LLM 生成逐条要点（30-80 字）、概览+要点、TOP5、趋势三块、下周关注 |
| 双格式周报 | 自包含单文件 HTML（含类别分布条形图、原文链接可直接点开）+ Word |
| 跨周去重 | URL 规范化 → 标题归一化 → 内容哈希，同篇不重复入档 |
| 可选推送 | 钉钉工作通知（HTML 文件发给个人）；失败告警 webhook（企业微信/钉钉/飞书） |
| 运维友好 | 单源失败不影响整体、日志滚动留存、`check_env.py` 自检、流水线摘要一目了然 |

## 二、工作原理

```
定时（Windows 任务计划，每周一 08:30）或手动
        ↓
① 采集  多源拉取候选 → 与历史比对去重
        ↓
② 存档  并发下载 HTML 快照 → 提取正文 → 落盘 + SQLite 索引
        ↓
③ 分类  LLM 逐条打标（并发，失败重试/降级），无关内容自动出局
        ↓
④ 综述  主题=打标一级标签 → LLM 写要点 → LLM 写概览/TOP5/趋势/下周关注
        ↓
⑤ 产物  data/reports/<周>/weekly-<周>.{html,docx,json}
```

**周口径**：目标周 = `今天-7天` 所在的 ISO 周。周一运行生成**上一完整周**周报，一周内任意一天补跑都落在同一周（跨周可比、可重跑）。

**LLM 抽象**：`config.yaml` 一行切换 `openai-compat`（DeepSeek 等国内 API，默认）/ `ollama`（本地）/ `mock`（无 Key 验证流水线）；打标与综述可分用不同模型。任何一步 LLM 不可用都会降级，周报仍能产出。

## 三、目录结构

```
newsagent/
├── config/
│   ├── config.yaml       # 主配置：LLM / 采集 / 分类 / 综述 / 通知
│   ├── sources.yaml      # 新闻源清单（新增源改这里，多数零代码）
│   └── taxonomy.yaml     # 标签树（LLM 只能从树中选）
├── src/newsagent/
│   ├── pipeline.py       # 流水线编排（采集→存档→分类→综述→推送）
│   ├── cli.py / __main__.py   # 命令行入口（python -m newsagent）
│   ├── collect/          # base(Article/HTTP) · sohu · websites · rss · search · dedup
│   ├── archive/          # downloader(快照+正文) · store(文件 + SQLite 检索)
│   ├── classify/         # llm(OpenAICompat/Ollama/Mock) · tagger(打标)
│   ├── report/           # generator(综述) · render(Jinja2→HTML) · export(→Word) · templates/
│   └── utils/            # config · logging · dates(周编号) · notify · dingtalk
├── scripts/
│   ├── run_weekly.py     # 每周运行入口（任务计划调它）
│   ├── check_env.py      # 环境自检（依赖/配置/目录/LLM/源可达/渲染）
│   └── install_task.ps1  # 注册每周一 08:30 定时任务（-Uninstall 移除）
├── tests/                # 50 个单元与集成测试（Mock LLM，不耗额度、不联网）
├── data/                 # 运行产物（gitignore）
│   ├── raw/<周>/         # 原始 HTML 快照
│   ├── articles/<周>/    # 正文 + 元数据 JSON
│   ├── reports/<周>/     # weekly-<周>.html / .docx / .json
│   ├── index.sqlite3     # 检索索引（周/标签/关键词/公司/重要度）
│   └── logs/             # 滚动日志
└── pyproject.toml / .env.example / README.md
```

## 四、部署

### 本机（开发/验证）

```powershell
python -m venv .venv
.venv\Scripts\pip install -e .                              # 网络慢加 -i https://pypi.tuna.tsinghua.edu.cn/simple
Copy-Item .env.example .env                                 # 填 LLM_API_KEY（DeepSeek 平台申请）
.venv\Scripts\python scripts\check_env.py                   # 自检：依赖/配置/目录/LLM/各源可达
.venv\Scripts\python scripts\run_weekly.py --limit 5        # 小规模真实验证（写入 data/）
.venv\Scripts\python scripts\run_weekly.py                  # 完整跑（上一完整周）
```

### Windows Server（定时自动）

1. 装 Python 3.11+（勾选 Add to PATH）与 git，`git clone` 本仓库
2. `python -m venv .venv` → `.venv\Scripts\pip install -e .`
3. 复制 `.env.example` 为 `.env`，填 `LLM_API_KEY`
4. `python scripts\check_env.py` 自检 → `run_weekly.py --limit 5` 核验
5. 确认时区为北京时间（`tzutil /s "China Standard Time"`），管理员 PowerShell 执行：
   `powershell -ExecutionPolicy Bypass -File scripts\install_task.ps1`
6. `data\reports\` 共享给部门成员；代码更新只需 `git pull`（任务无需重注册；新增依赖时补跑一次 `pip install -e .`）

### 常用命令

| 命令 | 说明 |
|---|---|
| `run_weekly.py` | 完整跑上一完整周 |
| `run_weekly.py --limit 5` | 只处理前 5 条新条目（小规模验证） |
| `run_weekly.py --dry-run` | 仅采集+去重预览，不下载不落库 |
| `run_weekly.py --regen --week 2026-W36` | 跳过采集，重新生成指定周周报 |
| `run_weekly.py --provider mock` | 临时切 Mock 模型 |
| `python -m newsagent report --week 2026-W36` | 同上（子命令形式） |
| `check_env.py` / `install_task.ps1 [-Uninstall]` | 环境自检 / 注册或移除定时任务 |

## 五、配置要点

**`config.yaml`**：`llm.provider/model`（DeepSeek 模型名为全小写，如 `deepseek-v4-flash-vision-exp`）；`collect.*`（并发、超时、节流）；`classify.*`（并行数、厂商重要度下限）；`report.*`（概览字数、要点条数、输入条数上限）；`notify.*`（失败告警、周报推送）。

**`sources.yaml`**：三种源类型 —— `sohu_account`（`profile_url`+`xpt`）/ `website`（`list_url`+`article_selector`）/ `rss`（`url`）；新增源只需加一条并置 `enabled: true`。文件顶部记录了各站点实测结论（哪些不可用、为什么）。

**`taxonomy.yaml`**：13 个一级标签（自动驾驶、车路协同/智能网联、智慧高速、智能公交/出租、信号与交管、政策法规、标准规范、产业动态/投融资、厂商动态、海外动态、事故与安全、展会会议、其他），二级格式 `一级/二级`。

**`.env`**（不入库）：`LLM_API_KEY`；可选 `NOTIFY_WEBHOOK_URL`、`DINGTALK_APP_KEY/SECRET/AGENT_ID/USERIDS`（钉钉推送，需企业内部应用审批后可用）。

## 六、测试

```powershell
.venv\Scripts\python -m pytest -q      # 50 个用例：配置/日期/去重/存储/提取/LLM/打标/综述/流水线/通知
```

全部基于 Mock LLM 与临时目录，不消耗 API 额度、不访问网络。

## 七、已知限制

- **厂商动态占比**：已接入赛文交通网等源后开始产出，仍可补充"集成商/中标"类源
- **微信生态**：公众号正文需 mp.weixin 直链（架构已兼容），暂无周期性渠道；搜狗微信搜索仅能做标题监测
- **钉钉推送**：代码已就绪，需企业内部应用审批发布后启用（新版平台接口适配留待联调）
- **规划中**：内网 Web 检索站（复用现有 SQLite 查询接口）、月报/趋势对比、人工审核反馈

## 八、合规

仅采集公开新闻，保留来源与原文链接，部门内部分析使用，不对外发布；采集设节流与友好 UA；单源失效不影响其他源。
