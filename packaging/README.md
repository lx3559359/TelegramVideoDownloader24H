# Windows installer

Version 0.3.6, installer revision 1, Windows 10/11 x64. Built from released 0.3.6 plus frozen-launch, supervisor and installer-update adaptations in this task. Unfinished concurrent sponsorship work is not part of this installer.

Build from the repository root with Windows Python 3.12 x64:

```powershell
python -m venv .tmp/exe-build-venv
.tmp/exe-build-venv/Scripts/python.exe -m pip install -r packaging/build-requirements.txt
.tmp/exe-build-venv/Scripts/python.exe -m pip install --no-deps .
.tmp/exe-build-venv/Scripts/python.exe -m PyInstaller --noconfirm --distpath .tmp/windows-dist --workpath .tmp/windows-build packaging/TelegramVideoDownloader.spec
.tmp/inno-compiler/ISCC.exe /Qp packaging/windows.iss
```

Compiler: Inno Setup 6.7.3, downloaded from the official jrsoftware release link; Authenticode checked valid, Pyrsys B.V. ChineseSimplified.isl is the official project's translation. Python license is copied verbatim from the build interpreter; dependency licenses and metadata are bundled.

The payload allowlist is generated EXE/_internal, supervisor script, instructions and licenses only. Never add the repository root, .runtime, config.toml, sessions, downloads, VPS information or credentials to [Files]. Do not run smoke tests directly in the distributable payload: install into .tmp/installer-smoke first.

Smoke test: `python scripts/smoke-windows-installer.py` after silent installation into .tmp/installer-smoke. This uses a restricted PATH and the default EXE GUI entry; checks bundled dependencies, native UI, installer running guard and real supervisor/service startup/stop. No user account is copied or used. Unconfigured account/Telegram doctor failures are expected. Live Telegram downloads and a clean Windows VM were not tested.

The installer runs per-user, creates shortcuts, blocks upgrade/uninstall while the EXE or packaged supervisor is active, and leaves user-created files on uninstall. No code-signing certificate is configured; do not promise SmartScreen suppression.

Publication: `python scripts/publish-windows-installer.py --publish`, only after website build and installer verification. Uses saved VPS credentials without printing them, existing SSH host-key trust, independent staged Go tests, upload hash verification, configuration race detection and rollback on failed public health checks.
