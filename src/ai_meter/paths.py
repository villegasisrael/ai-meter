from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from platformdirs import user_config_dir, user_data_dir


@dataclass(frozen=True)
class AppPaths:
    config_dir: Path
    data_dir: Path
    config_file: Path
    db_file: Path
    export_dir: Path
    codex_home: Path
    claude_home: Path


def detect_codex_home() -> Path:
    import os

    env = os.environ.get("CODEX_HOME")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".codex"


def detect_claude_home() -> Path:
    return Path.home() / ".claude"


def resolve_app_paths(app_name: str = "ai-meter") -> AppPaths:
    cfg_dir = Path(user_config_dir(app_name, roaming=True))
    data_dir = Path(user_data_dir(app_name))
    cfg_dir.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)
    export_dir = data_dir / "exports"
    export_dir.mkdir(parents=True, exist_ok=True)

    return AppPaths(
        config_dir=cfg_dir,
        data_dir=data_dir,
        config_file=cfg_dir / "config.toml",
        db_file=data_dir / "ai-meter.db",
        export_dir=export_dir,
        codex_home=detect_codex_home(),
        claude_home=detect_claude_home(),
    )
