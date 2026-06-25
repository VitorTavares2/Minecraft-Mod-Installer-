"""Find and launch the official Minecraft Launcher."""
import json
import os
import shutil
import subprocess
import winreg
from pathlib import Path

_MC_DIR = Path(os.environ.get("APPDATA", "")) / ".minecraft"

_LAUNCHER_CANDIDATES = [
    # Standard installer (user-local, no admin) — most common
    r"%LOCALAPPDATA%\Programs\Minecraft Launcher\MinecraftLauncher.exe",
    # Standard installer (system-wide)
    r"%PROGRAMFILES(X86)%\Minecraft Launcher\MinecraftLauncher.exe",
    r"%PROGRAMFILES%\Minecraft Launcher\MinecraftLauncher.exe",
    # Microsoft Store / Xbox Game Pass
    r"%LOCALAPPDATA%\Packages\Microsoft.4297127D64EC6_8wekyb3d8bbwe"
    r"\LocalCache\Local\runtime\launcher-ui\MinecraftLauncher.exe",
    # Xbox Game Pass standalone install
    r"C:\XboxGames\Minecraft Launcher\Content\Minecraft.exe",
    # Legacy Mojang launcher
    r"%APPDATA%\.minecraft\launcher\MinecraftLauncher.exe",
    r"%PROGRAMFILES(X86)%\Minecraft\MinecraftLauncher.exe",
]

_SETTINGS_FILE = Path(os.environ.get("LOCALAPPDATA", "")) / "MCInstaller" / "launcher_path.txt"


def _registry_lookup() -> str | None:
    """Try to find the launcher via Windows registry (uninstall entries)."""
    keys = [
        (winreg.HKEY_LOCAL_MACHINE,
         r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\Minecraft Launcher"),
        (winreg.HKEY_LOCAL_MACHINE,
         r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Minecraft Launcher"),
        (winreg.HKEY_CURRENT_USER,
         r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Minecraft Launcher"),
    ]
    for hive, subkey in keys:
        try:
            with winreg.OpenKey(hive, subkey) as k:
                install_loc, _ = winreg.QueryValueEx(k, "InstallLocation")
                candidate = os.path.join(install_loc, "MinecraftLauncher.exe")
                if os.path.isfile(candidate):
                    return candidate
        except OSError:
            continue
    return None


def _protocol_handler_lookup() -> str | None:
    """Check the minecraft:// protocol handler for the launcher path."""
    try:
        with winreg.OpenKey(
            winreg.HKEY_CLASSES_ROOT, r"minecraft\shell\open\command"
        ) as k:
            cmd, _ = winreg.QueryValueEx(k, "")
            # Format: "C:\path\MinecraftLauncher.exe" "%1"
            exe = cmd.strip().split('"')[1]
            if os.path.isfile(exe):
                return exe
    except (OSError, IndexError):
        pass
    return None


def get_custom_launcher_path() -> str | None:
    """Return user-saved custom launcher path, if set."""
    if _SETTINGS_FILE.exists():
        p = _SETTINGS_FILE.read_text(encoding="utf-8").strip()
        if p and os.path.isfile(p):
            return p
    return None


def save_custom_launcher_path(path: str):
    _SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    _SETTINGS_FILE.write_text(path, encoding="utf-8")


def find_launcher() -> str | None:
    # 1. User-saved custom path
    custom = get_custom_launcher_path()
    if custom:
        return custom

    # 2. Candidate paths
    for raw in _LAUNCHER_CANDIDATES:
        p = os.path.expandvars(raw)
        if p and os.path.isfile(p):
            return p

    # 3. Registry uninstall entry
    reg = _registry_lookup()
    if reg:
        return reg

    # 4. minecraft:// protocol handler
    proto = _protocol_handler_lookup()
    if proto:
        return proto

    # 5. PATH
    return shutil.which("MinecraftLauncher") or shutil.which("minecraft-launcher")


def get_launcher_profiles(mc_dir: Path | None = None) -> list[dict]:
    profiles_file = (mc_dir or _MC_DIR) / "launcher_profiles.json"
    if not profiles_file.exists():
        return []
    try:
        with open(profiles_file, encoding="utf-8") as f:
            data = json.load(f)
        result = []
        for pid, p in data.get("profiles", {}).items():
            result.append({
                "id": pid,
                "name": p.get("name", pid),
                "version": p.get("lastVersionId", ""),
                "type": p.get("type", "custom"),
            })
        return sorted(result, key=lambda x: x["name"].lower())
    except Exception:
        return []


def find_profile_for_version(mc_version: str, loader: str | None = None) -> str | None:
    profiles = get_launcher_profiles()
    loader_lower = (loader or "").lower()

    for p in profiles:
        v = p["version"].lower()
        if mc_version in v and (not loader_lower or loader_lower in v):
            return p["id"]

    for p in profiles:
        if mc_version in p["version"]:
            return p["id"]

    return None


def launch_minecraft(profile_id: str | None = None, log=None) -> None:
    launcher = find_launcher()
    if not launcher:
        raise FileNotFoundError("not_found")

    cmd = [launcher]
    if profile_id:
        cmd += ["--launch", profile_id]

    if log:
        log(f"  Launcher: {launcher}")
        if profile_id:
            log(f"  Perfil: {profile_id}")
        log("  Iniciando Minecraft...")

    subprocess.Popen(cmd, creationflags=subprocess.DETACHED_PROCESS)
