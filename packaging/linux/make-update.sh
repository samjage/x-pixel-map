#!/bin/sh
# Make update: rebuild ~/.local/share/x-pixel-map from the git checkout in this folder.
# The installed app is a snapshot; this replaces only its code, never its session data
# (xpixelmap/data: your backups, journals and finished sessions are left alone) and never
# your camera files (camera/, captures/, out/ in the checkout are not copied).
set -eu
here=$(cd "$(dirname "$0")" && pwd)
root=$(cd "$here/../.." && pwd)
dst=${XPIXELMAP_INSTALL_DIR:-$HOME/.local/share/x-pixel-map}

if [ ! -f "$root/xpixelmap/__init__.py" ]; then
  echo "This script belongs inside the X Pixel Map checkout (looked in $root)." >&2
  exit 1
fi
if [ ! -d "$dst" ]; then
  echo "$dst does not exist yet. Run packaging/linux/install.sh first." >&2
  exit 1
fi

echo "Updating $dst from $root ..."
rsync -a --exclude '.git' --exclude '.venv' --exclude '__pycache__' \
      --exclude '/camera' --exclude '/captures' --exclude '/out' \
      --exclude '/graphify-out' --exclude '/research' --exclude '/xpixelmap/data' \
      "$root/" "$dst/"

# Refresh the dependencies too, so a new import does not break the installed copy.
if [ -x "$dst/.venv/bin/python" ]; then
  echo "Refreshing the Python packages ..."
  if command -v uv >/dev/null 2>&1; then
    uv pip install --python "$dst/.venv/bin/python" numpy pywebview unicorn
  else
    "$dst/.venv/bin/python" -m pip install --upgrade numpy pywebview unicorn
  fi
else
  echo "No interpreter in $dst/.venv; run packaging/linux/install.sh to rebuild it." >&2
  exit 1
fi

# Re-assert the path file, in case the environment was rebuilt or this is an older install.
for sp in "$dst"/.venv/lib/python*/site-packages; do
  [ -d "$sp" ] && echo "$dst" > "$sp/x-pixel-map.pth"
done

echo "Done. Version now installed: $(cd "$dst" && .venv/bin/python -c 'import xpixelmap; print(xpixelmap.__version__)' 2>/dev/null || echo unknown)"
echo "Re-open X Pixel Map from your launcher to use it."
