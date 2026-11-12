#!/bin/sh
# Install X Pixel Map for the current user: the app in ~/.local/share/x-pixel-map, an entry in the
# applications list and an icon. Nothing outside ~/.local is touched and no root is needed.
# The installed app is a snapshot of this checkout's code, not a link to it: changing the checkout
# does nothing until you run packaging/linux/make-update.sh. Remove it with --uninstall.
set -eu
here=$(cd "$(dirname "$0")" && pwd)
root=$(cd "$here/../.." && pwd)
data=${XDG_DATA_HOME:-$HOME/.local/share}
apps=$data/applications
icons=$data/icons/hicolor/scalable/apps
dst=${XPIXELMAP_INSTALL_DIR:-$data/x-pixel-map}

if [ "${1:-}" = "--uninstall" ]; then
  rm -f "$apps/x-pixel-map.desktop" "$icons/x-pixel-map.svg"
  rm -rf "$dst"
  update-desktop-database "$apps" 2>/dev/null || true
  echo "Removed the app folder ($dst), the launcher and the icon. Your checkout ($root) was left alone."
  exit 0
fi

echo "Installing X Pixel Map into $dst ..."
mkdir -p "$dst"
# Code only: no git history, no session data, no camera files, no research or build output.
rsync -a --exclude '.git' --exclude '.venv' --exclude '__pycache__' \
      --exclude '/camera' --exclude '/captures' --exclude '/out' \
      --exclude '/graphify-out' --exclude '/research' --exclude '/xpixelmap/data' \
      "$root/" "$dst/"

if [ ! -x "$dst/.venv/bin/python" ]; then
  echo "Creating the Python environment in $dst/.venv ..."
  if command -v uv >/dev/null 2>&1; then
    uv venv --python /usr/bin/python3 --system-site-packages "$dst/.venv"
    uv pip install --python "$dst/.venv/bin/python" numpy pywebview unicorn
  else
    /usr/bin/python3 -m venv --system-site-packages "$dst/.venv"
    "$dst/.venv/bin/python" -m pip install numpy pywebview unicorn
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
Exec=$dst/.venv/bin/python -m xpixelmap.ui
Path=$dst
Icon=x-pixel-map
Terminal=false
Categories=Graphics;Photography;
StartupWMClass=x-pixel-map
DESKTOP
chmod 644 "$apps/x-pixel-map.desktop"
desktop-file-validate "$apps/x-pixel-map.desktop" 2>/dev/null || true
update-desktop-database "$apps" 2>/dev/null || true
echo "Installed. Search for \"X Pixel Map\" in your launcher."
echo "Changed this checkout since? Run packaging/linux/make-update.sh to copy the new code in."
