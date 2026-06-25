"""Detects Minecraft version and mod loader from a source ZIP or folder."""
import zipfile
import json
import os
import re


def version_tuple(version_str: str) -> tuple:
    try:
        return tuple(int(x) for x in str(version_str).split("."))
    except Exception:
        return (0, 0, 0)


def detect_source(path: str, source_type: str) -> dict:
    """
    Returns dict with keys: mc_version, loader, loader_version, pack_name.
    """
    if source_type == "zip":
        return _detect_from_zip(path)
    return _detect_from_folder(path)


def _detect_from_zip(path: str) -> dict:
    with zipfile.ZipFile(path, "r") as zf:
        names = zf.namelist()

        # Look for manifest.json at root or one folder deep
        for name in names:
            parts = [p for p in name.replace("\\", "/").split("/") if p]
            if parts and parts[-1] == "manifest.json" and len(parts) <= 2:
                with zf.open(name) as f:
                    data = json.load(f)
                return {
                    "mc_version": data.get("minecraft_version", "unknown"),
                    "loader": data.get("loader", "vanilla").lower(),
                    "loader_version": data.get("loader_version", ""),
                    "pack_name": data.get("name", "Modpack"),
                }

        # Fallback: detect from filenames inside the ZIP
        return _detect_from_filenames(names)


def _detect_from_folder(path: str) -> dict:
    profiles_path = os.path.join(path, "launcher_profiles.json")
    if os.path.exists(profiles_path):
        try:
            with open(profiles_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for profile in data.get("profiles", {}).values():
                result = _parse_version_id(profile.get("lastVersionId", ""))
                if result:
                    return result
        except Exception:
            pass

    mods_path = os.path.join(path, "mods")
    if os.path.exists(mods_path):
        return _detect_from_filenames(os.listdir(mods_path))

    return {"mc_version": "unknown", "loader": "unknown", "loader_version": "", "pack_name": "Modpack"}


def _parse_version_id(version_id: str) -> dict | None:
    # fabric-loader-0.15.11-1.20.1
    m = re.match(r"fabric-loader-([\d.]+)-([\d.]+)", version_id)
    if m:
        return {"mc_version": m.group(2), "loader": "fabric", "loader_version": m.group(1), "pack_name": "Modpack"}

    # 1.20.1-forge-47.2.0
    m = re.match(r"([\d.]+)-forge-([\d.]+)", version_id)
    if m:
        return {"mc_version": m.group(1), "loader": "forge", "loader_version": m.group(2), "pack_name": "Modpack"}

    # neoforge-21.1.0
    m = re.match(r"neoforge-([\d.]+)", version_id)
    if m:
        ver = m.group(1)
        parts = ver.split(".")
        mc = f"1.{parts[0]}.{parts[1]}" if len(parts) >= 2 else "unknown"
        return {"mc_version": mc, "loader": "neoforge", "loader_version": ver, "pack_name": "Modpack"}

    # quilt-loader-0.20.0-beta.10+1.20.1
    m = re.match(r"quilt-loader-([\d.]+[^+]*)\+([\d.]+)", version_id)
    if m:
        return {"mc_version": m.group(2), "loader": "quilt", "loader_version": m.group(1), "pack_name": "Modpack"}

    return None


def _detect_from_filenames(filenames: list) -> dict:
    loader = "vanilla"
    mc_version = "unknown"

    for name in filenames:
        low = name.lower()
        if "fabric-api" in low or "fabric_api" in low:
            loader = "fabric"
        elif "neoforge" in low:
            loader = "neoforge"
        elif "forge" in low and loader == "vanilla":
            loader = "forge"
        elif "quilt" in low and loader == "vanilla":
            loader = "quilt"

        if mc_version == "unknown":
            m = re.search(r"\b(1\.\d{1,2}(?:\.\d{1,2})?)\b", name)
            if m:
                mc_version = m.group(1)

    return {"mc_version": mc_version, "loader": loader, "loader_version": "", "pack_name": "Modpack"}
