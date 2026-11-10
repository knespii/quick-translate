// Quick Translate: places the popup window of the cz.que.QuickTranslate app like Spotlight.
//
// The app is a normal GTK window started by a GNOME custom shortcut (Super+T). This extension only
// recognises its window and: puts it at 22 % of the screen height, skips GNOME's open/close animation
// (the app animates itself), gives it focus (and closes the overview), hides it when you click outside,
// and lays Liquid Glass under it (glass.js, Atelier's material) – the app then draws no background.
// Disable the extension and the app still works, just centered, animated by GNOME and opaque.

import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Meta from 'gi://Meta';
import St from 'gi://St';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

import {PopupGlass, currentLook} from './glass.js';

const APP_ID = 'cz.que.QuickTranslate';
const APP_PATH = '/cz/que/QuickTranslate';
const WIDTH = 680; // keep in sync with [ui] width in the app config
const TOP = 0.22;  // top edge at 22 % of the work area
const RADIUS = 16; // the card's corners, as the app's style.css has them
const OPEN_MS = 170; // as the app's own animation (app.py OPEN_MS)
const DEBUG = GLib.getenv('QT_DEBUG') === '1';

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
    }

    disable() {
        global.display.get_selection().disconnectObject(this);
        Gio.DBus.session.signal_unsubscribe(this._animateSignal);
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
                this._layGlass(win);
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
                if (this._win === win) {
                    this._win = null;
                    this._glass = null; // (gone with the window's actor)
                }
            },
            this);
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

    _callApp(action, params) {
        Gio.DBus.session.call(APP_ID, APP_PATH, 'org.freedesktop.Application', 'ActivateAction',
            new GLib.Variant('(sava{sv})', [action, params, {}]), null,
            Gio.DBusCallFlags.NO_AUTO_START, 500, null, null);
    }
}
