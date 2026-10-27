#!/bin/bash
# chudQ's own PyInstaller bootloader (the small program at the front of chudQ.exe that starts Python).
# PyInstaller's prebuilt one is shared by countless programs, malware too, so antivirus guesses
# (Defender's "Trojan:Win32/Ulthar.A!ml") flag apps built with it; one compiled here from PyInstaller's
# own source does the same and isn't on those lists. Builds on Linux/WSL with Zig (pip, no install
# rights), for 64-bit Windows, into DEST; build.py uses it when its version matches PyInstaller's.
#
#   installer/bootloader.sh 6.22.3 /mnt/c/Users/<you>/AppData/Local/ChudQ-build/bootloader
set -euo pipefail
VERSION=${1:?PyInstaller version, as in the build venv (pip show pyinstaller)}
DEST=${2:?where build.py looks: %LOCALAPPDATA%\\ChudQ-build\\bootloader}
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
python3 -m venv "$TMP/venv"
"$TMP/venv/bin/pip" -q install ziglang
url=$(curl -s "https://pypi.org/pypi/pyinstaller/$VERSION/json" |
      python3 -c "import json,sys; print([u['url'] for u in json.load(sys.stdin)['urls'] if u['packagetype']=='sdist'][0])")
curl -sL "$url" | tar -xz -C "$TMP"
# zig cc as a 64-bit Windows C compiler, minus the GNU linker options zig's linker doesn't take
cat > "$TMP/cc" <<CC
#!/bin/bash
args=()
for a in "\$@"; do
  case "\$a" in -Wl,--enable-auto-import|-Wl,-Bstatic|-Wl,-Bdynamic) ;; *) args+=("\$a");; esac
done
exec "$TMP/venv/bin/python" -m ziglang cc -target x86_64-windows-gnu -Wno-macro-redefined "\${args[@]}"
CC
printf '#!/bin/sh\nexec "%s" -m ziglang ar "$@"\n' "$TMP/venv/bin/python" > "$TMP/ar"
chmod +x "$TMP/cc" "$TMP/ar"
cd "$TMP/pyinstaller-$VERSION/bootloader"
CC="$TMP/cc" AR="$TMP/ar" python3 ./waf configure all --clang --target-arch=64bit
mkdir -p "$DEST"
cp ../PyInstaller/bootloader/Windows-64bit-intel/*.exe "$DEST/"
echo "$VERSION" > "$DEST/VERSION"
ls -la "$DEST"
