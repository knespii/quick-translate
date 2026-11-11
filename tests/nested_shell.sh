#!/bin/sh
# Try the app and its GNOME extension in a nested GNOME Shell with throwaway settings (your real settings
# stay untouched): a text editor behind the popup, screenshots, and the Shell's CPU time while typing.
#
#   tests/nested_shell.sh OUTDIR          screenshots + logs in OUTDIR
#   NO_GLASS=1 tests/nested_shell.sh DIR  the same without the extension's glass (to compare)
#   STYLE=default tests/nested_shell.sh DIR   GNOME's light style (prefer-dark otherwise)
set -eu
REPO="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$(mkdir -p "${1:?usage: $0 OUTDIR}" && cd "$1" && pwd)"
T="$OUT/env"
rm -rf "$T"; mkdir -p "$T/data/gnome-shell/extensions" "$T/config"
ln -sfn "$REPO/extension/quick-translate@que.cz" "$T/data/gnome-shell/extensions/quick-translate@que.cz"
before=$(ls "$XDG_RUNTIME_DIR" | grep -E '^wayland-[0-9]+$' || true)
# (A throwaway state dir too: the debug log has the typed text, and the history must not get the test's words.)
export XDG_CONFIG_HOME="$T/config" XDG_DATA_HOME="$T/data" XDG_STATE_HOME="$T/state" GSETTINGS_BACKEND=keyfile QT_DEBUG=1
export QT_NO_GLASS="${NO_GLASS:-0}" MUTTER_DEBUG_DUMMY_MODE_SPECS=1600x900
gsettings set org.gnome.shell enabled-extensions "['quick-translate@que.cz']"
gsettings set org.gnome.shell welcome-dialog-last-shown-version '999'
gsettings set org.gnome.desktop.interface color-scheme "${STYLE:-prefer-dark}"
exec env -u WAYLAND_SOCKET dbus-run-session -- sh -c '
  OUT=$0; REPO=$2
  act() { gapplication action cz.que.QuickTranslate "$@"; }
  gnome-shell --nested --wayland >"$OUT/shell.log" 2>&1 &
  shell=$!
  sleep 6
  export WAYLAND_DISPLAY=$(ls "$XDG_RUNTIME_DIR" | grep -E "^wayland-[0-9]+$" | grep -vxF "$1" | tail -1)
  gnome-text-editor --standalone "$REPO/README.md" >/dev/null 2>&1 &
  sleep 8
  "$REPO/bin/quick-translate" --gapplication-service >"$OUT/app.log" 2>&1 &
  sleep 3
  act query "'"'"'dal si pozor'"'"'"; sleep 0.5
  act toggle
  # (The nested Shell can take seconds to show it: wait for the extension to say so.)
  n=0; until grep -q "shown at" "$OUT/shell.log" || [ $n -ge 100 ]; do sleep 0.1; n=$((n + 1)); done
  sleep 1
  python3 -I "$REPO/tests/shell_screenshot.py" "$OUT/popup.png"
  # Typing: 40 changes of the query, 70 ms apart; the Shell CPU time it costs (clock ticks).
  cpu() { awk "{print \$14 + \$15}" /proc/$shell/stat; }
  c0=$(cpu)
  i=0
  for q in z za zap zapo zapom zapomn zapomne zapomnel zapomne zapomn d da dal "dal " "dal s" "dal si" \
           "dal si " "dal si p" "dal si po" "dal si poz" "dal si pozo" "dal si pozor" g gi giv give "give " \
           "give u" "give up" p po pozor poz po p t ta tak take take; do
    act query "'"'"'$q'"'"'"; sleep 0.07; i=$((i + 1))
  done
  c1=$(cpu)
  echo "typing: $i changes, gnome-shell CPU $((c1 - c0)) ticks ($(getconf CLK_TCK)/s)" | tee "$OUT/typing.txt"
  sleep 1
  python3 -I "$REPO/tests/shell_screenshot.py" "$OUT/popup-after-typing.png"
  act quit; sleep 0.5
  kill $shell; wait $shell 2>/dev/null || true
' "$OUT" "$before" "$REPO"
