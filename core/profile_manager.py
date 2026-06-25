"""Manages multiple .minecraft profiles using Windows directory junctions."""
import json
import os
import shutil
import subprocess
from pathlib import Path

_PROFILES_ROOT = Path(os.environ.get("LOCALAPPDATA", "")) / "MCInstaller" / "profiles"
_META_FILE = Path(os.environ.get("LOCALAPPDATA", "")) / "MCInstaller" / "profiles.json"
_MC_DIR = Path(os.environ.get("APPDATA", "")) / ".minecraft"


# ── internal helpers ──────────────────────────────────────────────────────────

def _load_meta() -> dict:
    if _META_FILE.exists():
        with open(_META_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {"active": None, "profiles": {}}


def _save_meta(meta: dict):
    _META_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(_META_FILE, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)


def _is_junction(path: Path) -> bool:
    return path.exists() and (path.is_symlink() or _win_is_junction(path))


def _win_is_junction(path: Path) -> bool:
    try:
        result = subprocess.run(
            ["fsutil", "reparsepoint", "query", str(path)],
            capture_output=True, text=True
        )
        return "Mount Point" in result.stdout or "Symbolic Link" in result.stdout
    except Exception:
        return False


def _create_junction(link: Path, target: Path):
    """Create a Windows directory junction (no admin required)."""
    subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],
        check=True, capture_output=True,
    )


def _remove_junction(path: Path):
    """Remove a junction without deleting its contents."""
    if _is_junction(path):
        os.rmdir(path)
    elif path.is_dir():
        pass  # real folder — don't auto-delete


def _detect_profile_info(profile_path: Path) -> dict:
    """Try to detect MC version and loader from a profile folder."""
    try:
        from core.detector import detect_source
        info = detect_source(str(profile_path), "folder")
        return {
            "mc_version": info.get("mc_version", "?"),
            "loader": info.get("loader", "?"),
        }
    except Exception:
        return {"mc_version": "?", "loader": "?"}


# ── public API ────────────────────────────────────────────────────────────────

def get_profiles_dir() -> Path:
    return _PROFILES_ROOT


def list_profiles() -> list[dict]:
    """Return list of profile dicts sorted by name."""
    meta = _load_meta()
    active = meta.get("active")
    result = []
    _PROFILES_ROOT.mkdir(parents=True, exist_ok=True)

    for name, info in meta.get("profiles", {}).items():
        path = _PROFILES_ROOT / name
        result.append({
            "name": name,
            "path": str(path),
            "is_active": name == active,
            "mc_version": info.get("mc_version", "?"),
            "loader": info.get("loader", "?"),
            "exists": path.is_dir(),
        })

    return sorted(result, key=lambda p: p["name"].lower())


def get_active_profile() -> str | None:
    return _load_meta().get("active")


def create_profile(name: str) -> dict:
    """Create a new empty profile folder."""
    name = name.strip()
    if not name:
        raise ValueError("Nome do perfil não pode ser vazio.")

    meta = _load_meta()
    if name in meta.get("profiles", {}):
        raise ValueError(f"Perfil '{name}' já existe.")

    path = _PROFILES_ROOT / name
    path.mkdir(parents=True, exist_ok=True)

    meta.setdefault("profiles", {})[name] = {"mc_version": "?", "loader": "?"}
    _save_meta(meta)
    return {"name": name, "path": str(path)}


def import_profile(name: str, src: str | None = None) -> dict:
    """
    Import a profile from an existing folder.
    If src is None, imports the current %APPDATA%\\.minecraft.
    """
    name = name.strip()
    if not name:
        raise ValueError("Nome do perfil não pode ser vazio.")

    meta = _load_meta()
    if name in meta.get("profiles", {}):
        raise ValueError(f"Perfil '{name}' já existe.")

    src_path = Path(src) if src else _MC_DIR

    if not src_path.is_dir():
        raise FileNotFoundError(f"Pasta não encontrada: {src_path}")

    dest = _PROFILES_ROOT / name

    # If the source IS the current .minecraft junction, just register it
    if _is_junction(src_path) and src_path == _MC_DIR:
        raise ValueError(
            "O .minecraft atual já é um perfil gerenciado. "
            "Desative o perfil atual antes de importar."
        )

    # Copy the folder
    shutil.copytree(str(src_path), str(dest))

    info = _detect_profile_info(dest)
    meta.setdefault("profiles", {})[name] = info
    _save_meta(meta)
    return {"name": name, "path": str(dest), **info}


def absorb_current_minecraft(name: str = "default") -> dict:
    """
    Move the real %APPDATA%\\.minecraft into the profiles folder as a named profile.
    Call this when .minecraft exists as a real folder and needs to be managed.
    """
    if not _MC_DIR.exists():
        raise FileNotFoundError(".minecraft não encontrado.")
    if _is_junction(_MC_DIR):
        raise ValueError(".minecraft já é uma junction gerenciada.")

    name = name.strip() or "default"
    meta = _load_meta()

    # Find a unique name
    base = name
    i = 1
    while name in meta.get("profiles", {}):
        name = f"{base}_{i}"
        i += 1

    dest = _PROFILES_ROOT / name
    shutil.move(str(_MC_DIR), str(dest))

    info = _detect_profile_info(dest)
    meta.setdefault("profiles", {})[name] = info
    _save_meta(meta)
    return {"name": name, "path": str(dest), **info}


def activate_profile(name: str, log=None) -> None:
    """Switch the active .minecraft to the named profile."""
    def _log(msg):
        if log:
            log(msg)

    meta = _load_meta()
    if name not in meta.get("profiles", {}):
        raise ValueError(f"Perfil '{name}' não encontrado.")

    target = _PROFILES_ROOT / name
    if not target.is_dir():
        raise FileNotFoundError(f"Pasta do perfil não encontrada: {target}")

    _log(f"Desativando perfil atual...")

    if _MC_DIR.exists():
        if _is_junction(_MC_DIR):
            _remove_junction(_MC_DIR)
            _log("  Junction anterior removida.")
        else:
            # Real folder — absorb it first
            _log("  .minecraft real encontrado — salvando como perfil 'default'...")
            absorbed = absorb_current_minecraft("default")
            meta = _load_meta()
            _log(f"  Salvo como perfil '{absorbed['name']}'.")

    _log(f"Ativando perfil '{name}'...")
    _create_junction(_MC_DIR, target)

    meta["active"] = name
    _save_meta(meta)
    _log(f"  ✔  Perfil '{name}' ativo — .minecraft → {target}")


def delete_profile(name: str) -> None:
    meta = _load_meta()
    if name not in meta.get("profiles", {}):
        raise ValueError(f"Perfil '{name}' não encontrado.")
    if meta.get("active") == name:
        raise ValueError("Não é possível deletar o perfil ativo. Ative outro perfil antes.")

    path = _PROFILES_ROOT / name
    if path.is_dir():
        shutil.rmtree(path)

    del meta["profiles"][name]
    _save_meta(meta)


def rename_profile(old_name: str, new_name: str) -> None:
    new_name = new_name.strip()
    meta = _load_meta()
    if old_name not in meta.get("profiles", {}):
        raise ValueError(f"Perfil '{old_name}' não encontrado.")
    if new_name in meta["profiles"]:
        raise ValueError(f"Já existe um perfil com o nome '{new_name}'.")

    old_path = _PROFILES_ROOT / old_name
    new_path = _PROFILES_ROOT / new_name
    old_path.rename(new_path)

    meta["profiles"][new_name] = meta["profiles"].pop(old_name)
    if meta.get("active") == old_name:
        meta["active"] = new_name
    _save_meta(meta)


def refresh_profile_info(name: str) -> dict:
    """Re-detect MC version/loader for a profile."""
    meta = _load_meta()
    if name not in meta.get("profiles", {}):
        raise ValueError(f"Perfil '{name}' não encontrado.")
    path = _PROFILES_ROOT / name
    info = _detect_profile_info(path)
    meta["profiles"][name].update(info)
    _save_meta(meta)
    return info
