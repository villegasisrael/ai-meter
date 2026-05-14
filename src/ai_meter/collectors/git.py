from __future__ import annotations

import subprocess
from pathlib import Path


def git_project_info(path: str) -> dict[str, str | None]:
    cwd = Path(path)
    if not cwd.exists():
        return {"repo": None, "branch": None}

    def run(args: list[str]) -> str | None:
        try:
            out = subprocess.run(
                args,
                cwd=str(cwd),
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
            )
            if out.returncode != 0:
                return None
            value = out.stdout.strip()
            return value or None
        except (subprocess.TimeoutExpired, OSError):
            return None

    return {
        "repo": run(["git", "rev-parse", "--show-toplevel"]),
        "branch": run(["git", "branch", "--show-current"]),
    }
