# Installer build

This directory holds everything needed to produce the
`AlienCore-<version>-Setup.exe` Windows installer.

## Prerequisites (one-time setup)

```
python -m pip install pyinstaller pillow
```

Plus **Inno Setup 6** from <https://jrsoftware.org/isdl.php> — the build
script auto-detects `ISCC.exe` in `Program Files (x86)\Inno Setup 6\` and
on `PATH`. No need to add it to PATH manually if you accept the default
install location.

## Building

From the project root:

```
python installer/build.py
```

Output:

```
dist/AlienCore/                                  ← PyInstaller onedir
dist/AlienCore-<version>-Setup.exe               ← final installer
```

A typical build takes 60–120 s depending on machine speed. Most of the
time is PyInstaller analysing imports and copying CPython runtime files.

## What each file does

| File | Purpose |
| --- | --- |
| `aliencore.spec` | PyInstaller config — entry point, hidden imports, bundled assets, icon |
| `version_info.txt` | Windows VERSIONINFO resource (File Properties → Details) |
| `aliencore.iss` | Inno Setup script — install layout, shortcuts, license, SmartScreen note |
| `build_icon.py` | Renders the tray's alien-head canvas to a multi-size `.ico` |
| `build.py` | Runs PyInstaller + Inno Setup in sequence |

## Bumping the version

Edit `core/constants.py::VERSION`. Then update **both**:

- `installer/version_info.txt` — `filevers`, `prodvers`, `FileVersion`,
  `ProductVersion` strings (all four)
- `installer/aliencore.iss` — `MyAppVersion` define near the top

`build.py` reads `VERSION` from `core/constants.py` and uses it to name
the output installer.

## Code signing (deferred for v1.0)

The installer ships unsigned. Once an Authenticode cert is acquired,
add a `SignTool=` directive to the Inno `[Setup]` section pointing at
`signtool.exe` with the cert thumbprint. PyInstaller's output `.exe`
should also be signed before Inno wraps it; integrate via a step in
`build.py` between PyInstaller and Inno Setup.

## Troubleshooting

**Hidden import errors at first launch (`ModuleNotFoundError`).** Run the
built `AlienCore.exe` from a terminal once:
```
.\dist\AlienCore\AlienCore.exe
```
PyInstaller's frozen builds default to no console; if a hidden import is
missing the app dies silently. Either add `console=True` temporarily in
`aliencore.spec` to surface the traceback, or add the missing module to
the `hiddenimports=[...]` list.

**`The system cannot find the file specified` from `lhm_bridge`.** The
.NET subprocess didn't make it into the install tree. Check that
`tools/lhm_bridge/dist/lhm_bridge.exe` exists in your dev tree before
building (rebuild it with `dotnet publish` from `tools/lhm_bridge/`),
and that the `[Files]` section of `aliencore.iss` references it.

**`Cryptography` import error in installed build.** PyInstaller's CFFI
hooks sometimes miss the OpenSSL shim. Add
`cryptography.hazmat.bindings.openssl.binding` to `hiddenimports` and
rebuild.
