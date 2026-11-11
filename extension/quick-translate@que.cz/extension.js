// Quick Translate: places the popup window of the cz.que.QuickTranslate app like Spotlight.
//
// The app is a normal GTK window started by a GNOME custom shortcut (Super+T). This extension only
// recognises its window and: puts it at 22 % of the screen height, skips GNOME's open/close animation
// (the app animates itself), gives it focus (and closes the overview), hides it when you click outside,
// and lays Liquid Glass under it (glass.js, Atelier's material) – the app then draws no background. After
// login it compiles the glass's shader and starts the app, so the first opening does not stutter.
// Disable the extension and the app still works, just centered, animated by GNOME and opaque.

import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Meta from 'gi://Meta';
import St from 'gi://St';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

import {PopupGlass, currentLook, prewarmGlass} from './glass.js';

const APP_ID = 'cz.que.QuickTranslate';
const APP_PATH = '/cz/que/QuickTranslate';
const WIDTH = 680; // keep in sync with [ui] width in the app config
const TOP = 0.22;  // top edge at 22 % of the work area
const RADIUS = 16; // the card's corners, as the app's style.css has them
const OPEN_MS = 170; // as the app's own animation (popup.py OPEN_MS)
const DEBUG = GLib.getenv('QT_DEBUG') === '1';
const PRELOAD_S = 3; // after login: ready the glass and the app before the first Super+T
// Once per Shell session: the extension is disabled on every screen lock and enabled again on unlock, and an
// app the user quit is not started again then. (QT_NO_PRELOAD=1 in the Shell's environment: never.)
let preloaded = false;
// Rewrite mode: Enter pastes the result into the window the text was selected in, with Ctrl+V (Ctrl+Shift+V in
// a terminal) once that window has the focus back; not at all when it does not get it within this time.
const PASTE_WAIT_MS = 1500;
const TERMINAL = /terminal|kgx|ptyxis|console|kitty|alacritty|foot|wezterm|konsole|xterm|tilix|terminator|blackbox|ghostty/i;

function debug(...args) {
    if (DEBUG)
        console.log('quick-translate:', ...args);
}

export default class QuickTranslateExtension extends Extension {
    enable() {
        this._win = null;
        this._glass = null;
        global.display.connectObject('window-created', (_d, win) => this._onWindowCreated(win), this);
        // Text selected since the popup was last shown is handed to the app when it shows (prefill). The Shell
        // always knows the selection; the app, a Wayland client, learns of it only some time after it gets focus.
        this._freshSelection = false;
        global.display.get_selection().connectObject('owner-changed', (_s, type) => {
            debug('owner-changed', type, 'popup focused', Boolean(this._win?.has_focus()));
            if (type === Meta.SelectionType.SELECTION_PRIMARY && !this._win?.has_focus())
                this._freshSelection = true;
        }, this);
        // The app tells when it animates its content away, so the glass goes with it.
        this._animateSignal = Gio.DBus.session.signal_subscribe(APP_ID, 'cz.que.QuickTranslate.Glass', 'Animate',
            APP_PATH, null, Gio.DBusSignalFlags.NONE, (_c, _s, _p, _i, _n, params) => {
                const [show, duration] = params.deep_unpack();
                this._glass?.animate(show, duration);
            });
        this._source = null;  // the window that had the focus when the popup opened
        this._paste = null;
        // (Made now: a virtual device's first key presses are lost while the seat still adds it.)
        this._keyboard = Clutter.get_default_backend().get_default_seat()
            .create_virtual_device(Clutter.InputDeviceType.KEYBOARD_DEVICE);
        this._pasteSignal = Gio.DBus.session.signal_subscribe(APP_ID, 'cz.que.QuickTranslate.Glass', 'Paste',
            APP_PATH, null, Gio.DBusSignalFlags.NONE, () => this._pasteSoon());
        // QT_DEBUG only (tests in a nested Shell): key presses for the focused window, e.g.
        // gdbus emit --session --object-path /cz/que/QuickTranslate --signal cz.que.QuickTranslate.Test.Keys "'ctrl+a'"
        this._testSignal = DEBUG ? Gio.DBus.session.signal_subscribe(null, 'cz.que.QuickTranslate.Test', 'Keys',
            APP_PATH, null, Gio.DBusSignalFlags.NONE, (_c, _s, _p, _i, _n, params) =>
            {
                const spec = params.deep_unpack()[0];
                debug('test keys', spec, 'to', global.display.focus_window?.get_title() ?? 'nothing');
                this._sendKeys(spec.split('+'));
            }) : 0;
        // The first opening must not pay for one-off work: the glass's shader is compiled now, and the app
        // is started (it stays resident) and readies its window, so Super+T only shows it.
        this._prewarm = null;
        this._preloadId = preloaded ? 0 : GLib.timeout_add_seconds(GLib.PRIORITY_LOW, PRELOAD_S, () => {
            this._preloadId = 0;
            preloaded = true;
            let glass = false;
            try {
                glass = GLib.getenv('QT_NO_GLASS') !== '1' && Boolean(currentLook());
                if (glass)
                    this._prewarm = prewarmGlass();
            } catch (e) {
                console.error('quick-translate: glass prewarm failed:', e);
            }
            if (GLib.getenv('QT_NO_PRELOAD') !== '1') {
                this._callApp('preload', [], {start: true});
                // (So the app readies the look it will have: with or without its own background. Queued behind
                // the first call while D-Bus starts the app.)
                this._callApp('glass', [new GLib.Variant('b', glass)], {start: true});
            }
            return GLib.SOURCE_REMOVE;
        });
    }

    disable() {
        if (this._preloadId)
            GLib.source_remove(this._preloadId);
        this._preloadId = 0;
        this._prewarm?.cancel();
        this._prewarm = null;
        global.display.get_selection().disconnectObject(this);
        Gio.DBus.session.signal_unsubscribe(this._animateSignal);
        Gio.DBus.session.signal_unsubscribe(this._pasteSignal);
        if (this._testSignal)
            Gio.DBus.session.signal_unsubscribe(this._testSignal);
        this._stopPaste();
        this._keyboard?.run_dispose();
        this._keyboard = null;
        this._source = null;
        global.display.disconnectObject(this);
        global.stage.disconnectObject(this);
        this._win?.disconnectObject(this);
        this._win = null;
        this._glass?.destroy();
        this._glass = null;
        this._callApp('glass', [new GLib.Variant('b', false)]);
    }

    _isOurs(win) {
        return win.get_gtk_application_id() === APP_ID || win.get_wm_class() === APP_ID;
    }

    _onWindowCreated(win) {
        if (this._isOurs(win)) {
            this._hook(win);
        } else if (!win.get_wm_class()) {
            // The app id can arrive after the window is created.
            const id = win.connect('notify::wm-class', () => {
                win.disconnect(id);
                if (this._isOurs(win))
                    this._hook(win);
            });
        }
    }

    _target(win) {
        const monitor = global.display.get_current_monitor();
        const wa = Main.layoutManager.getWorkAreaForMonitor(monitor);
        const width = win.get_frame_rect().width || WIDTH;
        return [wa.x + Math.round((wa.width - width) / 2), wa.y + Math.round(wa.height * TOP)];
    }

    _hook(win) {
        debug('hooked window', win.get_gtk_application_id(), win.get_wm_class());
        this._win = win;
        // (Before the popup takes the focus: where a rewrite goes back to.)
        const focused = global.display.focus_window;
        if (!focused)
            this._source = null;  // (the desktop: nothing to paste into)
        else if (!this._isOurs(focused))
            this._source = focused;
        const actor = win.get_compositor_private();
        if (actor)
            Main.wm.skipNextEffect(actor);
        win.connectObject(
            'configure', (_w, config) => {
                if (config.get_is_initial()) {
                    const [x, y] = this._target(win);
                    config.set_position(x, y);
                    debug('initial configure ->', x, y);
                }
            },
            'shown', () => {
                const [x, y] = this._target(win);
                const r = win.get_frame_rect();
                if (Math.abs(r.x - x) > 4 || Math.abs(r.y - y) > 4)
                    win.move_frame(true, x, y);
                Main.activateWindow(win);
                this._watchClicks(win);
                this._sendSelection();
                const t0 = GLib.get_monotonic_time();
                this._layGlass(win);
                if (DEBUG)
                    this._traceFrames(t0, GLib.get_monotonic_time() - t0);
                GLib.idle_add(GLib.PRIORITY_DEFAULT, () => {
                    const f = win.get_frame_rect();
                    debug('shown at', f.x, f.y, f.width, f.height, 'target', x, y, 'focus', win.has_focus());
                    return GLib.SOURCE_REMOVE;
                });
            },
            'unmanaging', () => {
                const a = win.get_compositor_private();
                if (a)
                    Main.wm.skipNextEffect(a);
            },
            'unmanaged', () => {
                global.stage.disconnectObject(this);
                // A paste is waiting: give the focus back to the window it goes to (Mutter may pick another).
                const target = this._paste?.target;
                if (target && global.display.focus_window !== target && target.get_compositor_private())
                    Main.activateWindow(target);
                if (this._win === win) {
                    this._win = null;
                    this._glass = null; // (gone with the window's actor)
                }
            },
            this);
    }

    // QT_DEBUG: the Shell's frame times while the popup opens (a long frame is a visible stutter).
    _traceFrames(t0, layUs) {
        const stage = global.stage;
        let last = t0;
        const gaps = [];
        const id = stage.connect('after-paint', () => {
            const now = GLib.get_monotonic_time();
            gaps.push(Math.round((now - last) / 1000));
            last = now;
            if (now - t0 > 600000) {
                stage.disconnect(id);
                debug(`frames: glass ${(layUs / 1000).toFixed(1)} ms, first ${gaps[0]} ms, ` +
                    `max ${Math.max(...gaps)} ms, ${gaps.length} frames: ${gaps.join(' ')}`);
            }
        });
    }

    // --- paste (rewrite mode) ---------------------------------------------------------------------------------
    _pasteSoon() {
        const target = this._source;
        this._stopPaste();
        if (!target || !target.get_compositor_private()) {
            debug('paste: no window to paste into');
            return;
        }
        const ids = (target.get_wm_class() ?? '') + ' ' + (target.get_gtk_application_id() ?? '') + ' ' +
            (target.get_sandboxed_app_id() ?? '');
        const paste = {target, terminal: TERMINAL.test(ids), timeout: 0, delay: 0, owner: {}};
        this._paste = paste;
        const check = () => {
            if (this._paste !== paste || global.display.focus_window !== target || paste.delay)
                return;
            // (A moment for the window to get the keyboard and the clipboard's offer.)
            paste.delay = GLib.timeout_add(GLib.PRIORITY_DEFAULT, 80, () => {
                paste.delay = 0;
                if (this._paste === paste && global.display.focus_window === target) {
                    if (paste.terminal)
                        this._pasteIntoTerminal();
                    else
                        this._sendPaste(false);
                }
                this._stopPaste();
                return GLib.SOURCE_REMOVE;
            });
        };
        global.display.connectObject('notify::focus-window', check, paste.owner);
        check();
        paste.timeout = GLib.timeout_add(GLib.PRIORITY_DEFAULT, PASTE_WAIT_MS, () => {
            paste.timeout = 0;
            if (this._paste === paste && !paste.delay) {
                debug('paste: the window did not get the focus back');
                this._stopPaste();
            }
            return GLib.SOURCE_REMOVE;
        });
        debug('paste into', ids.trim(), paste.terminal ? '(terminal)' : '');
    }

    _stopPaste() {
        const paste = this._paste;
        this._paste = null;
        if (!paste)
            return;
        global.display.disconnectObject(paste.owner);
        if (paste.timeout)
            GLib.source_remove(paste.timeout);
        if (paste.delay)
            GLib.source_remove(paste.delay);
        paste.timeout = paste.delay = 0;
    }

    // A shell runs every line it gets: one line only, without its newline; more lines stay in the clipboard.
    _pasteIntoTerminal() {
        const clipboard = St.Clipboard.get_default();
        clipboard.get_text(St.ClipboardType.CLIPBOARD, (_c, text) => {
            const line = (text ?? '').replace(/[\r\n]+$/, '');
            if (!line || /[\r\n]/.test(line)) {
                debug('paste: several lines, not into a terminal');
                return;
            }
            if (line !== text)
                clipboard.set_text(St.ClipboardType.CLIPBOARD, line);
            this._sendPaste(true);
        });
    }

    _sendPaste(terminal) {
        this._sendKeys(terminal ? ['ctrl', 'shift', 'v'] : ['ctrl', 'v']);
        debug('pasted', terminal ? 'Ctrl+Shift+V' : 'Ctrl+V');
    }

    // Press keys together, then release them: ['ctrl', 'v'] (modifiers, then a key name as Clutter.KEY_*).
    _sendKeys(names) {
        const mods = {ctrl: Clutter.KEY_Control_L, shift: Clutter.KEY_Shift_L, alt: Clutter.KEY_Alt_L};
        const keys = names.map(n => mods[n.toLowerCase()] ?? Clutter[`KEY_${n}`]).filter(k => k !== undefined);
        if (keys.length !== names.length)
            return;
        if (!this._keyboard)
            return;
        let t = GLib.get_monotonic_time();
        for (const key of keys)
            this._keyboard.notify_keyval(t++, key, Clutter.KeyState.PRESSED);
        for (const key of keys.reverse())
            this._keyboard.notify_keyval(t++, key, Clutter.KeyState.RELEASED);
    }

    _watchClicks(win) {
        global.stage.disconnectObject(this);
        global.stage.connectObject('captured-event', (_stage, event) => {
            if (event.type() !== Clutter.EventType.BUTTON_PRESS || this._win !== win)
                return Clutter.EVENT_PROPAGATE;
            const [x, y] = event.get_coords();
            const r = win.get_frame_rect();
            if (x < r.x || y < r.y || x >= r.x + r.width || y >= r.y + r.height)
                this._hideApp();
            return Clutter.EVENT_PROPAGATE;
        }, this);
    }

    _layGlass(win) {
        this._glass?.destroy();
        this._glass = null;
        let on = false;
        try {
            // (QT_NO_GLASS=1 in the Shell's environment: none, to compare – tests/nested_shell.sh.)
            const look = GLib.getenv('QT_NO_GLASS') === '1' ? null : currentLook();
            if (look && win.get_compositor_private()) {
                this._glass = new PopupGlass(win, {...look, radius: RADIUS});
                this._glass.animate(true, OPEN_MS);
                on = true;
            }
        } catch (e) {
            console.error('quick-translate: no glass:', e);
            this._glass = null;
        }
        debug('glass', on);
        this._callApp('glass', [new GLib.Variant('b', on)]);
    }

    _sendSelection() {
        const fresh = this._freshSelection;
        this._freshSelection = false;
        debug('selection fresh', fresh);
        if (!fresh) {
            this._callApp('prefill', [new GLib.Variant('s', '')]);
            return;
        }
        St.Clipboard.get_default().get_text(St.ClipboardType.PRIMARY, (_c, text) =>
            this._callApp('prefill', [new GLib.Variant('s', text ?? '')]));
    }

    _hideApp() {
        debug('click outside -> hide');
        this._callApp('hide', []);
    }

    // start: D-Bus may start the app for this call (otherwise only a running app gets it).
    _callApp(action, params, {start = false} = {}) {
        Gio.DBus.session.call(APP_ID, APP_PATH, 'org.freedesktop.Application', 'ActivateAction',
            new GLib.Variant('(sava{sv})', [action, params, {}]), null,
            start ? Gio.DBusCallFlags.NONE : Gio.DBusCallFlags.NO_AUTO_START, start ? 30000 : 500, null,
            (conn, res) => {
                try {
                    conn.call_finish(res);
                } catch (e) {
                    debug(`app action ${action}:`, e.message);
                }
            });
    }
}
