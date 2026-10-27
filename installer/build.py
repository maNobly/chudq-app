"""Build chudQ's installer, on Windows, with the League Recorder's build venv (it has everything):

    %LOCALAPPDATA%\\LeagueRecorder-build\\venv\\Scripts\\python.exe build.py
        -> %LOCALAPPDATA%\\ChudQ-build\\dist\\chudQ\\ (the app) and ChudQ-Setup-<version>.exe

Needs, in the venv: pywebview, pythonnet, pystray, Pillow, PyInstaller; and Inno Setup 6 (winget
install JRSoftware.InnoSetup). Everything happens in %LOCALAPPDATA%\\ChudQ-build: the sources are
copied there first, since PyInstaller is slow and unreliable on a \\\\wsl.localhost path.

The app starts with chudQ's own PyInstaller bootloader when %LOCALAPPDATA%\\ChudQ-build\\bootloader has
one for this PyInstaller version (bootloader.sh builds it): antivirus guesses flag PyInstaller's
prebuilt one. It goes into the venv's PyInstaller for this build only; the originals are put back after.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

HERE = Path(__file__).resolve().parent
PACKAGE = HERE.parent / "chudq"
WORK = Path(os.environ["LOCALAPPDATA"]) / "ChudQ-build"
SRC = WORK / "src"
ISCC = [Path(os.environ["LOCALAPPDATA"]) / "Programs" / "Inno Setup 6" / "ISCC.exe",
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Inno Setup 6" / "ISCC.exe"]


@contextmanager
def own_bootloader():
    import PyInstaller
    target = Path(PyInstaller.__file__).parent / "bootloader" / "Windows-64bit-intel"
    ours = WORK / "bootloader"
    stamp = ours / "VERSION"
    if not stamp.exists() or stamp.read_text().strip() != PyInstaller.__version__:
        print(f"note: PyInstaller's prebuilt bootloader (no own one for {PyInstaller.__version__}: installer/bootloader.sh)")
        yield
        return
    keep = WORK / "bootloader-original"
    if not keep.exists():
        shutil.copytree(target, keep)
    try:
        for f in ours.glob("*.exe"):
            shutil.copy2(f, target / f.name)
        print("own bootloader", PyInstaller.__version__)
        yield
    finally:
        for f in keep.glob("*.exe"):
            shutil.copy2(f, target / f.name)


def copy_sources() -> None:
    shutil.rmtree(SRC, ignore_errors=True)
    shutil.copytree(PACKAGE, SRC / "chudq", ignore=shutil.ignore_patterns("__pycache__"))
    (SRC / "launch.py").write_text("import sys\nfrom chudq.app import main\nsys.exit(main())\n")


def main() -> int:
    iscc = next((p for p in ISCC if p.exists()), None)
    if not iscc:
        sys.exit("Inno Setup 6 isn't installed: winget install JRSoftware.InnoSetup")
    WORK.mkdir(parents=True, exist_ok=True)
    copy_sources()
    sys.path.insert(0, str(SRC))
    from chudq import __version__, icon
    ico = WORK / "chudQ.ico"
    ico.write_bytes(icon.ico())
    sep = os.pathsep
    with own_bootloader():
        subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed", "--onedir",
                        "--name", "chudQ", "--icon", str(ico),
                        "--distpath", str(WORK / "dist"), "--workpath", str(WORK / "pyi"), "--specpath", str(WORK),
                        "--paths", str(SRC),
                        "--add-data", f"{SRC / 'chudq' / 'page'}{sep}page",
                        "--hidden-import", "pystray._win32",
                        "--collect-submodules", "chudq",
                        str(SRC / "launch.py")], check=True, cwd=WORK)
    subprocess.run([str(iscc), "/Q", f"/DVersion={__version__}", f"/DDist={WORK / 'dist' / 'chudQ'}",
                    f"/DIcon={ico}", f"/O{WORK}", str(HERE / "ChudQ.iss")], check=True)
    print(WORK / f"ChudQ-Setup-{__version__}.exe")
    return 0


if __name__ == "__main__":
    sys.exit(main())
