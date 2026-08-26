"""环境自检脚本：部署前/部署后在目标机器上运行。

检查项：
  1. Python 版本与依赖导入
  2. 配置加载（config/sources/taxonomy）与启用源清单
  3. data 目录可写
  4. LLM Provider 可用性（mock 直接通过；API 模式检查 Key 是否配置，--net 时真实连通）
  5. 各启用源可达性（HTTP 状态）
  6. 综述渲染冒烟（空数据渲染 HTML 是否正常）

退出码：0 全部通过；2 致命问题（配置/依赖缺失）；1 存在警告（源不可达/网络受限等）。
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import httpx  # noqa: E402
from loguru import logger  # noqa: E402

from newsagent.utils.config import Config  # noqa: E402
from newsagent.utils.dates import iso_week  # noqa: E402

DEPS = ["httpx", "feedparser", "bs4", "lxml", "trafilatura", "jinja2", "docx",
        "loguru", "yaml"]
fatal = 0
warn = 0


def ok(msg: str) -> None:
    print(f"  [OK]   {msg}")


def bad(msg: str, *, is_fatal: bool = False) -> None:
    global fatal, warn
    if is_fatal:
        fatal += 1
        print(f"  [致命] {msg}")
    else:
        warn += 1
        print(f"  [警告] {msg}")


def main() -> int:
    global fatal, warn
    print("===== newsagent 环境自检 =====")

    # 1) Python 与依赖
    print("[1] Python / 依赖")
    if sys.version_info < (3, 11):
        bad(f"Python {sys.version.split()[0]} < 3.11，请升级", is_fatal=True)
    else:
        ok(f"Python {sys.version.split()[0]}")
    for d in DEPS:
        try:
            mod = importlib.import_module(d)
            setattr(mod, "__version__", getattr(mod, "__version__", "ok"))
            ok(f"依赖 {d}")
        except Exception as exc:
            bad(f"依赖 {d} 导入失败: {exc}", is_fatal=True)

    if fatal:
        print(f"自检终止：{fatal} 个致命问题（请先修复依赖/环境）")
        return 2

    # 2) 配置
    print("[2] 配置")
    try:
        cfg = Config.load()
        ok(f"配置加载成功（provider={cfg.llm.get('provider')}，"
           f"启用源 {len(cfg.sources_enabled())} 个，标签 {len(cfg.taxonomy_paths())} 个）")
    except Exception as exc:
        bad(f"配置加载失败: {exc}", is_fatal=True)
        return 2

    # 3) data 目录可写
    print("[3] 数据目录")
    try:
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        probe = cfg.data_dir / ".write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        ok(f"data 目录可写：{cfg.data_dir}")
    except Exception as exc:
        bad(f"data 目录不可写: {exc}", is_fatal=True)

    # 4) LLM Provider
    print("[4] LLM Provider")
    provider = str(cfg.llm.get("provider", "mock"))
    if provider == "mock":
        ok("Mock 模式（未接真实模型，仅流水线验证）")
    elif provider == "ollama":
        ok("Ollama 本地模型（运行前请确认 ollama serve 已启动，端口 11434）")
    else:
        if cfg.llm_api_key:
            ok(f"API Key 已配置（{cfg.llm.get('api_key_env')}）")
            if "--net" in sys.argv:
                try:
                    newsagent_llm_check(cfg)
                except Exception as exc:
                    bad(f"API 连通性失败: {exc}")
        else:
            bad(f"未配置 API Key（请在 .env 设置 {cfg.llm.get('api_key_env')}）")

    # 5) 源可达性
    print("[5] 新闻源可达性（启用源 {} 个）".format(len(cfg.sources_enabled())))
    for s in cfg.sources_enabled():
        url = str(s.get("profile_url") or s.get("list_url") or s.get("url") or "")
        if not url:
            bad(f"[{s.get('id')}] 无 URL 配置")
            continue
        try:
            resp = httpx.get(url, timeout=10.0, follow_redirects=True,
                             headers={"User-Agent": cfg.collect.get("user_agent", "")})
            ok(f"[{s.get('id')}] {s.get('name')} HTTP {resp.status_code}")
        except Exception as exc:
            bad(f"[{s.get('id')}] {s.get('name')} 不可达: {exc}")

    # 6) 综述渲染冒烟
    print("[6] 渲染冒烟")
    try:
        from newsagent.report.generator import ReportData
        from newsagent.report.render import render_html
        html = render_html(ReportData(week=iso_week(), week_label="测试周",
                                      generated_at="now"))
        assert "智能交通新闻周报" in html
        ok("HTML 模板渲染正常")
    except Exception as exc:
        bad(f"渲染异常: {exc}", is_fatal=True)

    print("===== 自检结束：{} 致命 / {} 警告 =====".format(fatal, warn))
    return 0 if fatal == 0 and warn == 0 else 1


def newsagent_llm_check(cfg: Config) -> None:
    from newsagent.classify.llm import create_provider
    prov = create_provider(cfg)
    out = prov.chat([{"role": "user", "content": "回复：OK"}], json_mode=False,
                    temperature=0.0, model=cfg.tagger_model)
    ok(f"API 连通性 OK（响应 {len(out)} 字符）")


if __name__ == "__main__":
    sys.exit(main())
