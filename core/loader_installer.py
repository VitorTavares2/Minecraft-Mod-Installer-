"""Fetches loader/MC versions from official APIs and runs installers."""
import os
import shutil
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

import requests

_CACHE_DIR = Path(os.environ.get("LOCALAPPDATA", "")) / "MCInstaller" / "installers"

# ── version fetching ─────────────────────────────────────────────────────────

def fetch_fabric_mc_versions() -> list[str]:
    r = requests.get("https://meta.fabricmc.net/v2/versions/game", timeout=12)
    r.raise_for_status()
    return [v["version"] for v in r.json() if v.get("stable")]


def fetch_fabric_loader_versions(mc_version: str) -> list[str]:
    url = f"https://meta.fabricmc.net/v2/versions/loader/{mc_version}"
    r = requests.get(url, timeout=12)
    r.raise_for_status()
    data = r.json()
    return [entry["loader"]["version"] for entry in data if entry["loader"].get("stable")]


def _latest_fabric_installer() -> str:
    r = requests.get("https://meta.fabricmc.net/v2/versions/installer", timeout=12)
    r.raise_for_status()
    stable = [v for v in r.json() if v.get("stable")]
    return stable[0]["version"] if stable else r.json()[0]["version"]


def fetch_quilt_mc_versions() -> list[str]:
    r = requests.get("https://meta.quiltmc.org/v3/versions/game", timeout=12)
    r.raise_for_status()
    return [v["version"] for v in r.json()]


def fetch_quilt_loader_versions() -> list[str]:
    r = requests.get("https://meta.quiltmc.org/v3/versions/loader", timeout=12)
    r.raise_for_status()
    return [v["version"] for v in r.json()]


def _parse_maven_xml(url: str) -> list[str]:
    r = requests.get(url, timeout=15)
    r.raise_for_status()
    root = ET.fromstring(r.text)
    return [v.text for v in root.findall(".//version") if v.text]


def fetch_forge_versions() -> dict[str, list[str]]:
    """Return {mc_version: [forge_version, ...]} newest first."""
    versions = _parse_maven_xml(
        "https://maven.minecraftforge.net/net/minecraftforge/forge/maven-metadata.xml"
    )
    result: dict[str, list[str]] = {}
    for v in reversed(versions):
        parts = v.split("-")
        if len(parts) >= 2:
            mc, forge = parts[0], parts[1]
            result.setdefault(mc, []).append(forge)
    return result


def fetch_neoforge_versions() -> dict[str, list[str]]:
    """Return {mc_version: [neoforge_version, ...]} newest first."""
    versions = _parse_maven_xml(
        "https://maven.neoforged.net/releases/net/neoforged/neoforge/maven-metadata.xml"
    )
    result: dict[str, list[str]] = {}
    for v in reversed(versions):
        if any(tag in v for tag in ("-beta", "-alpha", "-rc")):
            continue
        parts = v.split(".")
        if len(parts) >= 2:
            try:
                mc = f"1.{parts[0]}.{parts[1]}"
                result.setdefault(mc, []).append(v)
            except IndexError:
                pass
    return result


def fetch_mc_versions(loader: str) -> list[str]:
    """Return list of MC versions supported by this loader."""
    if loader == "fabric":
        return fetch_fabric_mc_versions()
    if loader == "quilt":
        return fetch_quilt_mc_versions()
    if loader == "forge":
        return list(fetch_forge_versions().keys())
    if loader == "neoforge":
        return list(fetch_neoforge_versions().keys())
    return []


def fetch_loader_versions(loader: str, mc_version: str) -> list[str]:
    """Return available loader versions for a given MC version."""
    if loader == "fabric":
        return fetch_fabric_loader_versions(mc_version)
    if loader == "quilt":
        return fetch_quilt_loader_versions()
    if loader == "forge":
        return fetch_forge_versions().get(mc_version, [])
    if loader == "neoforge":
        return fetch_neoforge_versions().get(mc_version, [])
    return []


# ── download ─────────────────────────────────────────────────────────────────

def _download(url: str, dest: Path, log=None, progress=None) -> Path:
    if log:
        log(f"  Baixando: {url}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    with requests.get(url, stream=True, timeout=120) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length", 0))
        done = 0
        with open(dest, "wb") as f:
            for chunk in r.iter_content(8192):
                f.write(chunk)
                done += len(chunk)
                if progress and total:
                    progress(done / total * 0.6)
    return dest


def download_installer(
    loader: str, mc_version: str, loader_version: str,
    log=None, progress=None,
) -> Path:
    """Download the appropriate installer JAR and return its path."""
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)

    if loader == "fabric":
        inst_ver = _latest_fabric_installer()
        jar_name = f"fabric-installer-{inst_ver}.jar"
        url = (f"https://maven.fabricmc.net/net/fabricmc/fabric-installer/"
               f"{inst_ver}/fabric-installer-{inst_ver}.jar")

    elif loader == "quilt":
        r = requests.get("https://maven.quiltmc.org/repository/release/org/quiltmc/"
                         "quilt-installer/maven-metadata.xml", timeout=12)
        root = ET.fromstring(r.text)
        inst_ver = root.findtext(".//latest") or root.findtext(".//release")
        jar_name = f"quilt-installer-{inst_ver}.jar"
        url = (f"https://maven.quiltmc.org/repository/release/org/quiltmc/"
               f"quilt-installer/{inst_ver}/quilt-installer-{inst_ver}.jar")

    elif loader == "forge":
        combo = f"{mc_version}-{loader_version}"
        jar_name = f"forge-{combo}-installer.jar"
        url = (f"https://maven.minecraftforge.net/net/minecraftforge/forge/"
               f"{combo}/forge-{combo}-installer.jar")

    elif loader == "neoforge":
        jar_name = f"neoforge-{loader_version}-installer.jar"
        url = (f"https://maven.neoforged.net/releases/net/neoforged/neoforge/"
               f"{loader_version}/neoforge-{loader_version}-installer.jar")

    else:
        raise ValueError(f"Loader desconhecido: {loader}")

    dest = _CACHE_DIR / jar_name
    if dest.exists():
        if log:
            log(f"  Usando cache: {jar_name}")
        if progress:
            progress(0.6)
        return dest

    return _download(url, dest, log=log, progress=progress)


# ── installation ─────────────────────────────────────────────────────────────

def run_installer(
    loader: str, mc_version: str, loader_version: str,
    jar_path: Path, java_path: str,
    log=None, progress=None,
) -> None:
    """Run the downloaded installer JAR."""
    if log:
        log(f"  Executando instalador...")

    if loader == "fabric":
        cmd = [
            java_path, "-jar", str(jar_path),
            "client",
            "-mcversion", mc_version,
            "-loader", loader_version,
        ]
    elif loader == "quilt":
        cmd = [
            java_path, "-jar", str(jar_path),
            "install", "client", mc_version,
            f"--loader-version={loader_version}",
        ]
    else:
        # Forge / NeoForge: launch the GUI installer
        cmd = [java_path, "-jar", str(jar_path)]

    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.stdout and log:
        for line in result.stdout.splitlines():
            log(f"  {line}")
    if result.returncode != 0:
        err = result.stderr.strip() or f"exit code {result.returncode}"
        raise RuntimeError(f"Instalador falhou: {err}")

    if progress:
        progress(1.0)
    if log:
        log(f"✔  {loader.capitalize()} {loader_version} para MC {mc_version} instalado!")


def install_loader(
    loader: str, mc_version: str, loader_version: str,
    log=None, progress=None,
) -> None:
    """Full pipeline: check Java → download → run installer."""
    from core.java_manager import find_java

    java = find_java()
    if not java:
        raise RuntimeError(
            "Java não encontrado. Vá ao painel ☕ Java e instale primeiro."
        )

    if log:
        log(f"Java: {java}")
        log(f"Instalando {loader.capitalize()} {loader_version} para MC {mc_version}...")

    jar = download_installer(loader, mc_version, loader_version,
                             log=log, progress=progress)
    run_installer(loader, mc_version, loader_version, jar, java,
                  log=log, progress=progress)
