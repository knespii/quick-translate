#!/bin/sh
# Quick Translate installer.
#   install.sh --phase1     D-Bus service, .desktop file and the Super+T custom shortcut
#   install.sh --uninstall  removes all of that (other custom shortcuts stay untouched)
set -eu
REPO="$(cd "$(dirname "$0")/.." && pwd)"
APP=cz.que.QuickTranslate
SERVICES="${XDG_DATA_HOME:-$HOME/.local/share}/dbus-1/services"
APPS="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
KEY=org.gnome.settings-daemon.plugins.media-keys
P=/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/quick-translate/
S="$KEY.custom-keybinding:$P"

list_edit() {  # $1 = add|remove ; prints the new custom-keybindings list
  cur=$(gsettings get $KEY custom-keybindings)
  python3 -c '
import ast, sys
cur, op, p = sys.argv[1], sys.argv[2], sys.argv[3]
l = ast.literal_eval(cur.removeprefix("@as "))
if op == "add" and p not in l: l.append(p)
if op == "remove": l = [x for x in l if x != p]
print(l)' "$cur" "$1" "$P"
}

case "${1:-}" in
--phase1)
  mkdir -p "$SERVICES" "$APPS"
  sed "s|@REPO@|$REPO|g" "$REPO/install/$APP.service" > "$SERVICES/$APP.service"
  sed "s|@REPO@|$REPO|g" "$REPO/install/$APP.desktop" > "$APPS/$APP.desktop"
  gsettings set "$S" name 'Quick Translate'
  gsettings set "$S" command "gapplication action $APP toggle"
  gsettings set "$S" binding '<Super>t'
  gsettings set $KEY custom-keybindings "$(list_edit add)"
  echo "Installed. Press Super+T. Shortcuts now: $(gsettings get $KEY custom-keybindings)"
  ;;
--uninstall)
  gsettings set $KEY custom-keybindings "$(list_edit remove)"
  gsettings reset-recursively "$S"
  rm -f "$SERVICES/$APP.service" "$APPS/$APP.desktop"
  gapplication action $APP quit 2>/dev/null || true
  echo "Removed. Shortcuts now: $(gsettings get $KEY custom-keybindings)"
  ;;
*)
  echo "usage: $0 --phase1 | --uninstall" >&2
  exit 2
  ;;
esac
