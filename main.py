import customtkinter as ctk
from ui.main_window import MinecraftInstallerApp

if __name__ == "__main__":
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("blue")
    app = MinecraftInstallerApp()
    app.mainloop()
