# 智能交通新闻智能体（newsagent）实施方案 v3（最终版）

> 本文档为项目实施方案全文（经对话评审后定稿），同时也是项目说明与使用手册。
> 状态：**已批准，实施中**。后续方案变更会同步更新本文件。

---

## 一、项目目标与成功标准

**目标**：部门内部（≤10人）实验项目，每周一 08:00 自动完成：①采集智能交通领域**国内**公开新闻并网页存档 → ②打标签、分类存储 → ③生成当周综述（**HTML + Word** 双格式）。

**成功标准**：
1. 一条命令跑通六个环节（采集→存档→分类→索引→综述→产物就位）；支持 `--dry-run/--limit/--week`。
2. 每周产物：原始 HTML 快照 + 正文 JSON + 标签/摘要/厂商元数据 + 综述（自包含单文件 HTML + Word）。
3. 跨周去重有效（URL 规范化 / 标题归一化 / 内容哈希三层）。
4. 失败环节有日志与汇总提示，流程不静默中断。
5. 人工抽查友好：可对指定周重新生成综述。

## 二、开发与验证策略

- **开发和验证全部在本机（开发电脑）进行**：虚拟机的远程管理通道（WinRM 5985）经多轮排查仍未打通（端口被京东云安全组拦截，RDP 之外均不通），放弃远程操作。
- **虚拟机交付物：自助部署手册 + 验证脚本**。用户日后自行在 VM 上执行：`scripts/check_env.py`（API/源/目录自检）、`scripts/install_task.ps1`（注册每周一 08:00 任务）、`run_weekly.py`；本文档第八章即《Windows Server 部署手册》，含《远程通道与安全组排查清单》。
- **LLM 实测需要 API Key**：本机真实跑打标签/综述时需配置（.env）；用户提供前，用 `MockLLMProvider` 完整验证流水线（解析、存储、渲染、去重全链路），保证机制正确后再插入真实 Key 做质量验证。
- 本机定时不为常态（开发期手动触发即可），定时脚本仍交付（面向 VM）。

## 三、技术栈

- Python 3.11+；`httpx` + `feedparser` + `beautifulsoup4/lxml`；正文提取 `trafilatura`
- 存储：文件系统（raw HTML / articles JSON / reports）+ SQLite（索引/去重/检索，查询接口为未来 Web 站点预留）
- LLM：统一抽象；`OpenAICompatProvider`（DeepSeek/智谱等国内 API，默认）+ `OllamaProvider`（本地备选）+ `MockLLMProvider`（测试）
- 报告：`Jinja2` 渲染自包含 HTML + `python-docx` 导出 Word
- 日志 `loguru`；调度 Windows 任务计划程序（交付 `install_task.ps1`）
- 配置：`config.yaml` / `sources.yaml` / `taxonomy.yaml` / `.env`（Key，不入库，gitignore）

## 四、目录结构

```
newsagent/
├── README.md                    # 方案全文 + 使用说明 + VM 自助部署手册
├── pyproject.toml / .env.example / .gitignore
├── config/
│   ├── config.yaml              # 路径、关键词、数量上限、LLM 选择、周编号、格式开关
│   ├── sources.yaml             # 全部国内源：sohu_account | rss | website | search
│   └── taxonomy.yaml            # 标签树（含“厂商动态/集成商”）
├── src/newsagent/
│   ├── pipeline.py              # 编排 + CLI（--dry-run/--limit/--week/--regen）
│   ├── collect/                 # rss.py / websites.py / sohu.py / search.py / dedup.py
│   ├── archive/                 # downloader.py / store.py（含 query(week/tag/keyword/company) 接口）
│   ├── classify/                # llm.py（抽象+OpenAICompat/Ollama/Mock）/ tagger.py
│   ├── report/                  # generator.py / render.py(HTML) / export.py(Word)
│   └── utils/                   # config.py / logging.py / notify.py
├── scripts/                     # check_env.py / run_weekly.py / install_task.ps1
├── tests/                       # 单测（mock LLM）
└── data/                        # gitignore 运行产物
```

## 五、数据流

1. **采集**：`sohu_account`（ITS114 搜狐号，指定核心源；探索 mp.sohu.com 号文章列表接口，受限则退化为官网 its114.com）；`rss`（国内行业媒体，实施调研可用源）；`website`（7its 赛文交通网、中国交通报/交通运输部等，配置化）；`search`（可选，国内引擎：百度新闻/搜狗，验证可行性）。去重：URL 规范化→标题归一化→SQLite 比对→内容哈希兜底。
2. **存档**：httpx 下载原 HTML（UA/超时/重试/节流）→ `raw/`；trafilatura 正文 → `articles/*.json`；失败标注"存档失败"继续。
3. **分类（LLM）**：结构化 JSON 输出 `relevant / tags（取自标签树）/ summary(100字) / keywords / companies（厂商集成商公司名）/ importance(1-3)`；few-shot；解析失败重试 1 次后降级"待人工"。
4. **索引**：SQLite `articles(id, guid, url, title, source, published_at, week, status, relevance, tags_json, summary, keywords_json, companies_json, importance, content_hash, created_at)`；store 提供查询接口（未来 Web 站直接复用）。
5. **综述**：当周条目（标题+摘要+重要度，截断限长）→ LLM 两步生成 → 自包含 HTML（内联 CSS、含目录/TOP5/厂商集成商动态专节）+ Word；输出 `reports/2026-W08/`。
6. **调度与告警**：install_task.ps1 注册周一 08:00（北京时区）；失败汇总+日志；可选企业微信/钉钉 webhook。

## 六、LLM 抽象与成本

`LLMProvider` 协议 + `OpenAICompatProvider`（base_url 可配 DeepSeek/智谱/通义）+ `OllamaProvider`（本地备选，无 GPU 不推荐）+ `MockLLMProvider`（无 Key 验证流水线）。config 一行切换；打标签与综述可分用不同模型。国内 API 成本预估每周 < 1 元。

## 七、标签体系（taxonomy.yaml，可增删）

一级：`自动驾驶`、`车路协同/智能网联`、`智慧高速`、`智能公交/出租`、`信号与交管`、`政策法规`、`标准规范`、`产业动态/投融资`、`厂商动态`（下设 `集成商动态`/`设备商动态`/`中标与采购`）、`海外动态`、`事故与安全`、`展会会议`、`其他`。采集侧重集成商密集源；厂商动态类提高保留优先级，保障占比。

## 八、部署与运行

### 本机（开发/验证）

1. `python -m venv .venv` → `.venv\Scripts\pip install -e .`（国内网络可用清华大学镜像 `-i https://pypi.tuna.tsinghua.edu.cn/simple`）
2. 复制 `.env.example` 为 `.env` 并填写 API Key（没有 Key 时默认走 Mock，可完整跑通流水线）
3. `python scripts/run_weekly.py --dry-run --limit 5` 试跑 → 全量跑 → 人工抽查标签/综述质量 → 调整 `config/taxonomy.yaml`
4. 查看产物：`data/reports/<周>/` 下 HTML（浏览器直接打开）与 Word

### Windows Server 虚拟机（用户自助）

1. 装 Python 3.11+（官网安装包，勾选 Add to PATH）与 git；`git clone` 私有仓库
2. `python -m venv .venv` → `.venv\Scripts\pip install -e .`
3. 复制 `.env.example` 为 `.env`，填 API Key
4. 运行 `python scripts\check_env.py` 自检（API 连通、源可达、目录可写）；**如 API/源不通，先检查京东云安全组是否放行所需出站（默认放行即可）与 DNS**
5. 试运行 `python scripts\run_weekly.py --dry-run --limit 5`，核验产物
6. 管理员 PowerShell 执行 `powershell -ExecutionPolicy Bypass -File scripts\install_task.ps1` 注册每周一 08:00 任务（需先确认 VM 时区为北京时间）
7. 查看：远程桌面打开 `data\reports\` 共享给部门成员

### 远程通道与安全组排查清单（当时未打通的记录）

- 现象：RDP(3389) 可通；SSH(22)、WinRM(5985/5986) 不通。
- 结论：Windows 防火墙规则已放行但**云安全组（京东云）入方向未放行**，属两层防护：云安全组 → Windows 防火墙。
- 若需远程管理：①京东云控制台安全组添加入方向 TCP 5985（或 22）；②VM 内 `Enable-PSRemoting -Force -SkipNetworkProfileCheck` 且防火墙 RemoteAddress=Any；③本机管理员设置 TrustedHosts。
- 部署完成后建议：安全组仅放行 3389/22，5985 关闭或限定来源 IP。

## 九、测试与验收

- 单测（MockLLM）：去重、JSON 解析与降级、公司抽取、HTML/Word 渲染结构、配置校验、周编号。
- 集成：dry-run 端到端；重复运行零重复；逐环节日志可追踪。
- 人工抽查：首批产物核验标签准确率与厂商内容占比。

## 十、边界与失败处理

单源失败继续；反爬节流+UA+失败静默；LLM 超时退避重试；正文提取失败保留快照标注；跨周重复三层兜底；合规：仅存公开新闻、注明来源、部门内部使用。

## 十一、实施阶段

- **阶段 1（MVP）**：骨架+配置 → ITS114 搜狐号（或官网）采集 → 存档 → LLM 打标签（Mock 先行）→ SQLite → 综述 HTML；dry-run 可验收。
- **阶段 2（完善）**：7its 等站点 + 国内搜索补充 + Word 导出 + 告警 + install_task.ps1 + 部署手册 + 真实 API Key 质量验证（用户提供 Key 后）。
- **阶段 3（按需）**：Web 检索站（FastAPI + 现有 SQLite 查询接口）、月报/趋势、Ollama 实测、用户补充重点来源。

## 十二、明确假设

1. 重点源清单后续由用户补充；默认源：ITS114 搜狐号（核心）、7its 赛文交通网、中国交通报/交通运输部类，全部国内源，实施时逐一验证可达性。
2. VM 无 GPU → 默认国内 LLM API；Ollama 备选；本机验证优先用 Mock，真实 LLM 验证待用户提供 Key。
3. 本机可正常访问国内网络与所需站点（Cloudflare/微信系来源若受限，实施时记录并换源）。
4. 交付形态为文件报告（HTML+Word）；数据层已为未来 Web 检索预留；VM 部署与调度由用户按手册自助完成。

---

## 附录：常用命令

```powershell
# 采集链路预览（只采集+去重，不下载、不落库）
python scripts\run_weekly.py --dry-run
# 小规模真实验证（最多 5 条，会真实写入 data/，可人工核验标签质量）
python scripts\run_weekly.py --limit 5
# 完整跑本周
python scripts\run_weekly.py
# 重新生成某周综述（不重新采集）
python scripts\run_weekly.py --regen --week 2026-W09
# 环境自检
python scripts\check_env.py
```
