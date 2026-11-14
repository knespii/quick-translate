"""Draw on the integrated GPU of a laptop with an NVIDIA card as well.

GTK picks its renderer through libglvnd, which tries NVIDIA's EGL first; a resident app then keeps a context on the
NVIDIA card for good, and the card never sleeps (power). The popup needs no discrete GPU: with an Intel or AMD
integrated GPU next to an NVIDIA one, EGL and Vulkan are limited to the integrated GPU's (Mesa) drivers.

Must run before GTK is loaded (environment variables read when the drivers load). Pure Python, no GTK.
"""
import glob
import os
import re
from pathlib import Path

NVIDIA, INTEL, AMD = "0x10de", "0x8086", "0x1002"
MESA_EGL = "/usr/share/glvnd/egl_vendor.d/50_mesa.json"
VK_ICDS = {INTEL: ("intel_icd.json", "intel_hasvk_icd.json"), AMD: ("radeon_icd.json",)}
VK_DIRS = ("/usr/share/vulkan/icd.d", "/etc/vulkan/icd.d")


def gpu_vendors(drm: str = "/sys/class/drm") -> set[str]:
    out = set()
    for vendor in glob.glob(f"{drm}/card[0-9]*/device/vendor"):
        if not re.fullmatch(r"card\d+", Path(vendor).parent.parent.name):
            continue  # (a connector: card1-eDP-1)
        try:
            out.add(Path(vendor).read_text().strip().lower())
        except OSError:
            pass
    return out


def integrated_env(vendors: set[str], environ, exists=os.path.exists) -> dict[str, str]:
    """The variables that keep GTK on the integrated GPU ({} when there is no NVIDIA card, no integrated GPU, or the
    user chose the drivers already)."""
    igpu = INTEL if INTEL in vendors else AMD if AMD in vendors else None
    if NVIDIA not in vendors or igpu is None:
        return {}
    env = {}
    if "__EGL_VENDOR_LIBRARY_FILENAMES" not in environ and "__EGL_VENDOR_LIBRARY_DIRS" not in environ \
            and exists(MESA_EGL):
        env["__EGL_VENDOR_LIBRARY_FILENAMES"] = MESA_EGL
    if not any(k in environ for k in ("VK_DRIVER_FILES", "VK_ICD_FILENAMES")):
        icds = [f"{d}/{name}" for d in VK_DIRS for name in VK_ICDS[igpu] if exists(f"{d}/{name}")]
        if icds:
            env["VK_DRIVER_FILES"] = ":".join(icds)
    return env


def prefer_integrated() -> dict[str, str]:
    """Set the variables for this process; returns what was set (for the log)."""
    env = integrated_env(gpu_vendors(), os.environ)
    os.environ.update(env)
    return env
