"""Copies selected content from a source (ZIP/RAR/folder) to a .minecraft directory."""
import os
import shutil
import zipfile

try:
    import rarfile
    _RAR_OK = True
except ImportError:
    _RAR_OK = False

CONTENT_DIRS = {
    "mods": "mods",
    "config": "config",
    "resourcepacks": "resourcepacks",
    "shaderpacks": "shaderpacks",
    "saves": "saves",
}


def get_minecraft_dir() -> str:
    return os.path.join(os.environ.get("APPDATA", ""), ".minecraft")


def install_modpack(
    source_path: str,
    source_type: str,
    dest_dir: str,
    content_selection: dict,
    preset: dict | None,
    shader: dict | None,
    mc_version: str,
    clean_before: bool = True,
    progress_callback=None,
    log_callback=None,
):
    def log(msg):
        if log_callback:
            log_callback(msg)

    def progress(val: float):
        if progress_callback:
            progress_callback(min(val, 1.0))

    os.makedirs(dest_dir, exist_ok=True)

    selected = [k for k, v in content_selection.items() if v]
    total = len(selected) + (1 if preset else 0) + (1 if shader and shader.get("id") != "none" else 0)
    if total == 0:
        log("Nada selecionado para instalar.")
        progress(1.0)
        return

    step = 0

    ext = os.path.splitext(source_path)[1].lower()
    if source_type == "folder" or os.path.isdir(source_path):
        log(f"Lendo pasta: {source_path}")
        _copy_from_folder(source_path, dest_dir, selected, log, clean_before)
    elif ext == ".rar":
        log(f"Abrindo RAR: {os.path.basename(source_path)}")
        _copy_from_rar(source_path, dest_dir, selected, log, clean_before)
    else:
        log(f"Abrindo ZIP: {os.path.basename(source_path)}")
        _copy_from_zip(source_path, dest_dir, selected, log, clean_before)

    step = len(selected)
    progress(step / total)

    if preset:
        log(f"Aplicando preset '{preset['name']}'...")
        from core.presets import write_options_txt
        write_options_txt(dest_dir, preset, mc_version)
        log("  options.txt atualizado.")
        step += 1
        progress(step / total)

    if shader and shader.get("id") != "none":
        _install_shader(shader, dest_dir, log)
        step += 1
        progress(step / total)

    progress(1.0)
    log("✓ Instalação concluída!")


def _find_zip_prefix(names: list[str], dir_name: str) -> str | None:
    """
    Robustly find the prefix path for dir_name inside a ZIP.
    Handles structures: dir_name/, .minecraft/dir_name/,
    root/dir_name/, root/.minecraft/dir_name/ — regardless of entry order.
    """
    name_set = set(names)

    # Direct
    if any(n.startswith(f"{dir_name}/") for n in name_set):
        return f"{dir_name}/"
    if any(n.startswith(f".minecraft/{dir_name}/") for n in name_set):
        return f".minecraft/{dir_name}/"

    # Scan every entry for a matching segment, regardless of names[0]
    for name in name_set:
        parts = name.replace("\\", "/").split("/")
        for i, part in enumerate(parts[:-1]):
            if part == dir_name:
                prefix = "/".join(parts[:i + 1]) + "/"
                if any(n.startswith(prefix) for n in name_set):
                    return prefix
            if part == ".minecraft" and i + 1 < len(parts) and parts[i + 1] == dir_name:
                prefix = "/".join(parts[:i + 2]) + "/"
                if any(n.startswith(prefix) for n in name_set):
                    return prefix

    return None


def _copy_from_zip(zip_path: str, dest_dir: str, selected: list, log, clean_before: bool):
    with zipfile.ZipFile(zip_path, "r") as zf:
        names = zf.namelist()

        for dir_key in selected:
            dir_name = CONTENT_DIRS[dir_key]

            prefix = _find_zip_prefix(names, dir_name)
            if prefix is None:
                log(f"  ⚠  {dir_name}/ não encontrado no ZIP — pulando.")
                continue

            # Only clean AFTER confirming the folder exists in the source
            dest_subdir = os.path.join(dest_dir, dir_name)
            if clean_before and os.path.isdir(dest_subdir):
                shutil.rmtree(dest_subdir)
                log(f"  🗑  {dir_name}/ limpo.")

            os.makedirs(dest_subdir, exist_ok=True)
            log(f"  Copiando {dir_name}/...")
            count = 0

            for member in names:
                if member.startswith(prefix) and not member.endswith("/"):
                    relative = member[len(prefix):]
                    if not relative:
                        continue
                    target = os.path.join(dest_subdir, relative)
                    os.makedirs(os.path.dirname(target), exist_ok=True)
                    with zf.open(member) as src, open(target, "wb") as dst:
                        shutil.copyfileobj(src, dst)
                    count += 1

            log(f"  ✔  {count} arquivo(s) copiado(s) → {dir_name}/")


def _copy_from_rar(rar_path: str, dest_dir: str, selected: list, log, clean_before: bool):
    if not _RAR_OK:
        raise RuntimeError("Biblioteca 'rarfile' não instalada. Execute: pip install rarfile")
    with rarfile.RarFile(rar_path, "r") as rf:
        names = rf.namelist()
        for dir_key in selected:
            dir_name = CONTENT_DIRS[dir_key]
            prefix = _find_zip_prefix(names, dir_name)
            if prefix is None:
                log(f"  ⚠  {dir_name}/ não encontrado no RAR — pulando.")
                continue
            dest_subdir = os.path.join(dest_dir, dir_name)
            if clean_before and os.path.isdir(dest_subdir):
                shutil.rmtree(dest_subdir)
                log(f"  🗑  {dir_name}/ limpo.")
            os.makedirs(dest_subdir, exist_ok=True)
            log(f"  Copiando {dir_name}/...")
            count = 0
            for member in names:
                if member.startswith(prefix) and not member.endswith("/"):
                    relative = member[len(prefix):]
                    if not relative:
                        continue
                    target = os.path.join(dest_subdir, relative)
                    os.makedirs(os.path.dirname(target), exist_ok=True)
                    with rf.open(member) as src, open(target, "wb") as dst:
                        shutil.copyfileobj(src, dst)
                    count += 1
            log(f"  ✔  {count} arquivo(s) copiado(s) → {dir_name}/")


def _copy_from_folder(src_root: str, dest_dir: str, selected: list, log, clean_before: bool):
    for dir_key in selected:
        dir_name = CONTENT_DIRS[dir_key]
        src_subdir = os.path.join(src_root, dir_name)

        if not os.path.exists(src_subdir):
            log(f"  ⚠  {dir_name}/ não encontrado na pasta fonte — pulando.")
            continue

        dest_subdir = os.path.join(dest_dir, dir_name)

        # Only clean AFTER confirming source exists
        if clean_before and os.path.isdir(dest_subdir):
            shutil.rmtree(dest_subdir)
            log(f"  🗑  {dir_name}/ limpo.")

        os.makedirs(dest_subdir, exist_ok=True)
        log(f"  Copiando {dir_name}/...")
        count = 0

        for item in os.listdir(src_subdir):
            s = os.path.join(src_subdir, item)
            d = os.path.join(dest_subdir, item)
            if os.path.isdir(s):
                shutil.copytree(s, d, dirs_exist_ok=True)
            else:
                shutil.copy2(s, d)
            count += 1

        log(f"  ✔  {count} item(ns) copiado(s) → {dir_name}/")


def _install_shader(shader: dict, dest_dir: str, log):
    shader_file = shader.get("file")
    if not shader_file:
        return

    installer_root = os.path.dirname(os.path.dirname(__file__))
    src = os.path.join(installer_root, "shaderpacks", shader_file)

    if not os.path.exists(src):
        log(f"  ⚠ Shader '{shader_file}' não encontrado em shaderpacks/")
        log(f"    Coloque o arquivo ZIP do shader na pasta shaderpacks/ do instalador.")
        return

    dest_shaderpacks = os.path.join(dest_dir, "shaderpacks")
    os.makedirs(dest_shaderpacks, exist_ok=True)
    shutil.copy2(src, os.path.join(dest_shaderpacks, shader_file))
    log(f"  Shader '{shader['name']}' instalado.")

    with open(os.path.join(dest_dir, "optionsshaders.txt"), "w", encoding="utf-8") as f:
        f.write(f"shaderPack={shader_file}\n")
