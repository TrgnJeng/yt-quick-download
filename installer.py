"""One-click installer for the desktop controller and Chrome extension."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import webbrowser
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, ttk


APP_NAME = "YT Quick Download"
INSTALL_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "YTQuickDownload"
EXTENSION_DIR = INSTALL_DIR / "extension"
START_MENU_DIR = Path(os.environ.get("APPDATA", Path.home())) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / APP_NAME


def payload_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS")) / "payload"
    return Path(__file__).resolve().parent / "installer-payload"


def powershell_quote(value: Path | str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def create_shortcut(shortcut_path: Path, target_path: Path, arguments: str = "") -> None:
    """Create a Windows .lnk without adding a third-party installer dependency."""
    shortcut_path.parent.mkdir(parents=True, exist_ok=True)
    script = (
        "$shell = New-Object -ComObject WScript.Shell; "
        f"$link = $shell.CreateShortcut({powershell_quote(shortcut_path)}); "
        f"$link.TargetPath = {powershell_quote(target_path)}; "
        f"$link.WorkingDirectory = {powershell_quote(target_path.parent)}; "
        f"$link.Arguments = {powershell_quote(arguments)}; "
        "$link.Save()"
    )
    subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def write_uninstaller() -> Path:
    uninstaller = INSTALL_DIR / "uninstall.bat"
    uninstaller.write_text(
        "@echo off\n"
        "set \"APP_DIR=%~dp0\"\n"
        "taskkill /IM YTQuickDownload.exe /F >nul 2>&1\n"
        "reg delete \"HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run\" /v YTQuickDownload /f >nul 2>&1\n"
        "start \"\" cmd /c \"timeout /t 1 /nobreak >nul & rmdir /s /q \"\"%APP_DIR%\"\"\"\n",
        encoding="utf-8",
    )
    return uninstaller


class Installer(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(f"Cài đặt {APP_NAME}")
        self.geometry("520x310")
        self.resizable(False, False)
        self.configure(bg="#0d111c")
        self.protocol("WM_DELETE_WINDOW", self.destroy)

        self.status = tk.StringVar(value="Đang chuẩn bị cài đặt...")
        self.build_ui()
        self.after(350, self.install)

    def build_ui(self) -> None:
        card = tk.Frame(self, bg="#171d2c", padx=28, pady=24)
        card.pack(fill="both", expand=True, padx=18, pady=18)

        tk.Label(card, text=APP_NAME, bg="#171d2c", fg="#f5f7ff", font=("Segoe UI", 19, "bold")).pack(anchor="w")
        tk.Label(card, text="by Tràng Dương", bg="#171d2c", fg="#aab3c8", font=("Segoe UI", 10, "italic")).pack(anchor="w", pady=(3, 0))
        tk.Label(card, text="Cài app điều khiển và extension Chrome", bg="#171d2c", fg="#aab3c8", font=("Segoe UI", 10)).pack(anchor="w", pady=(14, 18))

        self.progress = ttk.Progressbar(card, mode="indeterminate")
        self.progress.pack(fill="x")
        tk.Label(card, textvariable=self.status, bg="#171d2c", fg="#dce3f5", font=("Segoe UI", 10), wraplength=440, justify="left").pack(anchor="w", pady=(14, 0))

        self.close_button = tk.Button(card, text="Đóng", command=self.destroy, state="disabled", bg="#34394b", fg="white", relief="flat", padx=18, pady=7)
        self.close_button.pack(anchor="e", pady=(20, 0))

    def install(self) -> None:
        payload = payload_dir()
        app_source = payload / "YTQuickDownload.exe"
        extension_source = payload / "extension"

        try:
            if not app_source.is_file() or not extension_source.is_dir():
                raise FileNotFoundError("Không tìm thấy dữ liệu cài đặt trong bộ cài.")

            self.status.set("Đang đóng phiên bản cũ nếu có...")
            subprocess.run(
                ["taskkill", "/IM", "YTQuickDownload.exe", "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            INSTALL_DIR.mkdir(parents=True, exist_ok=True)
            EXTENSION_DIR.mkdir(parents=True, exist_ok=True)
            self.status.set("Đang chép app điều khiển...")
            shutil.copy2(app_source, INSTALL_DIR / "YTQuickDownload.exe")

            self.status.set("Đang chép extension Chrome...")
            for source in extension_source.iterdir():
                destination = EXTENSION_DIR / source.name
                if source.is_dir():
                    shutil.copytree(source, destination, dirs_exist_ok=True)
                else:
                    shutil.copy2(source, destination)

            self.status.set("Đang tạo shortcut và trình gỡ cài đặt...")
            installed_app = INSTALL_DIR / "YTQuickDownload.exe"
            uninstaller = write_uninstaller()
            create_shortcut(Path.home() / "Desktop" / f"{APP_NAME}.lnk", installed_app)
            create_shortcut(START_MENU_DIR / f"{APP_NAME}.lnk", installed_app)
            create_shortcut(START_MENU_DIR / f"Gỡ cài đặt {APP_NAME}.lnk", uninstaller)

            self.progress.stop()
            self.status.set(
                "Đã cài xong. Shortcut đã được tạo trên Desktop và Start Menu. "
                "Chrome sẽ mở trang extensions để nạp extension."
            )
            self.close_button.configure(state="normal")

            subprocess.Popen([str(INSTALL_DIR / "YTQuickDownload.exe")], cwd=INSTALL_DIR)
            os.startfile(str(EXTENSION_DIR))
            webbrowser.open("chrome://extensions")
            messagebox.showinfo(APP_NAME, "Đã cài app. Hãy chọn thư mục extension trong cửa sổ Chrome.")
        except Exception as error:  # noqa: BLE001
            self.progress.stop()
            self.status.set(f"Cài đặt thất bại: {error}")
            self.close_button.configure(state="normal")
            messagebox.showerror(APP_NAME, str(error))


if __name__ == "__main__":
    Installer().mainloop()
