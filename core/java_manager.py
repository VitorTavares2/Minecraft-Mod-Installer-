"""Background Java detection and download using Eclipse Temurin (Adoptium)."""
import os
import shutil
import subprocess
import threading
import zipfile
import requests

ADOPTIUM_API = (
    "https://api.adoptium.net/v3/assets/latest/21/hotspot"
    "?os=windows&architecture=x64&image_type=jre&vendor=eclipse"
)

_JAVA_DIR = os.path.join(os.environ.get("LOCALAPPDATA", ""), "MCInstaller", "java")


def find_java() -> str | None:
    """Return path to java.exe if found, else None."""
    # Check PATH
    java = shutil.which("java") or shutil.which("javaw")
    if java:
        return java

    # Check bundled location
    bundled = os.path.join(_JAVA_DIR, "bin", "java.exe")
    if os.path.exists(bundled):
        return bundled

    # Common install paths
    common = [
        r"C:\Program Files\Java",
        r"C:\Program Files\Eclipse Adoptium",
        r"C:\Program Files\Microsoft",
    ]
    for base in common:
        if os.path.isdir(base):
            for entry in os.listdir(base):
                candidate = os.path.join(base, entry, "bin", "java.exe")
                if os.path.exists(candidate):
                    return candidate

    return None


def is_java_installed() -> bool:
    return find_java() is not None


def download_java_background(log_callback=None, done_callback=None) -> threading.Thread:
    """Start a background thread that downloads and extracts Java 21 JRE."""

    def _run():
        def log(msg):
            if log_callback:
                log_callback(msg)

        try:
            log("[Java] Java não encontrado. Baixando Java 21 JRE (Adoptium)...")
            resp = requests.get(ADOPTIUM_API, timeout=15)
            resp.raise_for_status()
            assets = resp.json()

            if not assets:
                log("[Java] Não foi possível obter informações do download.")
                return

            binary = assets[0].get("binary", {})
            pkg = binary.get("package", {})
            url = pkg.get("link")
            size = pkg.get("size", 0)

            if not url:
                log("[Java] URL de download não encontrada.")
                return

            log(f"[Java] Baixando: {url}")
            os.makedirs(_JAVA_DIR, exist_ok=True)
            zip_path = os.path.join(_JAVA_DIR, "jre21.zip")

            with requests.get(url, stream=True, timeout=120) as r:
                r.raise_for_status()
                downloaded = 0
                with open(zip_path, "wb") as f:
                    for chunk in r.iter_content(chunk_size=8192):
                        f.write(chunk)
                        downloaded += len(chunk)
                        if size:
                            pct = int(downloaded / size * 100)
                            log(f"[Java] Baixando... {pct}%")

            log("[Java] Extraindo JRE...")
            with zipfile.ZipFile(zip_path, "r") as zf:
                # The ZIP has a root folder like jdk-21.0.3+9-jre
                members = zf.namelist()
                prefix = members[0].split("/")[0] + "/"
                for member in members:
                    relative = member[len(prefix):]
                    if not relative:
                        continue
                    target = os.path.join(_JAVA_DIR, relative)
                    if member.endswith("/"):
                        os.makedirs(target, exist_ok=True)
                    else:
                        os.makedirs(os.path.dirname(target), exist_ok=True)
                        with zf.open(member) as src, open(target, "wb") as dst:
                            shutil.copyfileobj(src, dst)

            os.remove(zip_path)
            log("[Java] Java 21 instalado com sucesso!")

            if done_callback:
                done_callback(find_java())

        except Exception as e:
            log(f"[Java] Erro ao baixar Java: {e}")
            if done_callback:
                done_callback(None)

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return t


def ensure_java(log_callback=None, done_callback=None):
    """Check Java; if missing, download it in background."""
    path = find_java()
    if path:
        if log_callback:
            log_callback(f"[Java] Java encontrado: {path}")
        if done_callback:
            done_callback(path)
        return

    download_java_background(log_callback=log_callback, done_callback=done_callback)
