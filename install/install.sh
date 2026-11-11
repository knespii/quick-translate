#!/bin/sh
# Quick Translate installer.
#   install.sh --phase1 [--debug]  D-Bus service, .desktop file and the custom shortcuts Super+T (translate)
#                                  and Super+R (rewrite the tone of the selected text);
#                                  --debug (developers) starts the app with QT_DEBUG=1: test-only D-Bus actions
#                                  (query, snapshot, pickword, ...) and a DEBUG log that contains the queried text
#   install.sh --uninstall         removes all of that (other custom shortcuts stay untouched)
set -eu
REPO="$(cd "$(dirname "$0")/.." && pwd)"
APP=cz.que.QuickTranslate
SERVICES="${XDG_DATA_HOME:-$HOME/.local/share}/dbus-1/services"
APPS="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
KEY=org.gnome.settings-daemon.plugins.media-keys
BASE=/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings
P=$BASE/quick-translate/          # Super+T
PR=$BASE/quick-translate-rewrite/ # Super+R
MODE= DEBUG=
for a in "$@"; do
  case "$a" in
  --debug) DEBUG=1 ;;
  *) MODE="$MODE$a" ;;
  esac
done

list_edit() {  # $1 = add|remove, $2 = path ; prints the new custom-keybindings list
  cur=$(gsettings get $KEY custom-keybindings)
  python3 -c '
import ast, sys
cur, op, p = sys.argv[1], sys.argv[2], sys.argv[3]
l = ast.literal_eval(cur.removeprefix("@as "))
if op == "add" and p not in l: l.append(p)
if op == "remove": l = [x for x in l if x != p]
print(l)' "$cur" "$1" "$2"
}

shortcut() {  # $1 = path, $2 = name, $3 = action, $4 = binding
  s="$KEY.custom-keybinding:$1"
  gsettings set "$s" name "$2"
  gsettings set "$s" command "gapplication action $APP $3"
  gsettings set "$s" binding "$4"
  gsettings set $KEY custom-keybindings "$(list_edit add "$1")"
}

case "$MODE" in
--phase1)
  mkdir -p "$SERVICES" "$APPS"
  # D-Bus starts the app without our environment: the variable goes into the service's Exec line.
  env_exec=; [ -n "$DEBUG" ] && env_exec="/usr/bin/env QT_DEBUG=1 "
  sed -e "s|@REPO@|$REPO|g" -e "s|^Exec=|Exec=$env_exec|" "$REPO/install/$APP.service" > "$SERVICES/$APP.service"
  sed "s|@REPO@|$REPO|g" "$REPO/install/$APP.desktop" > "$APPS/$APP.desktop"
  shortcut "$P" 'Quick Translate' toggle '<Super>t'
  shortcut "$PR" 'Quick Translate: rewrite' rewrite '<Super>r'
  echo "Installed${DEBUG:+ (debug: QT_DEBUG=1)}. Super+T translates, Super+R rewrites the selected text."
  echo "Shortcuts now: $(gsettings get $KEY custom-keybindings)"
  echo "A running app keeps its old environment: gapplication action $APP quit"
  ;;
--uninstall)
  for p in "$P" "$PR"; do
    gsettings set $KEY custom-keybindings "$(list_edit remove "$p")"
    gsettings reset-recursively "$KEY.custom-keybinding:$p"
  done
  rm -f "$SERVICES/$APP.service" "$APPS/$APP.desktop"
  gapplication action $APP quit 2>/dev/null || true
  echo "Removed. Shortcuts now: $(gsettings get $KEY custom-keybindings)"
  ;;
*)
  echo "usage: $0 --phase1 [--debug] | --uninstall" >&2
  exit 2
  ;;
esac
