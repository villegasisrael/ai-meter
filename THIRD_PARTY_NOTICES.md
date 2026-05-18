# Third-Party Notices

`ai-meter` source code is licensed under Apache-2.0.

Runtime dependencies are listed in `pyproject.toml`:

- Textual
- Rich
- Typer
- Pydantic
- platformdirs
- psutil

Default release artifacts should bundle only the supported user-mode Windows probe:

- `ai-meter-winprobe.exe`

The legacy LibreHardwareMonitor/WinRing0 sensor probe is disabled and must not be included in normal release artifacts.
