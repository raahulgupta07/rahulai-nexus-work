# CityAgent Insights — Local Runtime Helper

Runs the agent's analysis Python on **your machine** (Cowork-style). The cloud
does the thinking; your laptop does the work. Credentials never leave the
server; local folders are analyzed in place and never uploaded.

## Install (v0)

```bash
python3 -m pip install requests pandas numpy pyarrow duckdb openpyxl
```

## Pair (once)

In the app: **Settings → Local Runtime → Generate pairing code**, then:

```bash
python3 helper.py pair 123456 --server http://localhost:8095
```

## Run

```bash
python3 helper.py run
# optionally expose local folders (read in place, never uploaded):
python3 helper.py run --allow-folder ~/Data/sales
```

Leave it running; the app's analyses now execute here. Stop it any time —
the app automatically falls back to cloud execution.

Config/token: `~/.cityagent-local-runtime.json` (owner-only file). On Windows
that resolves to `%USERPROFILE%\.cityagent-local-runtime.json`.

## Desktop apps (no terminal)

| file | platform | packaging |
| --- | --- | --- |
| `helper_app.py` | macOS menu bar (rumps) | `pyinstaller --windowed --name "CityAgent Helper" --hidden-import rumps --collect-submodules pyarrow helper_app.py` |
| `helper_app_win.py` | Windows system tray (pystray) | see [BUILD-WINDOWS.md](BUILD-WINDOWS.md) |

Both wrap the same execution core (`helper.py`), so behavior matches the CLI.
The built zips are staged into `frontend/public/downloads/` (gitignored) and
offered from Settings → Local Runtime.
