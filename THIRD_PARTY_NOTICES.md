# Third-Party Notices

`ai-meter` source code is licensed under Apache-2.0. Some runtime dependencies and bundled probe dependencies are licensed separately by their authors.

## Python Dependencies

See `pyproject.toml` for the Python dependency list:

- Textual
- Rich
- Typer
- Pydantic
- platformdirs
- psutil

## Native Probe Dependencies

The Windows sensor probe uses NuGet packages resolved by `native/AiMeter.SensorProbe/AiMeter.SensorProbe.csproj`, including:

- `LibreHardwareMonitorLib` 0.9.4, MPL-2.0
- `HidSharp` 2.1.0, see package metadata for license terms
- Microsoft/.NET runtime libraries under their respective Microsoft licenses

When publishing release artifacts, include the generated dependency/license metadata from the build pipeline and keep third-party notices intact.
