# -*- mode: python ; coding: utf-8 -*-

import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules


project_root = Path(SPECPATH).resolve()
version = os.environ.get("DEVSTT_VERSION", "0.0.0")
build_name = os.environ.get("DEVSTT_BUILD_NAME", f"DevSTT_{version}")
exe_name = f"DevSTT_{version}"


def collect_optional(collector, package_name):
    try:
        return collector(package_name)
    except Exception:
        return []


hiddenimports = []
for package_name in (
    "faster_whisper",
    "ctranslate2",
    "nvidia",
    "nvidia.cublas",
    "nvidia.cuda_runtime",
    "nvidia.cuda_nvrtc",
    "nvidia.cudnn",
):
    hiddenimports += collect_optional(collect_submodules, package_name)

binaries = []
for package_name in (
    "ctranslate2",
    "onnxruntime",
    "nvidia",
    "nvidia.cublas",
    "nvidia.cuda_runtime",
    "nvidia.cuda_nvrtc",
    "nvidia.cudnn",
):
    binaries += collect_optional(collect_dynamic_libs, package_name)

datas = [(str(project_root / "assets" / "warmup.mp3"), "assets")]
for package_name in ("faster_whisper", "ctranslate2"):
    datas += collect_optional(collect_data_files, package_name)

a = Analysis(
    [str(project_root / "main.py")],
    pathex=[str(project_root)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=exe_name,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name=build_name,
)
