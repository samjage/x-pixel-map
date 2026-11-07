#!/bin/sh
# Install X Pixel Map for the current user: an entry in the applications list and an icon.
# Nothing outside ~/.local is touched and no root is needed.  Remove it with:  packaging/linux/install.sh --uninstall
set -eu
here=$(cd "$(dirname "$0")" && pwd)
root=$(cd "$here/../.." && pwd)
apps=${XDG_DATA_HOME:-$HOME/.local/share}/applications
icons=${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/scalable/apps

if [ "${1:-}" = "--uninstall" ]; then
  rm -f "$apps/x-pixel-map.desktop" "$icons/x-pixel-map.svg"
  update-desktop-database "$apps" 2>/dev/null || true
  echo "Removed the launcher and icon. The program folder ($root) was left alone."
  exit 0
fi

if [ ! -x "$root/.venv/bin/python" ]; then
  echo "Creating the Python environment in $root/.venv ..."
  if command -v uv >/dev/null 2>&1; then
    uv venv --python /usr/bin/python3 --system-site-packages "$root/.venv"
    uv pip install --python "$root/.venv/bin/python" numpy pywebview unicorn
  else
    /usr/bin/python3 -m venv --system-site-packages "$root/.venv"
    "$root/.venv/bin/python" -m pip install numpy pywebview unicorn
  fi
fi

mkdir -p "$apps" "$icons"
cp "$here/x-pixel-map.svg" "$icons/x-pixel-map.svg"
cat > "$apps/x-pixel-map.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=X Pixel Map
GenericName=Hot pixel fixer
Comment=Fix hot pixels on a FUJIFILM X-T1 (guided, with backups)
Exec=$root/.venv/bin/python -m xpixelmap.ui
Path=$root
Icon=x-pixel-map
Terminal=false
Categories=Graphics;Photography;
StartupWMClass=x-pixel-map
DESKTOP
chmod 644 "$apps/x-pixel-map.desktop"
desktop-file-validate "$apps/x-pixel-map.desktop" 2>/dev/null || true
update-desktop-database "$apps" 2>/dev/null || true
echo "Installed. Search for \"X Pixel Map\" in your launcher."
