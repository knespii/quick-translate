"""Choosing the integrated GPU (qtrans.gpu), without touching the real environment."""
from qtrans.gpu import AMD, INTEL, MESA_EGL, NVIDIA, gpu_vendors, integrated_env


def everything(_path):
    return True


def test_intel_with_nvidia():
    env = integrated_env({INTEL, NVIDIA}, {}, everything)
    assert env["__EGL_VENDOR_LIBRARY_FILENAMES"] == MESA_EGL
    assert "intel_icd.json" in env["VK_DRIVER_FILES"] and "nvidia" not in env["VK_DRIVER_FILES"]


def test_amd_with_nvidia():
    assert "radeon_icd.json" in integrated_env({AMD, NVIDIA}, {}, everything)["VK_DRIVER_FILES"]


def test_nothing_to_choose():
    assert integrated_env({NVIDIA}, {}, everything) == {}       # NVIDIA only: it must draw there
    assert integrated_env({INTEL}, {}, everything) == {}        # no NVIDIA: nothing to avoid
    assert integrated_env(set(), {}, everything) == {}


def test_user_choice_wins():
    env = integrated_env({INTEL, NVIDIA}, {"__EGL_VENDOR_LIBRARY_FILENAMES": "x", "VK_ICD_FILENAMES": "y"}, everything)
    assert env == {}


def test_missing_driver_files():
    assert integrated_env({INTEL, NVIDIA}, {}, lambda _p: False) == {}


def test_vendors_from_sysfs(tmp_path):
    for card, vendor in (("card0", INTEL), ("card1", NVIDIA), ("card1-eDP-1", "junk")):
        d = tmp_path / card / "device"
        d.mkdir(parents=True)
        (d / "vendor").write_text(vendor + "\n")
    assert gpu_vendors(str(tmp_path)) == {INTEL, NVIDIA}
