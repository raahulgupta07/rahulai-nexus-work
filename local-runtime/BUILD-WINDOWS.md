# Building the Windows helper (`CityAgentHelper-win.zip`)

The Windows tray app (`helper_app_win.py`) can only be packaged **on a Windows
machine** — PyInstaller does not cross-compile. Everything else (code, settings
page, download card) is already in the repo; this file is the whole build.

Result: `frontend/public/downloads/CityAgentHelper-win.zip`, offered by
**Settings → Local Runtime → Windows**.

---

## 1. Prerequisites (Windows 10/11, 64-bit)

- **Python 3.12 (64-bit)** from python.org — tick *"Add python.exe to PATH"*.
  (3.11 also works. Avoid the Microsoft Store build: its sandboxed file paths
  confuse PyInstaller.)
- A copy of this `local-runtime/` folder. `helper.py` **must** sit next to
  `helper_app_win.py` — it is imported as a local module and PyInstaller pulls
  it in automatically.

```powershell
cd path\to\bagofwords\local-runtime
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install pystray pillow requests pandas numpy pyarrow duckdb pyinstaller
```

`openpyxl` is optional — add it if you want `.xlsx` files inside allowed
folders to be queryable.

## 2. Build (one command)

```powershell
pyinstaller --windowed --name "CityAgent Helper" --hidden-import pystray --collect-submodules pyarrow helper_app_win.py
```

Output: `dist\CityAgent Helper\CityAgent Helper.exe` (plus its DLL/lib folder).
`--windowed` means no console window. Expect ~200–300 MB unpacked, a 2–4 minute
build.

Optional flags:

| flag | why |
| --- | --- |
| `--noconfirm` | overwrite a previous `dist\` without prompting (re-builds) |
| `--icon app.ico` | replace the generic PyInstaller exe icon (the *tray* icon is drawn in code and is unaffected) |
| `--collect-submodules duckdb` | only if a built exe raises `ModuleNotFoundError: duckdb...` |
| `--hidden-import pystray._win32` | only if the tray icon never appears (backend not detected) |

Do **not** use `--onefile`: it unpacks to `%TEMP%` on every launch, which
triples start-up time and trips some corporate antivirus.

## 3. Smoke-test before zipping

1. Run `dist\CityAgent Helper\CityAgent Helper.exe`.
2. A grey circle appears in the tray (use the tray overflow arrow — Windows
   hides new icons by default; drag it onto the taskbar to pin).
3. Right-click → **Pair…** → enter the server URL, then the 6-digit code from
   Settings → Local Runtime. Icon turns **green**.
4. Ask the agent a data question in the app — the icon flashes **blue** while
   the job runs, and the run should report that it executed on your device.
5. Check **Allow folder…**, **Pause/Resume**, **Unpair**, **Quit**.

Config lands in `%USERPROFILE%\.cityagent-local-runtime.json` (delete it to
reset to a fresh, unpaired state).

## 4. Zip and stage

```powershell
Compress-Archive -Path "dist\CityAgent Helper" -DestinationPath "CityAgentHelper-win.zip" -Force
```

Copy `CityAgentHelper-win.zip` into the repo at:

```
frontend/public/downloads/CityAgentHelper-win.zip
```

Notes:

- `frontend/public/downloads/` is **gitignored** (`.gitignore`, same rule that
  covers `CityAgentHelper-mac.zip`) — the zip is a build artifact, never
  committed. Keep a copy wherever release artifacts live.
- The zip is only served after a **frontend image rebuild** (`nuxt generate`
  copies `frontend/public/` into `/app/frontend/dist`, which FastAPI serves).
  Docker caches the `COPY ./frontend` layer silently — bust it:
  `docker compose -p cityagentinsights -f docker-compose.dev.yaml build app --build-arg FE_CACHEBUST=$(date +%s)`,
  then verify the file exists inside the container at
  `/app/frontend/dist/downloads/CityAgentHelper-win.zip`.
- Until the zip is staged the URL does **not** 404 — the SPA catch-all answers
  `200 text/html` with `index.html`, so `res.ok` proves nothing and a plain
  `HEAD` returns `405` (the catch-all is GET-only). The settings page therefore
  probes with a 1-byte ranged `GET` and checks the *content type*; on
  `text/html` it shows the Windows button disabled as *Coming soon*. Shipping
  without the zip is safe, but any new check must use the same test — see
  `checkDownload()` in `frontend/pages/settings/local-runtime.vue`.
- Each helper zip is ~70–90 MB and lands in the image. If image size becomes a
  problem, serve both zips from object storage and point the buttons there.

## 5. SmartScreen (unsigned build)

The exe is unsigned, so the first launch on a clean machine shows
**"Windows protected your PC"**. Users click **More info → Run anyway**.
Some managed fleets block unsigned executables outright — in that case the
CLI helper (`python helper.py run`) still works, and a signed build is
required.

## 6. TODO — code signing

To remove the SmartScreen prompt:

1. Buy an **OV or EV code-signing certificate** (DigiCert / Sectigo / SSL.com).
   EV clears SmartScreen reputation immediately; OV builds reputation over time
   and downloads.
2. Since June 2023 the private key must live on an HSM/token or a cloud signing
   service (Azure Trusted Signing, DigiCert KeyLocker) — you cannot just hold a
   `.pfx` on the build box.
3. Sign after PyInstaller, before zipping:
   ```powershell
   signtool sign /tr http://timestamp.digicert.com /td sha256 /fd sha256 /a "dist\CityAgent Helper\CityAgent Helper.exe"
   ```
   Sign every bundled `.exe`/`.dll` PyInstaller emits if the fleet enforces it.
4. Verify: `signtool verify /pa "dist\CityAgent Helper\CityAgent Helper.exe"`.

The macOS zip has the same gap (unsigned → right-click → Open) and needs an
Apple Developer ID + notarization. Track both together.

## 7. Troubleshooting

| symptom | fix |
| --- | --- |
| exe starts, no tray icon | add `--hidden-import pystray._win32`; also check the tray overflow area |
| `ModuleNotFoundError: pyarrow.lib` | keep `--collect-submodules pyarrow`; rebuild in a clean venv |
| `ModuleNotFoundError: duckdb` | add `--collect-submodules duckdb` (only needed for allowed folders) |
| pairing fails with a TLS error | the app must trust the server certificate; for self-signed certs install the CA into the Windows certificate store |
| antivirus quarantines the exe | expected for unsigned PyInstaller output — sign it (§6) or allowlist the path |
| tray icon stuck grey | the helper cannot reach the server: check the URL in `%USERPROFILE%\.cityagent-local-runtime.json`, re-pair via **Pair…** |
| dialogs never appear / app hangs on **Pair…** | `HelperTrayApp.run()` keeps Tk on the main thread and the tray on a worker thread (Win32 message loops are per-thread). If a pystray release rejects that, swap them: call `self.icon.run()` on the main thread and run the Tk root's `mainloop()` in the worker instead — the `_dialog()` marshalling works either way |
