"""配置加载：config.yaml / sources.yaml / taxonomy.yaml / .env。

约定：仓库根目录由本文件位置推导（src/newsagent/utils/config.py → 上溯 3 层），
数据目录、输出路径均相对仓库根目录解析。
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml


class ConfigError(Exception):
    """配置缺失或非法。"""


class D(dict):
    """字典的属性访问包装：cfg.app.data_dir 等价于 cfg.app['data_dir']。"""

    __getattr__ = dict.__getitem__  # type: ignore[assignment]

    def __setattr__(self, key, value):
        self[key] = value


def find_root() -> Path:
    """仓库根目录 = 本文件上溯 3 层（src/newsagent/utils/config.py）。"""
    return Path(__file__).resolve().parents[3]


def load_env(root: Path | None = None) -> dict[str, str]:
    """加载 .env（若存在）到 os.environ（不覆盖已有环境变量），返回 key 列表。"""
    root = root or find_root()
    env_file = root / ".env"
    if not env_file.exists():
        return {}
    loaded: dict[str, str] = {}
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value
        loaded[key] = os.environ.get(key, "")
    return loaded


def _read_yaml(path: Path) -> dict:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as exc:  # 让缺失/损坏配置立即暴露
        raise ConfigError(f"无法解析配置文件 {path}: {exc}") from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ConfigError(f"配置文件应为映射结构: {path}")
    return data


class Config:
    """项目配置聚合入口。

    用法：
        cfg = Config.load()
        cfg.llm.provider        # 'mock' | 'openai-compat' | 'ollama'
        cfg.sources             # sources.yaml 的 sources 列表
        cfg.taxonomy            # taxonomy.yaml 的 taxonomy 列表
        cfg.data_dir            # 运行产物根目录（Path）
    """

    def __init__(self, root: Path, app: dict, llm: dict, collect: dict,
                 classify: dict, report: dict, notify: dict,
                 sources: list, taxonomy: list):
        self.root = root
        self.app = D(app)
        self.llm = D(llm)
        self.collect = D(collect)
        self.classify = D(classify)
        self.report = D(report)
        self.notify = D(notify)
        self.sources = sources
        self.taxonomy = taxonomy
        self.data_dir = (root / str(app.get("data_dir", "data"))).resolve()

    @classmethod
    def load(cls, root: Path | None = None) -> "Config":
        root = root or find_root()
        load_env(root)
        config_dir = root / "config"

        main = _read_yaml(config_dir / "config.yaml")
        sources_doc = _read_yaml(config_dir / "sources.yaml")
        taxonomy_doc = _read_yaml(config_dir / "taxonomy.yaml")

        llm = D(main.get("llm", {}))
        # 校验 provider 取值
        provider = str(llm.get("provider", "mock")).lower()
        if provider not in ("openai-compat", "ollama", "mock"):
            raise ConfigError(f"llm.provider 取值非法: {provider}（应为 openai-compat|ollama|mock）")
        llm["provider"] = provider

        cfg = cls(
            root=root,
            app=main.get("app", {}),
            llm=llm,
            collect=main.get("collect", {}),
            classify=main.get("classify", {}),
            report=main.get("report", {}),
            notify=main.get("notify", {}),
            sources=list(sources_doc.get("sources", [])),
            taxonomy=list(taxonomy_doc.get("taxonomy", [])),
        )

        return cfg

    # ---- 常用派生值 ----
    @property
    def llm_api_key(self) -> str | None:
        env_name = self.llm.get("api_key_env", "LLM_API_KEY")
        return os.environ.get(env_name) or None

    @property
    def tagger_model(self) -> str:
        return self.llm.get("tagger_model") or self.llm.get("model", "")

    @property
    def report_model(self) -> str:
        return self.llm.get("report_model") or self.llm.get("model", "")

    def taxonomy_paths(self) -> list[str]:
        """全部合法标签路径，如 ['厂商动态/集成商动态', '政策法规']。"""
        paths: list[str] = []
        for node in self.taxonomy:
            name = str(node.get("name", "")).strip()
            children = node.get("children") or []
            if name:
                if children:
                    paths.extend(f"{name}/{str(c).strip()}" for c in children)
                else:
                    paths.append(name)
        return paths

    def sources_enabled(self) -> list[dict]:
        return [s for s in self.sources if s.get("enabled")]
