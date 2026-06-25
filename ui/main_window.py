"""Minecraft Modpack Installer — sidebar navigation + terminal aesthetic."""
import os
import shutil
import threading
import tkinter as tk
from tkinter import filedialog, messagebox

import customtkinter as ctk

from core.detector import detect_source
from core.presets import get_presets, get_shaders, filter_shaders_for_version
from core.copier import install_modpack, get_minecraft_dir, CONTENT_DIRS
from core.java_manager import ensure_java, is_java_installed, find_java
from core.profile_manager import (
    list_profiles, create_profile, import_profile, activate_profile,
    delete_profile, rename_profile, absorb_current_minecraft,
    get_active_profile, get_profiles_dir,
)
from core.loader_installer import (
    fetch_mc_versions, fetch_loader_versions, install_loader,
)
from core.launcher import (
    find_launcher, find_profile_for_version, launch_minecraft,
    get_launcher_profiles, save_custom_launcher_path,
)
from core.mod_scanner import scan_mods_dir
from core.copier import get_minecraft_dir as _get_mc_dir

# ── colour tokens ────────────────────────────────────────────────────────────
C_BG      = "#0D1117"
C_SURF    = "#161B22"
C_SURF2   = "#1C2128"
C_BORDER  = "#30363D"
C_GREEN   = "#3FB950"
C_GREEN2  = "#238636"
C_YELLOW  = "#D29922"
C_RED     = "#F85149"
C_BLUE    = "#58A6FF"
C_TEXT    = "#E6EDF3"
C_MUTED   = "#8B949E"
C_DIM     = "#484F58"
FONT_MONO = ("Consolas", 11)
FONT_MONO_B = ("Consolas", 11, "bold")
FONT_MONO_L = ("Consolas", 13, "bold")
FONT_MONO_XL = ("Consolas", 16, "bold")

NAV = [
    ("🧙", "Instalação Completa", "wizard"),
    ("🔧", "Mod Loader",          "loader"),
    ("☕", "Java",               "java"),
    ("⚙️ ", "Preset de Config",   "preset"),
    ("🗑️", "Limpar .minecraft",  "clean"),
]

COMMON_MC_VERSIONS = [
    "Todos", "1.21.4", "1.21.1", "1.21", "1.20.6", "1.20.4", "1.20.1",
    "1.20", "1.19.4", "1.19.2", "1.18.2", "1.17.1", "1.16.5",
    "1.15.2", "1.14.4", "1.13.2", "1.12.2", "1.8.9",
]

WIZARD_STEPS = ["Fonte", "Preset", "Shader", "Conteúdo", "Instalar"]


# ── helpers ──────────────────────────────────────────────────────────────────

def _bar(value: float, width: int = 22) -> str:
    filled = int(value * width)
    return f"[{'█' * filled}{'░' * (width - filled)}] {int(value * 100):>3}%"


def _section(title: str, width: int = 56) -> str:
    inner = f"── {title} "
    pad = "─" * max(0, width - len(inner) - 1)
    return f"╭{inner}{pad}╮"


def _section_end(width: int = 56) -> str:
    return "╰" + "─" * (width - 2) + "╯"


# ── reusable widgets ─────────────────────────────────────────────────────────

class Card(ctk.CTkFrame):
    """Clickable selection card."""

    def __init__(self, master, title: str, body: str, detail: str = "", **kw):
        super().__init__(master, fg_color=C_SURF2, border_width=1,
                         border_color=C_BORDER, corner_radius=6, cursor="hand2", **kw)
        self._cb = None

        ctk.CTkLabel(self, text=title, font=FONT_MONO_B, text_color=C_TEXT,
                     anchor="w").pack(anchor="w", padx=12, pady=(10, 2))
        ctk.CTkLabel(self, text=body, font=FONT_MONO, text_color=C_MUTED,
                     wraplength=260, justify="left", anchor="w").pack(anchor="w", padx=12)
        if detail:
            ctk.CTkLabel(self, text=detail, font=("Consolas", 10), text_color=C_DIM,
                         wraplength=260, justify="left", anchor="w").pack(anchor="w", padx=12, pady=(1, 10))
        else:
            ctk.CTkLabel(self, text="").pack(pady=3)

        for w in [self] + list(self.winfo_children()):
            w.bind("<Button-1>", self._click)

    def set_cb(self, cb):
        self._cb = cb

    def _click(self, _=None):
        if self._cb:
            self._cb()

    def select(self, on: bool):
        self.configure(border_color=C_GREEN if on else C_BORDER,
                       fg_color=(C_SURF if not on else "#0D2818") if on else C_SURF2)


class BlockProgress(ctk.CTkFrame):
    """Terminal-style block progress bar."""

    def __init__(self, master, **kw):
        super().__init__(master, fg_color="transparent", **kw)
        self._lbl = ctk.CTkLabel(self, text=_bar(0), font=("Consolas", 12),
                                 text_color=C_GREEN)
        self._lbl.pack(anchor="w")

    def set(self, v: float):
        self._lbl.configure(text=_bar(v))


class LogBox(ctk.CTkTextbox):
    def __init__(self, master, **kw):
        super().__init__(master, font=("Consolas", 11), state="disabled",
                         fg_color=C_BG, text_color=C_TEXT, **kw)

    def append(self, msg: str):
        self.configure(state="normal")
        self.insert("end", msg + "\n")
        self.see("end")
        self.configure(state="disabled")

    def clear(self):
        self.configure(state="normal")
        self.delete("1.0", "end")
        self.configure(state="disabled")


class SectionLabel(ctk.CTkLabel):
    def __init__(self, master, title: str, **kw):
        super().__init__(master, text=_section(title), font=("Consolas", 11),
                         text_color=C_DIM, anchor="w", **kw)


class Divider(ctk.CTkFrame):
    def __init__(self, master, **kw):
        super().__init__(master, height=1, fg_color=C_BORDER, **kw)


class VersionTreeWidget(ctk.CTkScrollableFrame):
    """
    Accordion-style hierarchical version selector.
    Parent rows show the major.minor group (e.g. 1.21).
    Clicking a parent expands it to reveal child versions (1.21.1, 1.21.4…).
    Clicking a child selects it and fires on_select(version).
    """

    def __init__(self, master, on_select=None, height=220, **kw):
        super().__init__(master, height=height, fg_color=C_BG,
                         scrollbar_button_color=C_SURF2,
                         scrollbar_button_hover_color=C_BORDER, **kw)
        self._on_select = on_select
        self._expanded: set[str] = set()
        self._selected: str | None = None
        self._parent_btns: dict[str, ctk.CTkButton] = {}
        self._child_frames: dict[str, ctk.CTkFrame] = {}
        self._child_btns: dict[str, list[ctk.CTkButton]] = {}
        self._placeholder()

    def _placeholder(self):
        ctk.CTkLabel(self,
                     text="  Selecione um loader e clique em ↻ Carregar",
                     font=FONT_MONO, text_color=C_DIM).pack(anchor="w", pady=12)

    @staticmethod
    def _group(versions: list[str]) -> dict[str, list[str]]:
        groups: dict[str, list[str]] = {}
        for v in versions:
            parts = v.split(".")
            parent = f"{parts[0]}.{parts[1]}" if len(parts) >= 2 else v
            groups.setdefault(parent, []).append(v)
        return groups

    def load(self, versions: list[str]):
        for w in self.winfo_children():
            w.destroy()
        self._parent_btns.clear()
        self._child_frames.clear()
        self._child_btns.clear()
        self._expanded.clear()
        self._selected = None

        if not versions:
            self._placeholder()
            return

        groups = self._group(versions)
        for parent, children in groups.items():
            n = len(children)
            label = f"  ▶  {parent}   ·  {n} versão{'ões' if n > 1 else ''}"

            wrapper = ctk.CTkFrame(self, fg_color="transparent")
            wrapper.pack(fill="x", pady=1)

            btn = ctk.CTkButton(
                wrapper, text=label, anchor="w",
                font=FONT_MONO, height=32,
                fg_color=C_SURF, hover_color=C_SURF2,
                text_color=C_TEXT, corner_radius=4,
                command=lambda p=parent: self._toggle(p),
            )
            btn.pack(fill="x")
            self._parent_btns[parent] = btn

            cf = ctk.CTkFrame(wrapper, fg_color="transparent")
            self._child_frames[parent] = cf
            self._child_btns[parent] = []

            for child in children:
                cb = ctk.CTkButton(
                    cf, text=f"        ○  {child}", anchor="w",
                    font=("Consolas", 10), height=26,
                    fg_color="transparent", hover_color=C_SURF,
                    text_color=C_MUTED, corner_radius=4,
                    command=lambda v=child, p=parent: self._select(v, p),
                )
                cb.pack(fill="x", padx=(24, 0), pady=1)
                self._child_btns[parent].append(cb)

    def _toggle(self, parent: str):
        cf = self._child_frames[parent]
        btn = self._parent_btns[parent]
        if parent in self._expanded:
            self._expanded.discard(parent)
            cf.pack_forget()
            btn.configure(
                text=btn.cget("text").replace("▼", "▶"),
                fg_color=C_SURF,
            )
        else:
            self._expanded.add(parent)
            cf.pack(fill="x")
            btn.configure(
                text=btn.cget("text").replace("▶", "▼"),
                fg_color=C_SURF2,
            )

    def _select(self, version: str, parent: str):
        # Reset all children
        for btns in self._child_btns.values():
            for b in btns:
                b.configure(text_color=C_MUTED, fg_color="transparent")
        # Highlight chosen
        for b in self._child_btns.get(parent, []):
            if b.cget("text").strip().endswith(version):
                b.configure(text_color=C_GREEN, fg_color=C_SURF)
                break
        self._selected = version
        if self._on_select:
            self._on_select(version)

    def get(self) -> str | None:
        return self._selected

    def clear(self):
        self.load([])

    def _select_version_auto(self, version: str):
        """Expand the parent group and select the version programmatically."""
        parts = version.split(".")
        parent = f"{parts[0]}.{parts[1]}" if len(parts) >= 2 else version
        if parent in self._child_frames and parent not in self._expanded:
            self._toggle(parent)
        self._select(version, parent)


# ── main window ──────────────────────────────────────────────────────────────

class MinecraftInstallerApp(ctk.CTk):

    def __init__(self):
        super().__init__()
        self.configure(fg_color=C_BG)
        self.title("MC Modpack Installer")
        self.geometry("960x660")
        self.resizable(False, False)

        # ── shared state ──────────────────────────────────────────────────
        self._src_type = tk.StringVar(value="zip")
        self._src_path: str | None = None
        self._mc_ver   = "unknown"
        self._loader   = "unknown"
        self._preset: dict | None = None
        self._shader: dict | None = None
        self._dest     = tk.StringVar(value=get_minecraft_dir())

        self._content_vars = {
            "mods":          tk.BooleanVar(value=True),
            "config":        tk.BooleanVar(value=True),
            "resourcepacks": tk.BooleanVar(value=False),
            "shaderpacks":   tk.BooleanVar(value=False),
            "saves":         tk.BooleanVar(value=False),
        }

        self._preset_cards: list[Card] = []
        self._shader_cards: list[Card] = []

        # wizard sub-state
        self._wiz_step = 0
        self._wiz_frames: list[ctk.CTkFrame] = []

        self._panels: dict[str, ctk.CTkFrame] = {}
        self._nav_btns: dict[str, ctk.CTkButton] = {}
        self._active_panel = ""

        self._build_ui()
        self._switch("wizard")

    # ── layout ───────────────────────────────────────────────────────────

    def _build_ui(self):
        # ── header ────────────────────────────────────────────────────
        hdr = ctk.CTkFrame(self, fg_color=C_SURF, height=52, corner_radius=0)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)

        ctk.CTkLabel(hdr, text="⛏  Minecraft Modpack Installer",
                     font=("Consolas", 15, "bold"), text_color=C_GREEN).pack(side="left", padx=20)
        ctk.CTkButton(hdr, text="▶  Launch", width=100, height=32,
                      font=("Consolas", 11, "bold"),
                      fg_color=C_GREEN2, hover_color=C_GREEN, corner_radius=6,
                      command=self._hdr_launch).pack(side="right", padx=16)

        Divider(self).pack(fill="x")

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True)

        # ── sidebar ───────────────────────────────────────────────────
        side = ctk.CTkFrame(body, fg_color=C_SURF, width=188, corner_radius=0)
        side.pack(side="left", fill="y")
        side.pack_propagate(False)

        ctk.CTkLabel(side, text="NAVEGAÇÃO", font=("Consolas", 9),
                     text_color=C_DIM).pack(anchor="w", padx=16, pady=(14, 4))

        for item in NAV:
            if item is None:
                Divider(side).pack(fill="x", padx=12, pady=6)
                continue
            icon, label, key = item
            btn = ctk.CTkButton(
                side,
                text=f" {icon}  {label}",
                anchor="w",
                font=FONT_MONO,
                fg_color="transparent",
                text_color=C_MUTED,
                hover_color=C_SURF2,
                corner_radius=4,
                height=34,
                command=lambda k=key: self._switch(k),
            )
            btn.pack(fill="x", padx=8, pady=1)
            self._nav_btns[key] = btn

        # ── main content ──────────────────────────────────────────────
        Divider(body).pack(side="left", fill="y")

        main = ctk.CTkFrame(body, fg_color="transparent")
        main.pack(side="left", fill="both", expand=True)

        self._panels["wizard"]  = self._mk_wizard(main)
        self._panels["loader"]  = self._mk_loader(main)
        self._panels["java"]    = self._mk_java(main)
        self._panels["preset"]  = self._mk_preset(main)
        self._panels["clean"]   = self._mk_clean(main)

    def _switch(self, key: str):
        for k, p in self._panels.items():
            if k == key:
                p.pack(fill="both", expand=True, padx=22, pady=16)
            else:
                p.pack_forget()

        for k, b in self._nav_btns.items():
            b.configure(
                fg_color=C_SURF2 if k == key else "transparent",
                text_color=C_TEXT if k == key else C_MUTED,
            )

        self._active_panel = key

    # ── panel: home ──────────────────────────────────────────────────────

    def _mk_home(self, parent) -> ctk.CTkFrame:
        f = ctk.CTkFrame(parent, fg_color="transparent")

        ctk.CTkLabel(f, text="╭── ⛏  Bem-vindo ──────────────────────────────────────────╮",
                     font=("Consolas", 12), text_color=C_DIM, anchor="w").pack(anchor="w")
        ctk.CTkLabel(f, text="│  Minecraft Modpack Installer  ·  Selecione uma função",
                     font=("Consolas", 12), text_color=C_MUTED, anchor="w").pack(anchor="w")
        ctk.CTkLabel(f, text="╰──────────────────────────────────────────────────────────╯",
                     font=("Consolas", 12), text_color=C_DIM, anchor="w").pack(anchor="w", pady=(0, 16))

        grid = ctk.CTkFrame(f, fg_color="transparent")
        grid.pack(fill="x")
        grid.columnconfigure(0, weight=1)
        grid.columnconfigure(1, weight=1)

        cards_info = [
            ("📦  Copiar Arquivos",     "Instale mods, configs e saves\nde um ZIP ou pasta .minecraft.", "copy"),
            ("⚙️   Preset de Config",    "Aplique um perfil gráfico ao\noptions.txt do Minecraft.",       "preset"),
            ("✨  Instalar Shader",      "Copie e ative um shader no\nMinecraft sem abrir pastas.",       "shader"),
            ("☕  Gerenciar Java",       "Verifique ou baixe o Java\nnecessário para rodar os loaders.",  "java"),
            ("🧙  Instalação Completa",  "Wizard passo a passo que\ncombina todas as funções.",           "wizard"),
        ]

        for i, (title, body, key) in enumerate(cards_info):
            r, c = divmod(i, 2)
            card = Card(grid, title, body)
            card.grid(row=r, column=c, padx=6, pady=6, sticky="nsew")
            card.set_cb(lambda k=key: self._switch(k))

        # .minecraft status
        mc = get_minecraft_dir()
        exists = os.path.isdir(mc)

        status = ctk.CTkFrame(f, fg_color=C_SURF, corner_radius=6)
        status.pack(fill="x", pady=(18, 0))

        def _row(icon, label, ok):
            color = C_GREEN if ok else C_YELLOW
            text = f" {icon}  {label}"
            ctk.CTkLabel(status, text=text, font=FONT_MONO, text_color=color,
                         anchor="w").pack(anchor="w", padx=14, pady=4)

        ctk.CTkLabel(status, text="  STATUS DO SISTEMA", font=("Consolas", 9),
                     text_color=C_DIM, anchor="w").pack(anchor="w", padx=14, pady=(8, 2))
        _row("✔" if exists else "✖", f".minecraft  →  {mc}", exists)

        java_path = find_java()
        java_ok = java_path is not None
        self._home_java_lbl = ctk.CTkLabel(
            status,
            text=f" {'✔' if java_ok else '⚠'}  Java  →  {java_path or 'não encontrado'}",
            font=FONT_MONO,
            text_color=C_GREEN if java_ok else C_YELLOW,
            anchor="w",
        )
        self._home_java_lbl.pack(anchor="w", padx=14, pady=4)

        ctk.CTkLabel(status, text="").pack(pady=2)

        return f

    # ── panel: profiles ──────────────────────────────────────────────────

    def _mk_profiles(self, parent) -> ctk.CTkFrame:
        f = ctk.CTkFrame(parent, fg_color="transparent")

        SectionLabel(f, "🎮  Perfis de .minecraft").pack(anchor="w")
        ctk.CTkLabel(f, text="│  Gerencie múltiplas instalações. Cada perfil é uma pasta .minecraft independente.",
                     font=FONT_MONO, text_color=C_MUTED, anchor="w").pack(anchor="w")
        ctk.CTkLabel(f, text=_section_end(), font=FONT_MONO,
                     text_color=C_DIM, anchor="w").pack(anchor="w", pady=(0, 8))

        # Action bar
        actions = ctk.CTkFrame(f, fg_color="transparent")
        actions.pack(fill="x", pady=(0, 8))

        ctk.CTkButton(actions, text="＋  Novo Perfil", width=130, height=30,
                      font=FONT_MONO, fg_color=C_GREEN2, hover_color=C_GREEN,
                      command=self._profiles_new).pack(side="left", padx=(0, 6))
        ctk.CTkButton(actions, text="⬆  Importar .minecraft atual", width=200, height=30,
                      font=FONT_MONO, fg_color=C_SURF2, hover_color=C_BORDER,
                      command=self._profiles_absorb).pack(side="left", padx=6)
        ctk.CTkButton(actions, text="📁  Importar Pasta...", width=150, height=30,
                      font=FONT_MONO, fg_color=C_SURF2, hover_color=C_BORDER,
                      command=self._profiles_import_folder).pack(side="left", padx=6)

        Divider(f).pack(fill="x", pady=(4, 8))

        # Scrollable profile list
        self._profiles_list = ctk.CTkScrollableFrame(f, fg_color="transparent", height=340)
        self._profiles_list.pack(fill="both", expand=True)

        # Log
        self._profiles_log = LogBox(f, height=60)
        self._profiles_log.pack(fill="x", pady=(8, 0))

        return f

    def _profiles_refresh(self):
        """Rebuild the profile list cards."""
        for w in self._profiles_list.winfo_children():
            w.destroy()

        profiles = list_profiles()

        if not profiles:
            ctk.CTkLabel(
                self._profiles_list,
                text="  Nenhum perfil cadastrado ainda.\n  Use 'Importar .minecraft atual' para começar.",
                font=FONT_MONO, text_color=C_DIM, justify="left",
            ).pack(anchor="w", pady=20)
            return

        for p in profiles:
            self._profiles_mk_card(self._profiles_list, p)

    def _profiles_mk_card(self, parent, p: dict):
        is_active = p["is_active"]
        border = C_GREEN if is_active else C_BORDER

        card = ctk.CTkFrame(parent, fg_color=C_SURF, border_width=1,
                            border_color=border, corner_radius=6)
        card.pack(fill="x", pady=4)

        # Left: info
        info = ctk.CTkFrame(card, fg_color="transparent")
        info.pack(side="left", fill="both", expand=True, padx=14, pady=10)

        active_tag = "  ● ATIVO" if is_active else ""
        ctk.CTkLabel(info, text=f"{p['name']}{active_tag}",
                     font=FONT_MONO_B,
                     text_color=C_GREEN if is_active else C_TEXT,
                     anchor="w").pack(anchor="w")

        meta = f"MC {p['mc_version']}  ·  {p['loader'].capitalize()}  ·  {p['path']}"
        ctk.CTkLabel(info, text=meta, font=("Consolas", 10),
                     text_color=C_DIM, anchor="w").pack(anchor="w")

        # Right: buttons
        btns = ctk.CTkFrame(card, fg_color="transparent")
        btns.pack(side="right", padx=10, pady=10)

        # ▶ Jogar — always visible; activates profile then launches MC
        ctk.CTkButton(
            btns, text="▶  Jogar", width=90, height=28, font=FONT_MONO,
            fg_color=C_GREEN2, hover_color=C_GREEN,
            command=lambda n=p["name"], mc=p["mc_version"], ld=p["loader"]:
                self._profiles_launch(n, mc, ld),
        ).pack(side="left", padx=4)

        if not is_active:
            ctk.CTkButton(
                btns, text="⬆  Ativar", width=90, height=28, font=FONT_MONO,
                fg_color=C_SURF2, hover_color=C_BORDER,
                command=lambda n=p["name"]: self._profiles_activate(n),
            ).pack(side="left", padx=4)

        ctk.CTkButton(
            btns, text="✎", width=34, height=28, font=FONT_MONO,
            fg_color=C_SURF2, hover_color=C_BORDER,
            command=lambda n=p["name"]: self._profiles_rename(n),
        ).pack(side="left", padx=2)

        if not is_active:
            ctk.CTkButton(
                btns, text="🗑", width=34, height=28, font=FONT_MONO,
                fg_color=C_SURF2, hover_color=C_RED,
                command=lambda n=p["name"]: self._profiles_delete(n),
            ).pack(side="left", padx=2)

    def _profiles_log_msg(self, msg: str):
        self._profiles_log.append(msg)

    def _profiles_new(self):
        dialog = ctk.CTkInputDialog(text="Nome do novo perfil:", title="Novo Perfil")
        name = dialog.get_input()
        if not name:
            return
        try:
            create_profile(name)
            self._profiles_log_msg(f"✔  Perfil '{name}' criado.")
            self._profiles_refresh()
        except Exception as e:
            messagebox.showerror("Erro", str(e))

    def _profiles_absorb(self):
        dialog = ctk.CTkInputDialog(
            text="Nome para o perfil atual (.minecraft):",
            title="Importar .minecraft atual",
        )
        name = dialog.get_input()
        if not name:
            return
        try:
            result = absorb_current_minecraft(name)
            self._profiles_log_msg(f"✔  .minecraft importado como '{result['name']}'.")
            self._profiles_refresh()
        except Exception as e:
            messagebox.showerror("Erro", str(e))

    def _profiles_import_folder(self):
        path = filedialog.askdirectory(title="Selecione a pasta .minecraft a importar")
        if not path:
            return
        dialog = ctk.CTkInputDialog(
            text=f"Nome para o perfil\n({os.path.basename(path)}):",
            title="Importar Pasta",
        )
        name = dialog.get_input()
        if not name:
            return
        try:
            import_profile(name, path)
            self._profiles_log_msg(f"✔  Pasta importada como perfil '{name}'.")
            self._profiles_refresh()
        except Exception as e:
            messagebox.showerror("Erro", str(e))

    def _profiles_activate(self, name: str):
        confirm = messagebox.askyesno(
            "Ativar Perfil",
            f"Ativar o perfil '{name}'?\n\n"
            "O .minecraft atual será desconectado e substituído por este perfil.\n"
            "Feche o Minecraft antes de continuar.",
        )
        if not confirm:
            return
        self._profiles_log.clear()

        def run():
            try:
                activate_profile(name, log=lambda m: self.after(0, lambda m=m: self._profiles_log_msg(m)))
                self.after(0, self._profiles_refresh)
            except Exception as e:
                self.after(0, lambda e=e: messagebox.showerror("Erro", str(e)))

        threading.Thread(target=run, daemon=True).start()

    def _profiles_rename(self, name: str):
        dialog = ctk.CTkInputDialog(text=f"Novo nome para '{name}':", title="Renomear Perfil")
        new_name = dialog.get_input()
        if not new_name or new_name == name:
            return
        try:
            rename_profile(name, new_name)
            self._profiles_log_msg(f"✔  '{name}' renomeado para '{new_name}'.")
            self._profiles_refresh()
        except Exception as e:
            messagebox.showerror("Erro", str(e))

    def _profiles_delete(self, name: str):
        confirm = messagebox.askyesno(
            "Deletar Perfil",
            f"Deletar permanentemente o perfil '{name}'?\n\nEsta ação não pode ser desfeita.",
        )
        if not confirm:
            return
        try:
            delete_profile(name)
            self._profiles_log_msg(f"✔  Perfil '{name}' deletado.")
            self._profiles_refresh()
        except Exception as e:
            messagebox.showerror("Erro", str(e))

    def _profiles_launch(self, name: str, mc_version: str, loader: str):
        """Activate the profile (if needed) then launch the Minecraft Launcher."""
        self._profiles_log.clear()

        def run():
            try:
                if get_active_profile() != name:
                    self.after(0, lambda: self._profiles_log.append(
                        f"  Ativando perfil '{name}'..."
                    ))
                    activate_profile(
                        name,
                        log=lambda m: self.after(0, lambda m=m: self._profiles_log.append(m)),
                    )
                    self.after(0, self._profiles_refresh)
            except Exception as e:
                self.after(0, lambda e=e: messagebox.showerror("Erro", str(e)))
                return

            self.after(0, lambda: self._do_launch(
                mc_version, loader,
                log=lambda m: self.after(0, lambda m=m: self._profiles_log.append(m)),
            ))

        threading.Thread(target=run, daemon=True).start()

    def _do_launch(self, mc_version: str, loader: str | None, log=None):
        """Find launcher + profile, handle 'not found' with browse dialog."""
        def run():
            try:
                profile_id = find_profile_for_version(mc_version, loader)
                if log:
                    log(f"  Perfil detectado: {profile_id or '(padrão)'}")
                launch_minecraft(profile_id=profile_id, log=log)
                if log:
                    log("  ✔  Minecraft iniciado!")
            except FileNotFoundError:
                self.after(0, lambda: self._ask_launcher_path(mc_version, loader, log))
            except Exception as e:
                self.after(0, lambda e=e: messagebox.showerror("Erro ao iniciar", str(e)))

        threading.Thread(target=run, daemon=True).start()

    def _ask_launcher_path(self, mc_version: str, loader: str | None, log=None):
        """Show a dialog asking the user to locate MinecraftLauncher.exe."""
        answer = messagebox.askyesno(
            "Launcher não encontrado",
            "O Minecraft Launcher não foi encontrado automaticamente.\n\n"
            "Deseja localizar o MinecraftLauncher.exe manualmente?",
        )
        if not answer:
            return
        path = filedialog.askopenfilename(
            title="Localizar Minecraft Launcher",
            filetypes=[("Executável", "*.exe"), ("Todos os arquivos", "*.*")],
            initialdir=os.path.expandvars(r"%PROGRAMFILES(X86)%"),
        )
        if not path:
            return
        save_custom_launcher_path(path)
        if log:
            log(f"  Launcher salvo: {path}")
        self._do_launch(mc_version, loader, log=log)

    # ── panel: mod loader ────────────────────────────────────────────────

    def _mk_loader(self, parent) -> ctk.CTkFrame:
        f = ctk.CTkFrame(parent, fg_color="transparent")
        self._loader_type: str | None = None
        self._loader_cards: list[Card] = []
        self._loader_pending_mc: str | None = None  # auto-select after fetch

        SectionLabel(f, "🔧  Instalar Mod Loader").pack(anchor="w")
        ctk.CTkLabel(f, text="│  Baixa e instala Fabric, Forge, NeoForge ou Quilt automaticamente",
                     font=FONT_MONO, text_color=C_MUTED, anchor="w").pack(anchor="w")
        ctk.CTkLabel(f, text=_section_end(), font=FONT_MONO,
                     text_color=C_DIM, anchor="w").pack(anchor="w", pady=(0, 6))

        # ── scrollable form ───────────────────────────────────────────
        scroll = ctk.CTkScrollableFrame(f, fg_color="transparent")
        scroll.pack(fill="both", expand=True)

        # ── 1. Loader type cards ──────────────────────────────────────
        ctk.CTkLabel(scroll, text="  1 · ESCOLHA O LOADER", font=("Consolas", 9),
                     text_color=C_DIM, anchor="w").pack(anchor="w", pady=(0, 4))

        loaders_info = [
            ("fabric",   "Fabric",    "O mais popular para mods modernos.\nRápido, leve e amplamente suportado."),
            ("forge",    "Forge",     "O mais antigo e compatível.\nNecessário para mods mais clássicos."),
            ("neoforge", "NeoForge",  "Fork moderno do Forge.\nMelhor performance e API atualizada."),
            ("quilt",    "Quilt",     "Fork do Fabric com mais funcionalidades.\nCompatível com mods Fabric."),
        ]

        cards_row = ctk.CTkFrame(scroll, fg_color="transparent")
        cards_row.pack(fill="x", pady=(0, 8))
        cards_row.columnconfigure(0, weight=1)
        cards_row.columnconfigure(1, weight=1)

        for i, (key, name, desc) in enumerate(loaders_info):
            r, c = divmod(i, 2)
            card = Card(cards_row, name, desc)
            card.grid(row=r, column=c, padx=5, pady=5, sticky="nsew")
            card._loader_key = key
            card.set_cb(lambda c=card: self._loader_select_type(c))
            self._loader_cards.append(card)

        Divider(scroll).pack(fill="x", pady=6)

        # ── 2. MC version tree ────────────────────────────────────────
        mc_hdr = ctk.CTkFrame(scroll, fg_color="transparent")
        mc_hdr.pack(fill="x", pady=(0, 4))
        ctk.CTkLabel(mc_hdr, text="  2 · VERSÃO DO MINECRAFT", font=("Consolas", 9),
                     text_color=C_DIM, anchor="w").pack(side="left")
        self._loader_mc_fetch_btn = ctk.CTkButton(
            mc_hdr, text="↻ Carregar versões", width=140, height=24,
            font=("Consolas", 10), fg_color=C_SURF2, hover_color=C_BORDER,
            state="disabled", command=self._loader_fetch_mc_versions,
        )
        self._loader_mc_fetch_btn.pack(side="right")

        self._loader_mc_tree = VersionTreeWidget(
            scroll, on_select=self._loader_on_mc_change, height=200,
        )
        self._loader_mc_tree.pack(fill="x", pady=(0, 6))

        Divider(scroll).pack(fill="x", pady=6)

        # ── 3. Loader version combo ───────────────────────────────────
        ctk.CTkLabel(scroll, text="  3 · VERSÃO DO LOADER", font=("Consolas", 9),
                     text_color=C_DIM, anchor="w").pack(anchor="w", pady=(0, 4))

        self._loader_ver_combo = ctk.CTkComboBox(
            scroll, values=["— selecione a versão MC primeiro —"],
            font=("Consolas", 10), dropdown_font=("Consolas", 10),
            fg_color=C_SURF, border_color=C_BORDER,
            button_color=C_SURF2, button_hover_color=C_BORDER,
            width=300, state="disabled",
        )
        self._loader_ver_combo.pack(anchor="w", pady=(0, 4))

        # ── fixed action bar ──────────────────────────────────────────
        Divider(f).pack(fill="x", pady=(6, 4))
        self._loader_prog = BlockProgress(f)
        self._loader_prog.pack(anchor="w", padx=2)
        self._loader_log = LogBox(f, height=80)
        self._loader_log.pack(fill="x", pady=(2, 4))

        btn_row = ctk.CTkFrame(f, fg_color="transparent")
        btn_row.pack(fill="x")
        btn_row.columnconfigure(0, weight=3)
        btn_row.columnconfigure(1, weight=2)

        ctk.CTkButton(btn_row, text="▶  Instalar Loader", font=FONT_MONO_B,
                      fg_color=C_GREEN2, hover_color=C_GREEN, height=36,
                      command=self._loader_run).grid(row=0, column=0, sticky="ew", padx=(0, 4))

        self._loader_launch_btn = ctk.CTkButton(
            btn_row, text="🎮  Lançar Minecraft", font=FONT_MONO,
            fg_color=C_SURF2, hover_color=C_BORDER, height=36,
            command=self._loader_launch,
        )
        self._loader_launch_btn.grid(row=0, column=1, sticky="ew")

        return f

    def _loader_select_type(self, chosen: Card):
        for c in self._loader_cards:
            c.select(c is chosen)
        self._loader_type = chosen._loader_key
        # Reset downstream widgets
        self._loader_mc_tree.clear()
        self._loader_ver_combo.configure(
            values=["— selecione a versão MC primeiro —"], state="disabled"
        )
        self._loader_ver_combo.set("— selecione a versão MC primeiro —")
        self._loader_mc_fetch_btn.configure(state="normal")
        self._loader_log.clear()
        self._loader_log.append(
            f"  Loader: {chosen._loader_key.capitalize()}  ·  clique em ↻ Carregar versões"
        )

    def _loader_fetch_mc_versions(self):
        if not self._loader_type:
            return
        self._loader_mc_fetch_btn.configure(state="disabled", text="↻ Buscando...")
        self._loader_log.append(f"  Buscando versões MC para {self._loader_type.capitalize()}...")

        def run():
            try:
                versions = fetch_mc_versions(self._loader_type)
                self.after(0, lambda: self._loader_mc_tree.load(versions))
                self.after(0, lambda: self._loader_log.append(
                    f"  ✔  {len(versions)} versões MC disponíveis — expanda um grupo para selecionar."
                ))
                # Auto-select if requested (e.g. from scanner panel)
                pending = self._loader_pending_mc
                if pending:
                    self._loader_pending_mc = None
                    self.after(100, lambda: self._loader_auto_select_mc(pending, versions))
            except Exception as e:
                self.after(0, lambda e=e: self._loader_log.append(f"  ❌  {e}"))
            finally:
                self.after(0, lambda: self._loader_mc_fetch_btn.configure(
                    state="normal", text="↻ Carregar versões"
                ))

        threading.Thread(target=run, daemon=True).start()

    def _loader_auto_select_mc(self, target: str, versions: list[str]):
        """Find the best match for target in versions and select it in the tree."""
        # Try exact match first, then major.minor prefix
        best = next((v for v in versions if v == target), None)
        if not best:
            best = next((v for v in versions if v.startswith(target + ".")), None)
        if not best:
            # target like "1.21" → find "1.21.1", "1.21.4" etc.
            candidates = [v for v in versions if v.startswith(target)]
            best = candidates[0] if candidates else None
        if best:
            self._loader_mc_tree._select_version_auto(best)
            self._loader_log.append(f"  ✔  Versão MC auto-selecionada: {best}")
            self._loader_on_mc_change(best)

    def _loader_on_mc_change(self, mc_version: str):
        if not mc_version or not self._loader_type:
            return
        self._loader_log.append(f"  MC {mc_version} selecionado — buscando versões do loader...")
        self._loader_ver_combo.configure(
            values=["↻ buscando..."], state="disabled"
        )
        self._loader_ver_combo.set("↻ buscando...")

        def run():
            try:
                versions = fetch_loader_versions(self._loader_type, mc_version)
                self.after(0, lambda: self._loader_ver_combo.configure(
                    values=versions if versions else ["nenhuma versão encontrada"],
                    state="normal" if versions else "disabled",
                ))
                self.after(0, lambda: self._loader_ver_combo.set(
                    versions[0] if versions else "nenhuma versão encontrada"
                ))
                self.after(0, lambda: self._loader_log.append(
                    f"  ✔  {len(versions)} versões do loader disponíveis."
                ))
            except Exception as e:
                self.after(0, lambda e=e: self._loader_log.append(f"  ❌ {e}"))

        threading.Thread(target=run, daemon=True).start()

    def _loader_run(self):
        if not self._loader_type:
            messagebox.showwarning("Atenção", "Selecione um loader primeiro.")
            return
        mc_ver = self._loader_mc_tree.get()
        ldr_ver = self._loader_ver_combo.get()
        if not mc_ver:
            messagebox.showwarning("Atenção", "Selecione a versão do Minecraft.")
            return
        if not ldr_ver or ldr_ver.startswith("—") or ldr_ver.startswith("↻"):
            messagebox.showwarning("Atenção", "Selecione a versão do loader.")
            return

        self._loader_log.clear()
        self._loader_prog.set(0)

        def run():
            try:
                install_loader(
                    self._loader_type, mc_ver, ldr_ver,
                    log=lambda m: self.after(0, lambda m=m: self._loader_log.append(m)),
                    progress=lambda v: self.after(0, lambda v=v: self._loader_prog.set(v)),
                )
                self.after(0, lambda: self._loader_prog.set(1.0))
            except Exception as e:
                self.after(0, lambda e=e: self._loader_log.append(f"❌  {e}"))

        threading.Thread(target=run, daemon=True).start()

    def _loader_launch(self):
        mc_ver = self._loader_mc_tree.get()
        self._loader_log.clear()
        self._do_launch(mc_ver or "", self._loader_type,
                        log=lambda m: self.after(0, lambda m=m: self._loader_log.append(m)))

    # ── panel: mod scanner ───────────────────────────────────────────────

    def _mk_scanner(self, parent) -> ctk.CTkFrame:
        f = ctk.CTkFrame(parent, fg_color="transparent")
        self._scanner_mods_dir = ctk.StringVar(
            value=str(os.path.join(_get_mc_dir(), "mods"))
        )

        SectionLabel(f, "🔍  Analisar Mods").pack(anchor="w")
        ctk.CTkLabel(f, text="│  Detecta loader, versão MC e incompatibilidades em cada JAR",
                     font=FONT_MONO, text_color=C_MUTED, anchor="w").pack(anchor="w")
        ctk.CTkLabel(f, text=_section_end(), font=FONT_MONO,
                     text_color=C_DIM, anchor="w").pack(anchor="w", pady=(0, 6))

        # ── source row ────────────────────────────────────────────────
        src_row = ctk.CTkFrame(f, fg_color="transparent")
        src_row.pack(fill="x", pady=(0, 6))
        ctk.CTkLabel(src_row, text="  Pasta mods →", font=FONT_MONO,
                     text_color=C_DIM, anchor="w", width=110).pack(side="left")
        ctk.CTkEntry(src_row, textvariable=self._scanner_mods_dir,
                     font=("Consolas", 10), fg_color=C_SURF,
                     border_color=C_BORDER).pack(side="left", fill="x", expand=True, padx=6)
        ctk.CTkButton(src_row, text="📁", width=34, height=28, font=FONT_MONO,
                      fg_color=C_SURF2, hover_color=C_BORDER,
                      command=self._scanner_browse).pack(side="left", padx=2)
        ctk.CTkButton(src_row, text="🔍  Analisar", width=110, height=28,
                      font=FONT_MONO, fg_color=C_GREEN2, hover_color=C_GREEN,
                      command=self._scanner_run).pack(side="left", padx=(6, 0))

        Divider(f).pack(fill="x", pady=6)

        # ── result summary ────────────────────────────────────────────
        self._scanner_summary = ctk.CTkFrame(f, fg_color=C_SURF,
                                             corner_radius=6, border_width=1,
                                             border_color=C_BORDER)
        self._scanner_summary.pack(fill="x", pady=(0, 6))
        self._scanner_summary_lbl = ctk.CTkLabel(
            self._scanner_summary,
            text="  Clique em 🔍 Analisar para escanear a pasta mods/",
            font=FONT_MONO, text_color=C_MUTED, anchor="w", justify="left",
        )
        self._scanner_summary_lbl.pack(anchor="w", padx=14, pady=10)

        # ── scrollable issues list ────────────────────────────────────
        ctk.CTkLabel(f, text="  DETALHES", font=("Consolas", 9),
                     text_color=C_DIM, anchor="w").pack(anchor="w", pady=(4, 2))
        self._scanner_list = ctk.CTkScrollableFrame(f, fg_color="transparent", height=200)
        self._scanner_list.pack(fill="both", expand=True)

        # ── action bar ────────────────────────────────────────────────
        Divider(f).pack(fill="x", pady=(6, 4))
        self._scanner_prog = BlockProgress(f)
        self._scanner_prog.pack(anchor="w", padx=2)
        self._scanner_log = LogBox(f, height=70)
        self._scanner_log.pack(fill="x", pady=(2, 4))
        self._scanner_auto_btn = ctk.CTkButton(
            f, text="🔧  Auto-configurar Loader com versão detectada",
            font=FONT_MONO_B, fg_color=C_SURF2, hover_color=C_BORDER, height=36,
            state="disabled", command=self._scanner_auto_configure,
        )
        self._scanner_auto_btn.pack(fill="x")

        self._scanner_result = None  # holds last ScanResult
        return f

    def _scanner_browse(self):
        path = filedialog.askdirectory(title="Selecione a pasta mods/")
        if path:
            self._scanner_mods_dir.set(path)

    def _scanner_run(self):
        mods_dir = self._scanner_mods_dir.get().strip()
        if not mods_dir:
            messagebox.showwarning("Atenção", "Informe o caminho da pasta mods/.")
            return

        self._scanner_log.clear()
        self._scanner_prog.set(0)
        self._scanner_auto_btn.configure(state="disabled")
        self._scanner_result = None
        # Clear issues list
        for w in self._scanner_list.winfo_children():
            w.destroy()
        self._scanner_summary_lbl.configure(text="  Escaneando...", text_color=C_MUTED)

        def run():
            result = scan_mods_dir(mods_dir)
            self.after(0, lambda: self._scanner_show(result))

        threading.Thread(target=run, daemon=True).start()

    def _scanner_show(self, result):
        self._scanner_result = result
        self._scanner_prog.set(1.0)

        # ── summary ───────────────────────────────────────────────────
        total = len(result.mods)
        loader = result.recommended_loader or "?"
        mc = result.recommended_mc or "?"
        votes = result.loader_votes
        mc_votes = result.mc_votes

        incompat = result.incompatible_mods()
        missing = result.missing_deps()

        status_icon = "✔" if not incompat and not missing else "⚠"
        status_col = C_GREEN if not incompat and not missing else C_YELLOW

        votes_str = "  ".join(f"{l.capitalize()}: {n}" for l, n in votes.items())
        mc_str = "  ".join(f"{v}: {n}" for v, n in sorted(mc_votes.items(), reverse=True)[:4])

        summary = (
            f"  {status_icon}  {total} mods escaneados\n"
            f"  Loader recomendado:  {loader.upper()}\n"
            f"  Versão MC detectada: {mc}\n"
            f"  Por loader:  {votes_str or '—'}\n"
            f"  Por versão:  {mc_str or '—'}"
        )
        self._scanner_summary_lbl.configure(text=summary, text_color=status_col)

        self._scanner_log.append(f"  {total} mods lidos  ·  {len(result.errors)} erros")

        # ── issues list ───────────────────────────────────────────────
        for w in self._scanner_list.winfo_children():
            w.destroy()

        def _row(icon, text, color):
            ctk.CTkLabel(
                self._scanner_list,
                text=f"  {icon}  {text}",
                font=("Consolas", 10), text_color=color, anchor="w",
            ).pack(anchor="w", pady=1)

        if incompat:
            ctk.CTkLabel(self._scanner_list,
                         text="  INCOMPATÍVEIS", font=("Consolas", 9),
                         text_color=C_DIM, anchor="w").pack(anchor="w", pady=(6, 2))
            for mod, reason in incompat:
                _row("❌", f"{mod.filename}  →  {reason}", C_RED)

        if missing:
            ctk.CTkLabel(self._scanner_list,
                         text="  DEPENDÊNCIAS FALTANDO", font=("Consolas", 9),
                         text_color=C_DIM, anchor="w").pack(anchor="w", pady=(10, 2))
            seen = set()
            for mod, dep_id, dep_ver in missing:
                key = f"{dep_id}@{dep_ver}"
                if key not in seen:
                    seen.add(key)
                    _row("⚠", f"{dep_id}  {dep_ver}  (exigido por {mod.name})", C_YELLOW)

        if result.errors:
            ctk.CTkLabel(self._scanner_list,
                         text="  ERROS DE LEITURA", font=("Consolas", 9),
                         text_color=C_DIM, anchor="w").pack(anchor="w", pady=(10, 2))
            for err in result.errors:
                _row("·", err, C_DIM)

        if not incompat and not missing and not result.errors:
            _row("✔", "Nenhum problema detectado!", C_GREEN)

        # Enable auto-configure if we have a recommendation
        if result.recommended_loader and result.recommended_mc:
            self._scanner_auto_btn.configure(
                state="normal",
                text=f"🔧  Auto-configurar: {result.recommended_loader.capitalize()} "
                     f"para MC {result.recommended_mc}",
            )

    def _scanner_auto_configure(self):
        result = self._scanner_result
        if not result or not result.recommended_loader or not result.recommended_mc:
            return

        loader = result.recommended_loader
        mc_target = result.recommended_mc

        self._scanner_log.append(
            f"  Configurando Loader: {loader.capitalize()} · MC {mc_target}..."
        )

        # 1. Switch to loader panel
        self._switch("loader")

        # 2. Find and click the matching loader card
        target_card = next(
            (c for c in self._loader_cards if c._loader_key == loader), None
        )
        if target_card:
            self._loader_select_type(target_card)
        else:
            messagebox.showwarning(
                "Loader não suportado",
                f"O loader '{loader}' detectado não está disponível no instalador.",
            )
            return

        # 3. Store pending MC version and trigger fetch
        self._loader_pending_mc = mc_target
        self._loader_log.append(
            f"  ↻ Buscando versões MC — auto-selecionará {mc_target}..."
        )
        self._loader_fetch_mc_versions()

    # ── panel: copy files ────────────────────────────────────────────────

    def _mk_copy(self, parent) -> ctk.CTkFrame:
        f = ctk.CTkFrame(parent, fg_color="transparent")

        # ── header (fixed) ────────────────────────────────────────────
        SectionLabel(f, "📦  Copiar Arquivos").pack(anchor="w")
        ctk.CTkLabel(f, text="│  Instale mods, configs e saves de um ZIP ou pasta .minecraft",
                     font=FONT_MONO, text_color=C_MUTED, anchor="w").pack(anchor="w")
        ctk.CTkLabel(f, text=_section_end(), font=FONT_MONO,
                     text_color=C_DIM, anchor="w").pack(anchor="w", pady=(0, 6))

        # ── scrollable form ───────────────────────────────────────────
        scroll = ctk.CTkScrollableFrame(f, fg_color="transparent")
        scroll.pack(fill="both", expand=True)

        # Source picker (unified: file or folder)
        ctk.CTkLabel(scroll, text="  FONTE", font=("Consolas", 9),
                     text_color=C_DIM, anchor="w").pack(anchor="w", pady=(0, 4))

        pick_row = ctk.CTkFrame(scroll, fg_color=C_SURF, corner_radius=6)
        pick_row.pack(fill="x", pady=(0, 4))

        self._cp_src_lbl = ctk.CTkLabel(pick_row, text="  nenhum arquivo/pasta selecionado",
                                        font=FONT_MONO, text_color=C_DIM, anchor="w")
        self._cp_src_lbl.pack(side="left", padx=4, pady=8, fill="x", expand=True)

        ctk.CTkButton(pick_row, text="📁 Pasta", width=90, height=28,
                      font=FONT_MONO, fg_color=C_SURF2, hover_color=C_BORDER,
                      command=self._cp_pick_folder).pack(side="right", padx=(0, 8), pady=8)
        ctk.CTkButton(pick_row, text="📦 Arquivo", width=100, height=28,
                      font=FONT_MONO, fg_color=C_SURF2, hover_color=C_BORDER,
                      command=self._cp_pick_file).pack(side="right", padx=4, pady=8)

        self._cp_detect_lbl = ctk.CTkLabel(scroll, text="", font=FONT_MONO,
                                           text_color=C_MUTED, anchor="w")
        self._cp_detect_lbl.pack(anchor="w", pady=(2, 4))

        # Checkboxes
        Divider(scroll).pack(fill="x", pady=4)
        ctk.CTkLabel(scroll, text="  SELECIONE O CONTEÚDO", font=("Consolas", 9),
                     text_color=C_DIM, anchor="w").pack(anchor="w", pady=(0, 4))

        items = [
            ("mods",          "mods/",          "Arquivos .jar dos mods"),
            ("config",        "config/",         "Configurações dos mods"),
            ("resourcepacks", "resourcepacks/",  "Texturas e pacotes de recursos"),
            ("shaderpacks",   "shaderpacks/",    "Shaders extras"),
            ("saves",         "saves/",          "Mundos salvos"),
        ]
        cb_grid = ctk.CTkFrame(scroll, fg_color="transparent")
        cb_grid.pack(fill="x")
        cb_grid.columnconfigure(0, weight=1)
        cb_grid.columnconfigure(1, weight=1)

        for i, (key, label, desc) in enumerate(items):
            r, c = divmod(i, 2)
            row = ctk.CTkFrame(cb_grid, fg_color=C_SURF, corner_radius=4)
            row.grid(row=r, column=c, padx=4, pady=3, sticky="ew")
            ctk.CTkCheckBox(row, text=f"  {label}", variable=self._content_vars[key],
                            font=FONT_MONO_B, text_color=C_TEXT,
                            fg_color=C_GREEN, hover_color=C_GREEN2,
                            checkmark_color=C_BG, width=24).pack(side="left", padx=10, pady=8)
            ctk.CTkLabel(row, text=desc, font=("Consolas", 10),
                         text_color=C_DIM).pack(side="left")

        # Clean option
        Divider(scroll).pack(fill="x", pady=6)
        self._cp_clean_var = ctk.BooleanVar(value=True)
        clean_row = ctk.CTkFrame(scroll, fg_color=C_SURF, corner_radius=4)
        clean_row.pack(fill="x", pady=(0, 6))
        ctk.CTkCheckBox(clean_row, text="  Limpar pastas antes de copiar",
                        variable=self._cp_clean_var,
                        font=FONT_MONO_B, text_color=C_YELLOW,
                        fg_color=C_YELLOW, hover_color=C_DIM,
                        checkmark_color=C_BG, width=24).pack(side="left", padx=10, pady=8)
        ctk.CTkLabel(clean_row,
                     text="Remove mods/configs antigos antes de instalar os novos",
                     font=("Consolas", 10), text_color=C_DIM).pack(side="left")

        # Destination
        dest_row = ctk.CTkFrame(scroll, fg_color="transparent")
        dest_row.pack(fill="x")
        ctk.CTkLabel(dest_row, text="  destino  →", font=FONT_MONO,
                     text_color=C_DIM).pack(side="left")
        ctk.CTkEntry(dest_row, textvariable=self._dest, font=("Consolas", 10),
                     fg_color=C_SURF, border_color=C_BORDER, width=430).pack(side="left", padx=8)
        ctk.CTkButton(dest_row, text="…", width=32, height=26, font=FONT_MONO,
                      fg_color=C_SURF2, command=self._pick_dest).pack(side="left")

        # ── fixed action bar (always visible) ─────────────────────────
        Divider(f).pack(fill="x", pady=(6, 4))

        self._cp_prog = BlockProgress(f)
        self._cp_prog.pack(anchor="w", padx=2)

        self._cp_log = LogBox(f, height=56)
        self._cp_log.pack(fill="x", pady=(2, 4))

        ctk.CTkButton(f, text="▶  Instalar Arquivos", font=FONT_MONO_B,
                      fg_color=C_GREEN2, hover_color=C_GREEN, height=36,
                      command=self._cp_run).pack(fill="x")

        return f

    def _cp_pick_file(self):
        path = filedialog.askopenfilename(
            title="Selecionar modpack",
            filetypes=[
                ("Arquivos de modpack", "*.zip *.rar *.7z"),
                ("ZIP", "*.zip"),
                ("RAR", "*.rar"),
                ("7-Zip", "*.7z"),
                ("Todos os arquivos", "*.*"),
            ],
        )
        if path:
            self._src_type.set("zip")
            self._cp_set_source(path, "zip")

    def _cp_pick_folder(self):
        path = filedialog.askdirectory(title="Selecionar pasta .minecraft")
        if path:
            self._src_type.set("folder")
            self._cp_set_source(path, "folder")

    def _cp_set_source(self, path: str, src_type: str):
        self._src_path = path
        short = os.path.basename(path) or path
        ext = os.path.splitext(path)[1].upper() or "PASTA"
        self._cp_src_lbl.configure(
            text=f"  [{ext.lstrip('.')}]  {short}", text_color=C_TEXT
        )
        try:
            info = detect_source(path, src_type)
            self._mc_ver = info["mc_version"]
            self._loader = info["loader"]
            ver = self._mc_ver if self._mc_ver != "unknown" else "?"
            loader = self._loader.capitalize()
            lv = info.get("loader_version", "")
            self._cp_detect_lbl.configure(
                text=f"  ✔  MC {ver}  ·  {loader}{' ' + lv if lv else ''}",
                text_color=C_GREEN)
        except Exception as e:
            self._cp_detect_lbl.configure(text=f"  ⚠  {e}", text_color=C_YELLOW)

    def _cp_run(self):
        if not self._src_path:
            messagebox.showwarning("Atenção", "Selecione a fonte primeiro.")
            return
        self._cp_log.clear()
        self._cp_prog.set(0)
        sel = {k: v.get() for k, v in self._content_vars.items()}

        def run():
            install_modpack(
                self._src_path, self._src_type.get(),
                self._dest.get(), sel,
                preset=None, shader=None, mc_version=self._mc_ver,
                clean_before=self._cp_clean_var.get(),
                progress_callback=lambda v: self.after(0, lambda v=v: self._cp_prog.set(v)),
                log_callback=lambda m: self.after(0, lambda m=m: self._cp_log.append(m)),
            )

        threading.Thread(target=run, daemon=True).start()

    # ── panel: preset ────────────────────────────────────────────────────

    def _mk_preset(self, parent) -> ctk.CTkFrame:
        f = ctk.CTkFrame(parent, fg_color="transparent")

        SectionLabel(f, "⚙️  Preset de Configuração").pack(anchor="w")
        ctk.CTkLabel(f, text="│  Aplica um perfil gráfico ao options.txt sem mover nenhum mod",
                     font=FONT_MONO, text_color=C_MUTED, anchor="w").pack(anchor="w")
        ctk.CTkLabel(f, text=_section_end(), font=FONT_MONO,
                     text_color=C_DIM, anchor="w").pack(anchor="w", pady=(0, 6))

        # ── scrollable form ───────────────────────────────────────────
        scroll = ctk.CTkScrollableFrame(f, fg_color="transparent")
        scroll.pack(fill="both", expand=True)

        grid = ctk.CTkFrame(scroll, fg_color="transparent")
        grid.pack(fill="x")
        grid.columnconfigure(0, weight=1)
        grid.columnconfigure(1, weight=1)

        for i, p in enumerate(get_presets()):
            r, c = divmod(i, 2)
            opts = p["options"]
            detail = (f"Render {opts.get('renderDistance','?')}ch  ·  "
                      f"FPS {opts.get('maxFps','?')}  ·  γ {opts.get('gamma','1.0')}")
            card = Card(grid, f"{p['icon']}  {p['name']}", p["description"], detail)
            card.grid(row=r, column=c, padx=5, pady=5, sticky="nsew")
            card._preset = p
            card.set_cb(lambda c=card: self._sel_preset_standalone(c))
            self._preset_cards.append(card)

        Divider(scroll).pack(fill="x", pady=6)
        dest_row = ctk.CTkFrame(scroll, fg_color="transparent")
        dest_row.pack(fill="x")
        ctk.CTkLabel(dest_row, text="  destino  →", font=FONT_MONO,
                     text_color=C_DIM).pack(side="left")
        ctk.CTkEntry(dest_row, textvariable=self._dest, font=("Consolas", 10),
                     fg_color=C_SURF, border_color=C_BORDER, width=430).pack(side="left", padx=8)
        ctk.CTkButton(dest_row, text="…", width=32, height=26,
                      fg_color=C_SURF2, command=self._pick_dest).pack(side="left")

        # ── fixed action bar ──────────────────────────────────────────
        Divider(f).pack(fill="x", pady=(6, 4))
        self._preset_prog = BlockProgress(f)
        self._preset_prog.pack(anchor="w", padx=2)
        self._preset_log = LogBox(f, height=56)
        self._preset_log.pack(fill="x", pady=(2, 4))
        ctk.CTkButton(f, text="▶  Aplicar Preset", font=FONT_MONO_B,
                      fg_color=C_GREEN2, hover_color=C_GREEN, height=36,
                      command=self._preset_run).pack(fill="x")

        return f

    def _sel_preset_standalone(self, chosen: Card):
        for c in self._preset_cards:
            c.select(c is chosen)
        self._preset = chosen._preset

    def _preset_run(self):
        if not self._preset:
            messagebox.showwarning("Atenção", "Selecione um preset primeiro.")
            return
        self._preset_log.clear()
        self._preset_prog.set(0)

        def run():
            try:
                from core.presets import write_options_txt
                write_options_txt(self._dest.get(), self._preset, self._mc_ver)
                self.after(0, lambda: self._preset_log.append("✔  options.txt atualizado."))
                self.after(0, lambda: self._preset_prog.set(1.0))
            except Exception as e:
                self.after(0, lambda e=e: self._preset_log.append(f"❌  {e}"))

        threading.Thread(target=run, daemon=True).start()

    # ── panel: shader ────────────────────────────────────────────────────

    def _mk_shader(self, parent) -> ctk.CTkFrame:
        f = ctk.CTkFrame(parent, fg_color="transparent")

        SectionLabel(f, "✨  Shader").pack(anchor="w")
        ctk.CTkLabel(f, text="│  Copia e ativa um shader no seu .minecraft",
                     font=FONT_MONO, text_color=C_MUTED, anchor="w").pack(anchor="w")
        ctk.CTkLabel(f, text=_section_end(), font=FONT_MONO,
                     text_color=C_DIM, anchor="w").pack(anchor="w", pady=(0, 6))

        # ── scrollable form ───────────────────────────────────────────
        scroll = ctk.CTkScrollableFrame(f, fg_color="transparent")
        scroll.pack(fill="both", expand=True)

        mc_ver_row = ctk.CTkFrame(scroll, fg_color="transparent")
        mc_ver_row.pack(fill="x", pady=(0, 6))
        ctk.CTkLabel(mc_ver_row, text="  versão MC  →", font=FONT_MONO,
                     text_color=C_DIM).pack(side="left")
        self._shader_ver_combo = ctk.CTkComboBox(
            mc_ver_row,
            values=COMMON_MC_VERSIONS,
            font=("Consolas", 10),
            dropdown_font=("Consolas", 10),
            fg_color=C_SURF,
            border_color=C_BORDER,
            button_color=C_SURF2,
            button_hover_color=C_BORDER,
            width=150,
            command=lambda v: self._populate_shaders(
                self._shader_grid_main,
                None if v == "Todos" else v,
            ),
        )
        self._shader_ver_combo.pack(side="left", padx=8)
        self._shader_ver_combo.set("Todos")

        self._shader_grid_main = ctk.CTkFrame(scroll, fg_color="transparent")
        self._shader_grid_main.pack(fill="x")

        Divider(scroll).pack(fill="x", pady=6)
        dest_row = ctk.CTkFrame(scroll, fg_color="transparent")
        dest_row.pack(fill="x")
        ctk.CTkLabel(dest_row, text="  destino  →", font=FONT_MONO,
                     text_color=C_DIM).pack(side="left")
        ctk.CTkEntry(dest_row, textvariable=self._dest, font=("Consolas", 10),
                     fg_color=C_SURF, border_color=C_BORDER, width=430).pack(side="left", padx=8)
        ctk.CTkButton(dest_row, text="…", width=32, height=26,
                      fg_color=C_SURF2, command=self._pick_dest).pack(side="left")

        # ── fixed action bar ──────────────────────────────────────────
        Divider(f).pack(fill="x", pady=(6, 4))
        self._shader_prog = BlockProgress(f)
        self._shader_prog.pack(anchor="w", padx=2)
        self._shader_log = LogBox(f, height=56)
        self._shader_log.pack(fill="x", pady=(2, 4))
        ctk.CTkButton(f, text="▶  Instalar Shader", font=FONT_MONO_B,
                      fg_color=C_GREEN2, hover_color=C_GREEN, height=36,
                      command=self._shader_run).pack(fill="x")

        return f

    def _populate_shaders(self, grid: ctk.CTkFrame, version: str = "unknown"):
        for w in grid.winfo_children():
            w.destroy()
        self._shader_cards.clear()

        all_s = get_shaders()
        avail = filter_shaders_for_version(all_s, version) if version != "unknown" else all_s

        grid.columnconfigure(0, weight=1)
        grid.columnconfigure(1, weight=1)

        tier = {"none": "—", "low": "Leve", "medium": "Médio", "high": "Pesado"}
        for i, s in enumerate(avail):
            r, c = divmod(i, 2)
            req = []
            if s.get("requires_iris"): req.append("Iris")
            if s.get("requires_optifine"): req.append("OptiFine")
            detail = f"{tier.get(s.get('tier',''), '')}  ·  {' / '.join(req)}" if req else ""
            card = Card(grid, s["name"], s["description"], detail)
            card.grid(row=r, column=c, padx=5, pady=5, sticky="nsew")
            card._shader = s
            card.set_cb(lambda c=card: self._sel_shader(c))
            self._shader_cards.append(card)

        if self._shader_cards:
            self._sel_shader(self._shader_cards[0])

    def _sel_shader(self, chosen: Card):
        for c in self._shader_cards:
            c.select(c is chosen)
        self._shader = chosen._shader

    def _shader_run(self):
        if not self._shader or self._shader.get("id") == "none":
            messagebox.showinfo("Info", "Nenhum shader selecionado.")
            return
        self._shader_log.clear()
        self._shader_prog.set(0)

        def run():
            from core.copier import _install_shader
            try:
                _install_shader(self._shader, self._dest.get(),
                                lambda m: self.after(0, lambda m=m: self._shader_log.append(m)))
                self.after(0, lambda: self._shader_prog.set(1.0))
            except Exception as e:
                self.after(0, lambda e=e: self._shader_log.append(f"❌  {e}"))

        threading.Thread(target=run, daemon=True).start()

    # ── panel: java ──────────────────────────────────────────────────────

    def _mk_java(self, parent) -> ctk.CTkFrame:
        f = ctk.CTkFrame(parent, fg_color="transparent")

        SectionLabel(f, "☕  Java").pack(anchor="w")
        ctk.CTkLabel(f, text="│  Verifica e baixa o Java 21 JRE (Adoptium Temurin)",
                     font=FONT_MONO, text_color=C_MUTED, anchor="w").pack(anchor="w")
        ctk.CTkLabel(f, text=_section_end(), font=FONT_MONO,
                     text_color=C_DIM, anchor="w").pack(anchor="w", pady=(0, 14))

        status_box = ctk.CTkFrame(f, fg_color=C_SURF, corner_radius=6)
        status_box.pack(fill="x", pady=(0, 10))

        java_path = find_java()
        ok = java_path is not None
        self._java_status_lbl = ctk.CTkLabel(
            status_box,
            text=f"  {'✔' if ok else '✖'}  Java  →  {java_path or 'não encontrado'}",
            font=FONT_MONO,
            text_color=C_GREEN if ok else C_RED,
            anchor="w",
        )
        self._java_status_lbl.pack(anchor="w", padx=14, pady=10)

        self._java_prog = BlockProgress(f)
        self._java_prog.pack(anchor="w", pady=(4, 2))
        self._java_log = LogBox(f, height=200)
        self._java_log.pack(fill="both", expand=True)

        ctk.CTkButton(f, text="▶  Verificar / Baixar Java 21", font=FONT_MONO_B,
                      fg_color=C_GREEN2, hover_color=C_GREEN, height=36,
                      command=self._java_run).pack(fill="x", pady=(10, 0))

        return f

    def _update_java_status(self, path: str | None):
        ok = path is not None
        icon = "✔" if ok else "✖"
        color = C_GREEN if ok else C_RED
        display = path or "não encontrado"
        if hasattr(self, "_java_status_lbl"):
            self._java_status_lbl.configure(
                text=f"  {icon}  Java  →  {display}", text_color=color)
        self._java_prog.set(1.0 if ok else 0.0)

    def _hdr_launch(self):
        self._do_launch(self._mc_ver, self._loader, log=None)

    def _java_run(self):
        self._java_log.clear()
        self._java_prog.set(0)
        self._java_status_lbl.configure(
            text="  ⠋  Java  →  verificando...",
            text_color=C_YELLOW,
        )
        ensure_java(
            log_callback=lambda m: self.after(0, lambda m=m: self._java_log.append(m)),
            done_callback=lambda p: self.after(0, lambda p=p: self._update_java_status(p)),
        )

    # ── panel: wizard ────────────────────────────────────────────────────

    def _mk_wizard(self, parent) -> ctk.CTkFrame:
        f = ctk.CTkFrame(parent, fg_color="transparent")

        SectionLabel(f, "🧙  Instalação Completa").pack(anchor="w")
        ctk.CTkLabel(f, text="│  Wizard passo a passo — fonte › preset › shader › conteúdo › instalar",
                     font=FONT_MONO, text_color=C_MUTED, anchor="w").pack(anchor="w")
        ctk.CTkLabel(f, text=_section_end(), font=FONT_MONO,
                     text_color=C_DIM, anchor="w").pack(anchor="w", pady=(0, 8))

        # Step bar
        self._wiz_bar_frame = ctk.CTkFrame(f, fg_color="transparent")
        self._wiz_bar_frame.pack(anchor="w", pady=(0, 10))
        self._wiz_step_labels: list[ctk.CTkLabel] = []
        for i, name in enumerate(WIZARD_STEPS):
            lbl = ctk.CTkLabel(self._wiz_bar_frame, text=f"[{i+1}] {name}",
                               font=("Consolas", 10), text_color=C_DIM)
            lbl.pack(side="left", padx=(0, 12))
            self._wiz_step_labels.append(lbl)
            if i < len(WIZARD_STEPS) - 1:
                ctk.CTkLabel(self._wiz_bar_frame, text="›", font=("Consolas", 10),
                             text_color=C_DIM).pack(side="left", padx=(0, 12))

        Divider(f).pack(fill="x", pady=(0, 10))

        # Content host
        self._wiz_host = ctk.CTkFrame(f, fg_color="transparent")
        self._wiz_host.pack(fill="both", expand=True)

        self._wiz_frames = [
            self._wiz_mk_source(self._wiz_host),
            self._wiz_mk_preset(self._wiz_host),
            self._wiz_mk_shader(self._wiz_host),
            self._wiz_mk_content(self._wiz_host),
            self._wiz_mk_install(self._wiz_host),
        ]

        # Nav buttons
        Divider(f).pack(fill="x", pady=(8, 4))
        nav = ctk.CTkFrame(f, fg_color="transparent")
        nav.pack(fill="x")

        self._wiz_btn_back = ctk.CTkButton(
            nav, text="‹ Voltar", width=110, height=30, font=FONT_MONO,
            fg_color=C_SURF2, hover_color=C_BORDER, command=self._wiz_back)
        self._wiz_btn_back.pack(side="left")

        self._wiz_btn_next = ctk.CTkButton(
            nav, text="Próximo ›", width=110, height=30, font=FONT_MONO,
            fg_color=C_GREEN2, hover_color=C_GREEN, command=self._wiz_next)
        self._wiz_btn_next.pack(side="right")

        self._wiz_show(0)
        return f

    def _wiz_show(self, n: int):
        for i, fr in enumerate(self._wiz_frames):
            if i == n:
                fr.pack(fill="both", expand=True)
            else:
                fr.pack_forget()
        self._wiz_step = n
        for i, lbl in enumerate(self._wiz_step_labels):
            if i < n:
                lbl.configure(text_color=C_DIM)
            elif i == n:
                lbl.configure(text_color=C_GREEN)
            else:
                lbl.configure(text_color=C_DIM)
        self._wiz_btn_back.configure(state="normal" if n > 0 else "disabled")
        last = n == len(WIZARD_STEPS) - 1
        self._wiz_btn_next.configure(
            text="▶  Instalar" if last else "Próximo ›",
            fg_color=C_RED if last else C_GREEN2,
            hover_color="#C62828" if last else C_GREEN,
        )

    def _wiz_back(self):
        if self._wiz_step > 0:
            self._wiz_show(self._wiz_step - 1)

    def _wiz_next(self):
        s = self._wiz_step
        if s == len(WIZARD_STEPS) - 1:
            self._wiz_install()
            return
        if s == 0 and not self._src_path:
            messagebox.showwarning("Atenção", "Selecione a fonte do modpack.")
            return
        if s == 1 and self._preset is None:
            messagebox.showwarning("Atenção", "Selecione um preset.")
            return
        if s + 1 == 2:
            self._populate_shaders(self._wiz_shader_grid, self._mc_ver)
        self._wiz_show(s + 1)

    # wizard sub-steps (share state with standalone panels)

    def _wiz_mk_source(self, host) -> ctk.CTkFrame:
        f = ctk.CTkFrame(host, fg_color="transparent")

        ctk.CTkLabel(f, text="  SELECIONE A FONTE DO MODPACK", font=("Consolas", 9),
                     text_color=C_DIM, anchor="w").pack(anchor="w", pady=(0, 6))

        pick = ctk.CTkFrame(f, fg_color=C_SURF, corner_radius=6)
        pick.pack(fill="x", pady=(0, 4))
        self._wiz_src_lbl = ctk.CTkLabel(pick, text="  nenhum arquivo/pasta selecionado",
                                         font=FONT_MONO, text_color=C_DIM, anchor="w")
        self._wiz_src_lbl.pack(side="left", padx=4, pady=8, fill="x", expand=True)
        ctk.CTkButton(pick, text="📁 Pasta", width=90, height=28, font=FONT_MONO,
                      fg_color=C_SURF2, hover_color=C_BORDER,
                      command=self._wiz_pick_folder).pack(side="right", padx=(0, 8), pady=8)
        ctk.CTkButton(pick, text="📦 Arquivo", width=100, height=28, font=FONT_MONO,
                      fg_color=C_SURF2, hover_color=C_BORDER,
                      command=self._wiz_pick_file).pack(side="right", padx=4, pady=8)

        self._wiz_detect_lbl = ctk.CTkLabel(f, text="", font=FONT_MONO,
                                            text_color=C_MUTED, anchor="w")
        self._wiz_detect_lbl.pack(anchor="w", pady=(4, 0))
        return f

    def _wiz_pick_file(self):
        path = filedialog.askopenfilename(
            title="Selecionar modpack",
            filetypes=[
                ("Modpack", "*.zip *.rar *.7z"),
                ("ZIP", "*.zip"), ("RAR", "*.rar"),
                ("Todos os arquivos", "*.*"),
            ],
        )
        if path:
            self._src_type.set("zip")
            self._wiz_set_source(path, "zip")

    def _wiz_pick_folder(self):
        path = filedialog.askdirectory(title="Selecionar pasta .minecraft")
        if path:
            self._src_type.set("folder")
            self._wiz_set_source(path, "folder")

    def _wiz_set_source(self, path: str, src_type: str):
        self._src_path = path
        ext = os.path.splitext(path)[1].upper().lstrip(".") or "PASTA"
        short = os.path.basename(path) or path
        self._wiz_src_lbl.configure(text=f"  [{ext}]  {short}", text_color=C_TEXT)
        try:
            info = detect_source(path, src_type)
            self._mc_ver = info["mc_version"]
            self._loader = info["loader"]
            ver = self._mc_ver if self._mc_ver != "unknown" else "?"
            lv = info.get("loader_version", "")
            self._wiz_detect_lbl.configure(
                text=f"  ✔  MC {ver}  ·  {self._loader.capitalize()}{' ' + lv if lv else ''}",
                text_color=C_GREEN)
        except Exception as e:
            self._wiz_detect_lbl.configure(text=f"  ⚠  {e}", text_color=C_YELLOW)

    def _wiz_mk_preset(self, host) -> ctk.CTkFrame:
        f = ctk.CTkFrame(host, fg_color="transparent")
        grid = ctk.CTkFrame(f, fg_color="transparent")
        grid.pack(fill="x")
        grid.columnconfigure(0, weight=1)
        grid.columnconfigure(1, weight=1)

        self._wiz_preset_cards: list[Card] = []
        for i, p in enumerate(get_presets()):
            r, c = divmod(i, 2)
            opts = p["options"]
            detail = (f"Render {opts.get('renderDistance','?')}ch  ·  "
                      f"FPS {opts.get('maxFps','?')}  ·  γ {opts.get('gamma','1.0')}")
            card = Card(grid, f"{p['icon']}  {p['name']}", p["description"], detail)
            card.grid(row=r, column=c, padx=5, pady=5, sticky="nsew")
            card._preset = p
            card.set_cb(lambda c=card: self._wiz_sel_preset(c))
            self._wiz_preset_cards.append(card)

        return f

    def _wiz_sel_preset(self, chosen: Card):
        for c in self._wiz_preset_cards:
            c.select(c is chosen)
        self._preset = chosen._preset

    def _wiz_mk_shader(self, host) -> ctk.CTkFrame:
        f = ctk.CTkFrame(host, fg_color="transparent")
        self._wiz_shader_grid = ctk.CTkFrame(f, fg_color="transparent")
        self._wiz_shader_grid.pack(fill="x")
        return f

    def _wiz_mk_content(self, host) -> ctk.CTkFrame:
        f = ctk.CTkFrame(host, fg_color="transparent")
        items = [
            ("mods",          "mods/",          "Arquivos .jar dos mods"),
            ("config",        "config/",         "Configurações dos mods"),
            ("resourcepacks", "resourcepacks/",  "Texturas e pacotes"),
            ("shaderpacks",   "shaderpacks/",    "Shaders extras"),
            ("saves",         "saves/",          "Mundos salvos"),
        ]
        cb_grid = ctk.CTkFrame(f, fg_color="transparent")
        cb_grid.pack(fill="x")
        cb_grid.columnconfigure(0, weight=1)
        cb_grid.columnconfigure(1, weight=1)

        for i, (key, label, desc) in enumerate(items):
            r, c = divmod(i, 2)
            row = ctk.CTkFrame(cb_grid, fg_color=C_SURF, corner_radius=4)
            row.grid(row=r, column=c, padx=4, pady=3, sticky="ew")
            ctk.CTkCheckBox(row, text=f"  {label}", variable=self._content_vars[key],
                            font=FONT_MONO_B, text_color=C_TEXT,
                            fg_color=C_GREEN, hover_color=C_GREEN2,
                            checkmark_color=C_BG, width=24).pack(side="left", padx=10, pady=8)
            ctk.CTkLabel(row, text=desc, font=("Consolas", 10), text_color=C_DIM).pack(side="left")

        Divider(f).pack(fill="x", pady=8)
        dest_row = ctk.CTkFrame(f, fg_color="transparent")
        dest_row.pack(fill="x")
        ctk.CTkLabel(dest_row, text="  destino  →", font=FONT_MONO, text_color=C_DIM).pack(side="left")
        ctk.CTkEntry(dest_row, textvariable=self._dest, font=("Consolas", 10),
                     fg_color=C_SURF, border_color=C_BORDER, width=460).pack(side="left", padx=8)
        ctk.CTkButton(dest_row, text="…", width=32, height=26,
                      fg_color=C_SURF2, command=self._pick_dest).pack(side="left")
        return f

    def _wiz_mk_install(self, host) -> ctk.CTkFrame:
        f = ctk.CTkFrame(host, fg_color="transparent")
        self._wiz_summary = ctk.CTkLabel(f, text="", font=FONT_MONO,
                                         text_color=C_MUTED, anchor="w", wraplength=700)
        self._wiz_summary.pack(anchor="w", pady=(0, 8))
        self._wiz_prog = BlockProgress(f)
        self._wiz_prog.pack(anchor="w", pady=(0, 4))
        self._wiz_log = LogBox(f, height=220)
        self._wiz_log.pack(fill="both", expand=True)
        return f

    def _wiz_install(self):
        preset_name = self._preset["name"] if self._preset else "nenhum"
        shader_name = self._shader["name"] if self._shader else "nenhum"
        sel = [k for k, v in self._content_vars.items() if v.get()]
        self._wiz_summary.configure(
            text=(f"  src: {os.path.basename(self._src_path or '?')}  ·  "
                  f"MC {self._mc_ver}  ·  preset: {preset_name}  ·  "
                  f"shader: {shader_name}  ·  conteúdo: {', '.join(sel) or 'nenhum'}")
        )
        self._wiz_log.clear()
        self._wiz_prog.set(0)
        self._wiz_btn_next.configure(state="disabled")
        self._wiz_btn_back.configure(state="disabled")

        ensure_java(log_callback=lambda m: self.after(0, lambda m=m: self._wiz_log.append(m)))

        def run():
            try:
                install_modpack(
                    self._src_path, self._src_type.get(),
                    self._dest.get(),
                    {k: v.get() for k, v in self._content_vars.items()},
                    preset=self._preset, shader=self._shader,
                    mc_version=self._mc_ver,
                    clean_before=True,
                    progress_callback=lambda v: self.after(0, lambda v=v: self._wiz_prog.set(v)),
                    log_callback=lambda m: self.after(0, lambda m=m: self._wiz_log.append(m)),
                )
                self.after(0, self._wiz_done)
            except Exception as e:
                self.after(0, lambda e=e: self._wiz_error(str(e)))

        threading.Thread(target=run, daemon=True).start()

    def _wiz_done(self):
        self._wiz_prog.set(1.0)
        self._wiz_btn_next.configure(state="normal", text="✔  Fechar",
                                     fg_color=C_GREEN2, command=self.destroy)
        self._wiz_btn_back.configure(state="normal")
        messagebox.showinfo("Concluído", "Modpack instalado com sucesso! 🎮")

    def _wiz_error(self, err: str):
        self._wiz_log.append(f"\n❌  {err}")
        self._wiz_btn_next.configure(state="normal")
        self._wiz_btn_back.configure(state="normal")
        messagebox.showerror("Erro", err)

    # ── panel: clean .minecraft ───────────────────────────────────────────

    def _mk_clean(self, parent) -> ctk.CTkFrame:
        f = ctk.CTkFrame(parent, fg_color="transparent")

        SectionLabel(f, "🗑️  Limpar .minecraft").pack(anchor="w")
        ctk.CTkLabel(f, text="│  Remove pastas selecionadas da sua instalação do Minecraft",
                     font=FONT_MONO, text_color=C_MUTED, anchor="w").pack(anchor="w")
        ctk.CTkLabel(f, text=_section_end(), font=FONT_MONO,
                     text_color=C_DIM, anchor="w").pack(anchor="w", pady=(0, 10))

        mc_dir = get_minecraft_dir()

        status_box = ctk.CTkFrame(f, fg_color=C_SURF, corner_radius=6)
        status_box.pack(fill="x", pady=(0, 10))
        ctk.CTkLabel(status_box, text=f"  Pasta:  {mc_dir}",
                     font=FONT_MONO, text_color=C_MUTED, anchor="w").pack(
                         anchor="w", padx=14, pady=(8, 2))
        self._clean_dir_lbl = ctk.CTkLabel(status_box, text="",
                                           font=("Consolas", 10), text_color=C_DIM, anchor="w")
        self._clean_dir_lbl.pack(anchor="w", padx=14, pady=(0, 8))
        exists = os.path.isdir(mc_dir)
        self._clean_dir_lbl.configure(
            text=f"  {'✔  Encontrada' if exists else '✖  Não encontrada'}",
            text_color=C_GREEN if exists else C_RED)

        ctk.CTkLabel(f, text="  SELECIONE O QUE LIMPAR", font=("Consolas", 9),
                     text_color=C_DIM, anchor="w").pack(anchor="w", pady=(4, 4))

        clean_items = [
            ("mods",          "mods",          "Mods instalados (.jar)"),
            ("config",        "config",         "Configurações dos mods"),
            ("resourcepacks", "resourcepacks",  "Pacotes de textura"),
            ("shaderpacks",   "shaderpacks",    "Shaders"),
            ("saves",         "saves",          "Mundos salvos"),
        ]

        self._clean_vars: dict[str, ctk.BooleanVar] = {}
        self._clean_count_lbls: dict[str, ctk.CTkLabel] = {}

        cb_grid = ctk.CTkFrame(f, fg_color="transparent")
        cb_grid.pack(fill="x")
        cb_grid.columnconfigure(0, weight=1)
        cb_grid.columnconfigure(1, weight=1)

        for i, (key, dirname, desc) in enumerate(clean_items):
            r, c = divmod(i, 2)
            var = ctk.BooleanVar(value=False)
            self._clean_vars[key] = var
            row_f = ctk.CTkFrame(cb_grid, fg_color=C_SURF, corner_radius=4)
            row_f.grid(row=r, column=c, padx=4, pady=3, sticky="ew")
            ctk.CTkCheckBox(row_f, text=f"  {dirname}/", variable=var,
                            font=FONT_MONO_B, text_color=C_TEXT,
                            fg_color=C_RED, hover_color="#C62828",
                            checkmark_color=C_BG, width=24).pack(side="left", padx=10, pady=8)
            count_lbl = ctk.CTkLabel(row_f, text="", font=("Consolas", 10), text_color=C_DIM)
            count_lbl.pack(side="right", padx=10)
            self._clean_count_lbls[key] = count_lbl
            subdir = os.path.join(mc_dir, dirname)
            if os.path.isdir(subdir):
                n = len(os.listdir(subdir))
                count_lbl.configure(text=f"{n} itens")

        Divider(f).pack(fill="x", pady=10)
        self._clean_log = LogBox(f, height=160)
        self._clean_log.pack(fill="both", expand=True)

        ctk.CTkButton(f, text="🗑  Limpar Selecionados", font=FONT_MONO_B,
                      fg_color=C_RED, hover_color="#C62828", height=36,
                      command=self._clean_run).pack(fill="x", pady=(10, 0))
        return f

    def _clean_run(self):
        mc_dir = get_minecraft_dir()
        clean_items_map = {
            "mods": "mods", "config": "config", "resourcepacks": "resourcepacks",
            "shaderpacks": "shaderpacks", "saves": "saves",
        }
        selected = [k for k, v in self._clean_vars.items() if v.get()]
        if not selected:
            messagebox.showwarning("Atenção", "Selecione pelo menos uma pasta para limpar.")
            return
        folders_txt = "\n  ".join(f"{clean_items_map[k]}/" for k in selected)
        if not messagebox.askyesno("Confirmar limpeza",
                                   f"Isso vai apagar permanentemente:\n\n  {folders_txt}"
                                   f"\n\nEm: {mc_dir}\n\nContinuar?"):
            return
        self._clean_log.clear()

        def run():
            for key in selected:
                dirname = clean_items_map[key]
                subdir = os.path.join(mc_dir, dirname)
                if os.path.isdir(subdir):
                    shutil.rmtree(subdir)
                    self.after(0, lambda d=dirname: self._clean_log.append(f"  🗑  {d}/ removido."))
                    if key in self._clean_count_lbls:
                        self.after(0, lambda k=key: self._clean_count_lbls[k].configure(text="0 itens"))
                else:
                    self.after(0, lambda d=dirname: self._clean_log.append(f"  ⚠  {d}/ não encontrado — pulando."))
            self.after(0, lambda: self._clean_log.append("✓ Limpeza concluída!"))

        threading.Thread(target=run, daemon=True).start()

    # ── shared helpers ────────────────────────────────────────────────────

    def _pick_dest(self):
        path = filedialog.askdirectory(initialdir=self._dest.get())
        if path:
            self._dest.set(path)
