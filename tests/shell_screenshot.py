"""Screenshot of the GNOME Shell on this session bus (the nested one in tests/nested_shell.sh).

The Shell lets only a few names call its Screenshot API; this owns one of them (org.gnome.Screenshot).
Usage: python3 tests/shell_screenshot.py OUT.png
"""
import sys
import time

from gi.repository import Gio, GLib

bus = Gio.bus_get_sync(Gio.BusType.SESSION)
owned = []
Gio.bus_own_name_on_connection(bus, "org.gnome.Screenshot", Gio.BusNameOwnerFlags.NONE,
                               lambda *_a: owned.append(1), None)
ctx = GLib.MainContext.default()
end = time.time() + 3
while not owned and time.time() < end:
    ctx.iteration(False)
    time.sleep(0.01)
bus.call_sync("org.gnome.Shell.Screenshot", "/org/gnome/Shell/Screenshot", "org.gnome.Shell.Screenshot",
              "Screenshot", GLib.Variant("(bbs)", (False, False, sys.argv[1])), None,
              Gio.DBusCallFlags.NONE, 5000, None)
print("screenshot:", sys.argv[1])
