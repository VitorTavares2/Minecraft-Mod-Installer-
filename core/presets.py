"""Manages options.txt presets and shader data."""
import json
import os

_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")


def get_presets() -> list:
    with open(os.path.join(_DATA_DIR, "presets.json"), encoding="utf-8") as f:
        return json.load(f)["presets"]


def get_shaders() -> list:
    with open(os.path.join(_DATA_DIR, "shaders.json"), encoding="utf-8") as f:
        return json.load(f)["shaders"]


def filter_shaders_for_version(shaders: list, mc_version: str) -> list:
    from core.detector import version_tuple
    ver = version_tuple(mc_version)
    return [
        s for s in shaders
        if version_tuple(s.get("min_version", "1.0")) <= ver <= version_tuple(s.get("max_version", "99.0"))
    ]


def write_options_txt(dest_dir: str, preset: dict, mc_version: str) -> None:
    from core.detector import version_tuple
    ver = version_tuple(mc_version)

    options_path = os.path.join(dest_dir, "options.txt")

    # Read existing options to preserve keys we're not overwriting
    existing: dict[str, str] = {}
    if os.path.exists(options_path):
        with open(options_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if ":" in line:
                    key, _, value = line.partition(":")
                    existing[key.strip()] = value.strip()

    options = dict(preset.get("options", {}))

    # Version-aware key mapping
    # graphicsMode was fancyGraphics before 1.16
    if ver < (1, 16, 0) and "graphicsMode" in options:
        val = options.pop("graphicsMode")
        options["fancyGraphics"] = "false" if val == "0" else "true"

    # simulationDistance didn't exist before 1.18
    if ver < (1, 18, 0):
        options.pop("simulationDistance", None)

    # clouds: older versions used true/false
    if ver < (1, 13, 0) and "clouds" in options:
        val = options["clouds"]
        if val == "false":
            options["clouds"] = "false"
        else:
            options["clouds"] = "true"

    existing.update(options)

    with open(options_path, "w", encoding="utf-8") as f:
        for key, value in existing.items():
            f.write(f"{key}:{value}\n")
