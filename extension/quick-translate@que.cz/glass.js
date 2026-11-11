// Liquid Glass under the Quick Translate popup: Atelier's material (glassMaterial.js and glassLook.js,
// copied from Atelier) on one rounded rectangle, the popup's card.
//
// The glass is a child of the popup's window actor, below its surface, so it moves, fades and goes
// with the window. It shows what is under the popup – the wallpaper and the windows below it, as
// clones (a clone of the whole window group would contain the popup itself). Over the wallpaper alone
// it is clear, as Atelier's widgets; while a window lies under the card it is frosted as Atelier frosts
// glass under text (FROST), so the type of the window behind does not compete with the popup's own.
// The app draws no background of its own over it (its 'glass' action).

import Clutter from 'gi://Clutter';
import Cogl from 'gi://Cogl';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import GObject from 'gi://GObject';
import Shell from 'gi://Shell';
import St from 'gi://St';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';

import {FROST, REST_LIGHT, dimmedLook, edgeReach, lookOf, resolveVariant, textureFrame} from './glassLook.js';
import {
    MATERIAL_CODE, MATERIAL_DECLARATIONS, filterBetweenTexels, glassScale, materialUniforms, setMaterial,
    setMaterialArea, setMaterialReach, textureDensity,
} from './glassMaterial.js';

const ATELIER = 'atelier@knespii.github.io';
const DEBUG = GLib.getenv('QT_DEBUG') === '1';
// Text sits on this glass, so in GNOME's dark style it is at least this much darker than Atelier's
// (a white window behind still leaves white type at about 5:1).
const MIN_DIM_DARK = 0;
// How far past the card the glass draws (shadow) and reads what is behind (the frost's blur, at its
// largest: frosted), logical px.
const MARGIN = Math.ceil(Math.max(edgeReach(true), 3 * FROST.sigma + 2));
// The glass grows in steps of this many logical pixels and never shrinks while the popup is shown: as the
// result list animates the window's height every frame, only the shader's uniforms change – no new
// offscreen textures, and the frost is not blurred again (as Atelier's surfaces, its FIT_STEP).
const FIT_STEP = 64;

const SHAPE_DECLARATIONS = `
uniform vec2 origin;
uniform vec2 size;
uniform vec4 rect;
uniform float radius;
uniform vec2 glassLight;

float qt_card(vec2 p) {
    vec2 halfSize = rect.zw * 0.5;
    vec2 q = abs(p - rect.xy - halfSize) - halfSize + vec2(radius);
    return length(max(q, 0.0)) + min(max(q.x, q.y), 0.0) - radius;
}

float atelier_shape(vec2 p) {
    return qt_card(p);
}

float atelier_body(vec2 p) {
    return qt_card(p);
}

float atelier_others(vec2 p) {
    return 100000.0;
}

void atelier_part(vec2 p, out vec2 light, out float thick) {
    light = glassLight;
    thick = min(rect.z, rect.w);
}
${MATERIAL_DECLARATIONS}
`;

const CardGlassEffect = GObject.registerClass(
class QuickTranslateCardGlass extends Shell.GLSLEffect {
    _init(params) {
        super._init(params);
        this._at = Object.fromEntries(['origin', 'size', 'rect', 'radius', 'glassLight']
            .map(name => [name, this.get_uniform_location(name)]));
        this._material = materialUniforms(this);
        this._look = null;
        this._scale = 1;
        this._options = {};
        this.set_uniform_float(this._at.glassLight, 2, REST_LIGHT);
    }

    vfunc_build_pipeline() {
        this.add_glsl_snippet(Cogl.SnippetHook.FRAGMENT, SHAPE_DECLARATIONS, MATERIAL_CODE, false);
    }

    vfunc_paint_target(node, paintContext) {
        const actor = this.get_actor();
        if (actor) {
            // Where the actor lies in the texture Clutter paints it into (glassLook.js textureFrame()).
            const [x, width] = textureFrame(actor.width);
            const [y, height] = textureFrame(actor.height);
            this.set_uniform_float(this._at.origin, 2, [x, y]);
            this.set_uniform_float(this._at.size, 2, [width, height]);
            const density = textureDensity(actor);
            if (this._look && density !== (this._options.density ?? 1))
                this.setMaterial(this._look, this._scale, {...this._options, density});
            if (this._look)
                filterBetweenTexels(this);
        }
        super.vfunc_paint_target(node, paintContext);
    }

    setShape(area, rect, radius) {
        setMaterialArea(this, this._material, area);
        setMaterialReach(this, this._material, [0, 0, area[0], area[1]]);
        this.set_uniform_float(this._at.rect, 4, rect);
        this.set_uniform_float(this._at.radius, 1, [radius]);
        this.queue_repaint();
    }

    setMaterial(look, scale, options) {
        this._look = look;
        this._scale = scale;
        this._options = options;
        setMaterial(this, this._material, look, scale, options);
        this.queue_repaint();
    }
});

// Mutter draws each window only where it shows and is being redrawn this frame (culling), and the glass
// is drawn while that is in force – inside the window group – so a clone of a window under the popup
// would come out in pieces. Mutter skips culling for actors with effects: this one does nothing else.
// It stays on the windows (and the wallpaper) the glass shows only while the popup is up.
const NoCulling = GObject.registerClass(
class QuickTranslateNoCulling extends Clutter.Effect {});

const NO_CULLING = 'quick-translate-no-culling';

function keepWhole(actor) {
    if (!actor.get_effect(NO_CULLING))
        actor.add_effect_with_name(NO_CULLING, new NoCulling());
}

function letCull(actor) {
    try {
        if (actor.get_effect(NO_CULLING))
            actor.remove_effect_by_name(NO_CULLING);
    } catch (e) {
        // (Gone already.)
    }
}

// A clone that stays as large as it is told (the background group says it has no size).
const StageClone = GObject.registerClass(
class QuickTranslateStageClone extends Clutter.Clone {
    vfunc_allocate(box) {
        this.set_allocation(box);
    }
});

/**
 * The glass to draw: Atelier's variant and dim when Atelier is installed (so the popup matches the
 * desktop), regular otherwise; null when glass is off (Atelier's 'off', GNOME's high contrast).
 *
 * @returns {{look: object, light: boolean}|null}
 */
export function currentLook() {
    let variant = 'regular';
    let dim = 0;
    const atelier = atelierSettings();
    if (atelier) {
        variant = atelier.get_string('glass-variant');
        dim = atelier.get_double('glass-dim');
    }
    const a11y = Gio.SettingsSchemaSource.get_default().lookup('org.gnome.desktop.a11y.interface', true);
    const highContrast = a11y?.has_key('high-contrast')
        ? new Gio.Settings({schema_id: 'org.gnome.desktop.a11y.interface'}).get_boolean('high-contrast') : false;
    variant = resolveVariant(variant, {highContrast});
    const look = lookOf(variant);
    if (!look)
        return null;
    const light = new Gio.Settings({schema_id: 'org.gnome.desktop.interface'}).get_string('color-scheme') !==
        'prefer-dark';
    return {look: dimmedLook(look, light ? dim : Math.max(dim, MIN_DIM_DARK)), light};
}

function atelierSettings() {
    try {
        const ext = Main.extensionManager.lookup(ATELIER);
        if (!ext?.dir)
            return null;
        const source = Gio.SettingsSchemaSource.new_from_directory(ext.dir.get_child('schemas').get_path(),
            Gio.SettingsSchemaSource.get_default(), false);
        const schema = source.lookup('org.gnome.shell.extensions.atelier', false);
        return schema ? new Gio.Settings({settings_schema: schema}) : null;
    } catch (e) {
        return null;
    }
}

/** Liquid Glass under a window's card (its frame rectangle), with corners of radius logical pixels. */
export class PopupGlass {
    /**
     * @param {Meta.Window} win
     * @param {object} params
     * @param {object} params.look - currentLook()'s look
     * @param {boolean} params.light - GNOME's style is light
     * @param {number} params.radius - the card's corner radius, logical pixels
     */
    constructor(win, {look, light, radius}) {
        this._win = win;
        this._actor = win.get_compositor_private();
        this._radius = radius;

        this._glass = new St.Widget({clip_to_allocation: true, reactive: false});
        Shell.util_set_hidden_from_pick(this._glass, true);
        this._scene = new Clutter.Actor();
        this._glass.add_child(this._scene);
        this._effect = new CardGlassEffect();
        this._glass.add_effect_with_name('quick-translate-glass', this._effect);
        this._look = look;
        this._light = light;
        this._frost = 0;  // as shown now: 0 clear … 1 frosted (setFrost())
        this._frostTarget = null;
        this._frostTimeline = null;
        this._syncFrost();

        this._whole = new Set();  // actors kept from culling
        this._actor.insert_child_at_index(this._glass, 0);
        this._glass.connect('destroy', () => {
            this._glass = null;
            this._stopFrost();
            this._win.disconnectObject(this);
            this._actor.disconnectObject(this);
            this._releaseWhole();
        });
        this._sync();
        this._buildScene();
        // (Taller glass may reach windows the old one did not: the scene is built again then.)
        win.connectObject(
            'size-changed', () => this._sync() && this._buildScene(),
            'position-changed', () => this._sync() && this._buildScene(),
            this);
        this._actor.connectObject('notify::allocation', () => this._sync(), this);
    }

    // What is under the card: the wallpaper and, above it, the windows below the popup.
    _buildScene() {
        if (!this._glass)
            return;
        this._scene.destroy_all_children();
        this._releaseWhole();
        const background = Main.layoutManager._backgroundGroup;
        this._holdWhole(background);
        const bg = new StageClone({source: background});
        bg.set_size(global.stage.width, global.stage.height);
        this._scene.add_child(bg);
        const [gx, gy, gw, gh] = this._area;
        const card = this._win.get_frame_rect();
        const shown = [];
        let underCard = false;
        // Below the popup in the stack, bottom first. (global.get_window_actors() is not in stacking order
        // while the popup is new.) The popup is raised when shown: if it is not in the stack yet, all the
        // other windows are below it.
        const stack = global.display.sort_windows_by_stacking(global.display.list_all_windows());
        const own = stack.indexOf(this._win);
        const below = own >= 0 ? stack.slice(0, own) : stack.filter(w => w !== this._win);
        const workspace = global.workspace_manager.get_active_workspace();
        for (const mw of below) {
            const actor = mw.get_compositor_private();
            if (!actor || !actor.visible || mw.minimized || !mw.located_on_workspace(workspace))
                continue;
            if (actor.x > gx + gw || actor.y > gy + gh || actor.x + actor.width < gx || actor.y + actor.height < gy)
                continue;
            this._holdWhole(actor);
            const clone = new Clutter.Clone({source: actor});
            clone.set_position(actor.x, actor.y);
            clone.set_size(actor.width, actor.height);
            this._scene.add_child(clone);
            shown.push(`${mw.get_title()} ${actor.x},${actor.y} ${actor.width}x${actor.height}`);
            // (Its frame, not its buffer: the shadow of a window beside the card does not frost it.)
            const r = mw.get_frame_rect();
            underCard ||= r.x < card.x + card.width && r.y < card.y + card.height &&
                r.x + r.width > card.x && r.y + r.height > card.y;
        }
        // (At once when the popup opens; eased as it moves to or off a window.)
        this.setFrost(underCard, {instant: this._frostTarget === null});
        if (DEBUG) {
            const order = stack.map(w => w.get_title()).join(' | ');
            console.log(`quick-translate: glass area ${this._area.map(Math.round)}, windows behind: [${shown.join('; ')}], ` +
                `frosted: ${underCard}, stacking: ${order}`);
        }
    }

    /**
     * Frosted (a window under the card) or clear (only the wallpaper), eased over FROST.time as Atelier's
     * GlassSurface.setFrost().
     *
     * @param {boolean} frosted
     * @param {object} [options]
     * @param {boolean} [options.instant]
     */
    setFrost(frosted, {instant = false} = {}) {
        const target = frosted ? 1 : 0;
        if (target === this._frostTarget)
            return;
        this._frostTarget = target;
        this._stopFrost();
        if (instant || !this._glass) {
            this._frost = target;
            this._syncFrost();
            return;
        }
        const from = this._frost;
        this._frostTimeline = new Clutter.Timeline({actor: this._glass, duration: FROST.time});
        this._frostTimeline.connect('new-frame', timeline => {
            this._frost = from + (target - from) * timeline.get_progress();
            this._syncFrost();
        });
        this._frostTimeline.connect('stopped', (_timeline, finished) => {
            if (!finished)
                return;
            this._frostTimeline = null;
            this._frost = target;
            this._syncFrost();
        });
        this._frostTimeline.start();
    }

    _stopFrost() {
        const timeline = this._frostTimeline;
        this._frostTimeline = null;
        timeline?.stop();
    }

    // GNOME's blur as frosted now (none at all when clear), and the material as frosted once fully so
    // (as Atelier: its own light blur until then).
    _syncFrost() {
        if (!this._glass)
            return;
        const scale = glassScale();
        const radius = Math.round(2 * FROST.sigma * this._frost * scale * 10) / 10;
        let blur = this._glass.get_effect('quick-translate-frost');
        if (radius < 1) {
            if (blur)
                this._glass.remove_effect(blur);
        } else if (!blur) {
            // (Effects run last-added first: added after the glass, the frost blurs what is behind, then
            // the glass bends it.)
            blur = new Shell.BlurEffect({mode: Shell.BlurMode.ACTOR, brightness: 1, radius});
            this._glass.add_effect_with_name('quick-translate-frost', blur);
        } else if (blur.radius !== radius) {
            blur.radius = radius;
        }
        const frosted = this._frost >= 1;
        if (frosted !== this._materialFrosted) {
            this._materialFrosted = frosted;
            this._effect.setMaterial(this._look, scale, {light: this._light, shadow: true, frosted,
                density: textureDensity(this._actor)});
        }
    }

    _holdWhole(actor) {
        keepWhole(actor);
        this._whole.add(actor);
        actor.connectObject('destroy', () => this._whole.delete(actor), this);
    }

    _releaseWhole() {
        for (const actor of this._whole) {
            actor.disconnectObject(this);
            letCull(actor);
        }
        this._whole.clear();
    }

    // The glass over the card and the margin around it, in steps (FIT_STEP); the scene at the stage's
    // origin. Returns whether the glass's area changed.
    _sync() {
        if (!this._glass)
            return false;
        const f = this._win.get_frame_rect();
        const scale = glassScale();
        const m = MARGIN * scale;
        const step = FIT_STEP * scale;
        const need = f.height + 2 * m;
        const height = this._area && this._area[2] === f.width + 2 * m && need <= this._area[3]
            ? this._area[3] : Math.ceil(need / step) * step;
        const area = [f.x - m, f.y - m, f.width + 2 * m, height];
        const moved = area.join() !== this._area?.join();
        this._area = area;
        const [gx, gy, gw, gh] = area;
        // (The window's buffer moves when GTK drops its own shadow: the glass stays over the card.)
        const at = [gx - this._actor.x, gy - this._actor.y];
        if (at.join() !== this._at?.join()) {
            this._at = at;
            this._glass.set_position(...at);
        }
        if (moved) {
            this._glass.set_size(gw, gh);
            this._scene.set_position(-gx, -gy);
            // (It grows and shrinks from the card's top edge, as the app's content does.)
            this._glass.set_pivot_point((m + f.width / 2) / gw, m / gh);
        }
        this._effect.setShape([gw, gh], [m, m, f.width, f.height], this._radius * scale);
        return moved;
    }

    /**
     * Fade and grow in, or out, as the app animates its content (popup.py OPEN_MS, CLOSE_MS).
     *
     * @param {boolean} show
     * @param {number} duration - ms
     */
    animate(show, duration) {
        const g = this._glass;
        if (!g)
            return;
        g.remove_all_transitions();
        if (show) {
            g.opacity = 0;
            g.set_scale(0.97, 0.97);
            g.translation_y = -8;
        }
        g.ease({
            opacity: show ? 255 : 0,
            scale_x: show ? 1 : 0.97,
            scale_y: show ? 1 : 0.97,
            translation_y: show ? 0 : -8,
            duration,
            mode: show ? Clutter.AnimationMode.EASE_OUT_CUBIC : Clutter.AnimationMode.EASE_IN_CUBIC,
        });
    }

    destroy() {
        this._stopFrost();
        this._win.disconnectObject(this);
        this._actor.disconnectObject(this);
        this._releaseWhole();
        this._glass?.destroy();
        this._glass = null;
    }
}
