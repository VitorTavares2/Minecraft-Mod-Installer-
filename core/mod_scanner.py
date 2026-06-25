"""
Scans a mods/ directory and analyses loader compatibility + MC version.
Reads metadata from inside each JAR without external dependencies:
  - Fabric/Quilt: fabric.mod.json / quilt.mod.json
  - Forge/NeoForge: META-INF/mods.toml / META-INF/neoforge.mods.toml
"""
import json
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ModInfo:
    filename: str
    mod_id: str
    name: str
    version: str
    loader: str           # "fabric" | "forge" | "neoforge" | "quilt" | "unknown"
    mc_range: str         # raw MC version string from metadata
    mc_major: str | None  # e.g. "1.21"
    depends: dict         # dep_id -> version range (Fabric/Quilt only)


@dataclass
class ScanResult:
    mods: list[ModInfo] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    # ── aggregated properties ─────────────────────────────────────────

    @property
    def loader_votes(self) -> dict[str, int]:
        votes: dict[str, int] = {}
        for m in self.mods:
            if m.loader != "unknown":
                votes[m.loader] = votes.get(m.loader, 0) + 1
        return votes

    @property
    def recommended_loader(self) -> str | None:
        v = self.loader_votes
        return max(v, key=v.get) if v else None

    @property
    def mc_votes(self) -> dict[str, int]:
        votes: dict[str, int] = {}
        for m in self.mods:
            if m.mc_major:
                votes[m.mc_major] = votes.get(m.mc_major, 0) + 1
        return votes

    @property
    def recommended_mc(self) -> str | None:
        v = self.mc_votes
        return max(v, key=v.get) if v else None

    def incompatible_mods(self) -> list[tuple[ModInfo, str]]:
        """Return (mod, reason) for mods that don't match the recommendation."""
        loader = self.recommended_loader
        mc = self.recommended_mc
        result = []
        for m in self.mods:
            if loader and m.loader not in ("unknown", loader):
                result.append((m, f"loader: {m.loader} ≠ {loader}"))
            elif mc and m.mc_major and m.mc_major != mc:
                result.append((m, f"MC: {m.mc_major} ≠ {mc}"))
        return result

    def missing_deps(self) -> list[tuple[ModInfo, str, str]]:
        """
        Return (mod, dep_id, dep_version) for dependencies not present.
        Only works for Fabric/Quilt mods with fabric.mod.json.
        """
        present_ids = {m.mod_id for m in self.mods}
        # Built-in IDs that don't need to be in the folder
        BUILTIN = {"minecraft", "java", "fabricloader", "quiltloader",
                   "forge", "neoforge", "neoforged"}
        missing = []
        for m in self.mods:
            for dep_id, dep_ver in m.depends.items():
                if dep_id in BUILTIN:
                    continue
                if dep_id not in present_ids:
                    missing.append((m, dep_id, dep_ver))
        return missing


# ── JAR metadata readers ──────────────────────────────────────────────────────

def _extract_mc_major(s: str) -> str | None:
    """'>=1.21 <1.22' | '[1.21,1.22)' | '1.21.1'  →  '1.21'"""
    m = re.search(r'\b(1\.\d+)', s)
    return m.group(1) if m else None


def _read_fabric_json(zf: zipfile.ZipFile, name: str, loader: str) -> ModInfo | None:
    try:
        with zf.open(name) as f:
            data = json.load(f)
        depends = {k: str(v) for k, v in data.get("depends", {}).items()}
        mc_range = depends.get("minecraft", "")
        return ModInfo(
            filename="",
            mod_id=data.get("id", "?"),
            name=data.get("name", data.get("id", "?")),
            version=str(data.get("version", "?")),
            loader=loader,
            mc_range=mc_range,
            mc_major=_extract_mc_major(mc_range),
            depends=depends,
        )
    except Exception:
        return None


def _read_toml(zf: zipfile.ZipFile, name: str, default_loader: str) -> ModInfo | None:
    """Minimal hand-rolled TOML reader — no tomllib/toml dependency needed."""
    try:
        with zf.open(name) as f:
            text = f.read().decode("utf-8", errors="ignore")

        mod_id = re.search(r'\bmodId\s*=\s*"([^"]+)"', text)
        mod_id = mod_id.group(1) if mod_id else "?"

        version = re.search(r'(?<!loader)\bversion\s*=\s*"([^"$\{][^"]*)"', text)
        version = version.group(1) if version else "?"

        display = re.search(r'displayName\s*=\s*"([^"]+)"', text)
        display = display.group(1) if display else mod_id

        # MC version range inside [[dependencies.*]] block
        mc_range = ""
        mc_dep = re.search(
            r'modId\s*=\s*"minecraft".*?versionRange\s*=\s*"([^"]+)"',
            text, re.DOTALL,
        )
        if mc_dep:
            mc_range = mc_dep.group(1)

        # Determine loader: neoforge.mods.toml or "neoforge" keyword in deps
        loader = default_loader
        if name == "META-INF/neoforge.mods.toml":
            loader = "neoforge"
        elif re.search(r'modId\s*=\s*"neoforge"', text):
            loader = "neoforge"

        return ModInfo(
            filename="",
            mod_id=mod_id,
            name=display,
            version=version,
            loader=loader,
            mc_range=mc_range,
            mc_major=_extract_mc_major(mc_range),
            depends={},
        )
    except Exception:
        return None


def scan_jar(path: Path) -> ModInfo | None:
    try:
        with zipfile.ZipFile(path, "r") as zf:
            names = set(zf.namelist())

            if "quilt.mod.json" in names:
                info = _read_fabric_json(zf, "quilt.mod.json", "quilt")
            elif "fabric.mod.json" in names:
                info = _read_fabric_json(zf, "fabric.mod.json", "fabric")
            elif "META-INF/neoforge.mods.toml" in names:
                info = _read_toml(zf, "META-INF/neoforge.mods.toml", "neoforge")
            elif "META-INF/mods.toml" in names:
                info = _read_toml(zf, "META-INF/mods.toml", "forge")
            else:
                info = ModInfo(
                    filename=path.name, mod_id=path.stem, name=path.stem,
                    version="?", loader="unknown", mc_range="",
                    mc_major=None, depends={},
                )

            if info:
                info.filename = path.name
            return info
    except Exception:
        return None


def scan_mods_dir(mods_dir: str | Path) -> ScanResult:
    result = ScanResult()
    p = Path(mods_dir)

    if not p.is_dir():
        result.errors.append(f"Pasta não encontrada: {mods_dir}")
        return result

    jars = sorted(p.glob("*.jar"))
    if not jars:
        result.errors.append("Nenhum arquivo .jar encontrado na pasta.")
        return result

    for jar in jars:
        info = scan_jar(jar)
        if info:
            result.mods.append(info)
        else:
            result.errors.append(f"Não lido: {jar.name}")

    return result
