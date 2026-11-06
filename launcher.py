"""Desktop controller for starting and stopping the YT Quick Download server."""

from __future__ import annotations

import os
import queue
import shutil
import socket
import subprocess
import sys
import threading
import tkinter as tk
import urllib.error
import urllib.request
import json
import ctypes
import re
import tempfile
import webbrowser
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import pystray
from PIL import Image, ImageDraw


APP_NAME = "YT Quick Download"
APP_VERSION = "2.1.3"
GITHUB_REPO = "TrgnJeng/yt-quick-download"
GITHUB_RELEASES_API = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
SETUP_ASSET_NAME = "YTQuickDownloadSetup.exe"
PORT = 8765
PORT_RANGE = range(PORT, PORT + 16)
INSTANCE_MUTEX_NAME = "Local\\YTQuickDownloadController"
SETTINGS_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "YTQuickDownload"
SETTINGS_FILE = SETTINGS_DIR / "settings.json"
_INSTANCE_MUTEX = None


def acquire_single_instance() -> bool:
    """Prevent multiple controller windows from starting at the same time."""
    global _INSTANCE_MUTEX
    if os.name != "nt":
        return True
    _INSTANCE_MUTEX = ctypes.windll.kernel32.CreateMutexW(None, False, INSTANCE_MUTEX_NAME)
    if not _INSTANCE_MUTEX:
        return False
    if ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        ctypes.windll.kernel32.CloseHandle(_INSTANCE_MUTEX)
        _INSTANCE_MUTEX = None
        return False
    return True


def release_single_instance() -> None:
    global _INSTANCE_MUTEX
    if _INSTANCE_MUTEX:
        ctypes.windll.kernel32.ReleaseMutex(_INSTANCE_MUTEX)
        ctypes.windll.kernel32.CloseHandle(_INSTANCE_MUTEX)
        _INSTANCE_MUTEX = None


def app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


ROOT_DIR = app_dir()


def find_free_port() -> int:
    for port in PORT_RANGE:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                probe.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise RuntimeError("Không còn cổng trống trong khoảng 8765-8780.")


def run_embedded_server() -> None:
    """Run the bundled server when the packaged executable receives --server."""
    from server.server import main

    main()


class Controller(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(APP_NAME)
        self.geometry("680x540")
        self.minsize(600, 460)
        self.configure(bg="#0d111c")
        self.protocol("WM_DELETE_WINDOW", self.on_close)

        self.process: subprocess.Popen[str] | None = None
        self.port = find_free_port()
        self.intentional_stop = False
        self.shutting_down = False
        self.auto_restart_count = 0
        self.max_auto_restarts = 3
        self.tray_icon: pystray.Icon | None = None
        self.tray_thread: threading.Thread | None = None
        self.log_queue: queue.Queue[str] = queue.Queue()
        self.health_check_started = False
        self.jobs_check_started = False
        self.job_ids: list[str] = []
        settings = self.load_settings()
        self.save_dir = str(settings.get("save_dir", "") or "")
        self.start_with_windows = bool(settings.get("start_with_windows", False))
        self.log_file = SETTINGS_DIR / "logs" / "app.log"
        self.history_file = SETTINGS_DIR / "history.json"
        self.history_items = self.load_history()
        self.status_var = tk.StringVar(value="Server đang tắt")
        self.status_detail_var = tk.StringVar(value="Bấm Bật server để bắt đầu.")
        self.dependency_var = tk.StringVar(value="Đang chờ kiểm tra môi trường...")
        self.startup_var = tk.BooleanVar(value=self.start_with_windows)

        self.configure_styles()
        self.build_ui()
        self.apply_jobs([])
        self.after(100, self.flush_logs)
        self.after(500, self.refresh_process_state)
        self.after(350, self.start_server)

    def load_settings(self) -> dict[str, object]:
        try:
            with SETTINGS_FILE.open("r", encoding="utf-8") as stream:
                data = json.load(stream)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def save_settings(self) -> None:
        try:
            SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
            with SETTINGS_FILE.open("w", encoding="utf-8") as stream:
                json.dump({
                    "save_dir": self.save_dir,
                    "start_with_windows": self.start_with_windows,
                }, stream, ensure_ascii=False, indent=2)
        except OSError as error:
            self.write_log(f"Không thể lưu cài đặt: {error}")

    def load_history(self) -> list[dict[str, object]]:
        try:
            with self.history_file.open("r", encoding="utf-8") as stream:
                data = json.load(stream)
            return data if isinstance(data, list) else []
        except (OSError, ValueError):
            return []

    def save_history(self) -> None:
        try:
            self.history_file.parent.mkdir(parents=True, exist_ok=True)
            with self.history_file.open("w", encoding="utf-8") as stream:
                json.dump(self.history_items[:50], stream, ensure_ascii=False, indent=2)
        except OSError as error:
            self.write_log(f"Không thể lưu lịch sử: {error}")

    def configure_styles(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("App.TFrame", background="#0d111c")
        style.configure("Card.TFrame", background="#171d2c")
        style.configure("Title.TLabel", background="#171d2c", foreground="#f5f7ff", font=("Segoe UI", 18, "bold"))
        style.configure("Muted.TLabel", background="#171d2c", foreground="#9da7bd", font=("Segoe UI", 10))
        style.configure("Status.TLabel", background="#171d2c", foreground="#ff829b", font=("Segoe UI", 12, "bold"))
        style.configure("Primary.TButton", padding=(16, 9), background="#7657f2", foreground="white", font=("Segoe UI", 10, "bold"))
        style.map("Primary.TButton", background=[("active", "#8c70ff"), ("disabled", "#34394b")])
        style.configure("Stop.TButton", padding=(16, 9), background="#d94d6b", foreground="white", font=("Segoe UI", 10, "bold"))
        style.map("Stop.TButton", background=[("active", "#ef6681"), ("disabled", "#34394b")])
        style.configure("Log.TFrame", background="#090c14")

    def build_ui(self) -> None:
        root = ttk.Frame(self, style="App.TFrame", padding=22)
        root.pack(fill="both", expand=True)

        header = ttk.Frame(root, style="Card.TFrame", padding=22)
        header.pack(fill="x")
        ttk.Label(header, text="YT Quick Download", style="Title.TLabel").pack(anchor="w")
        ttk.Label(header, text="by Tràng Dương", style="Muted.TLabel").pack(anchor="w", pady=(2, 0))
        ttk.Label(header, text="Bảng điều khiển máy chủ tải xuống", style="Muted.TLabel").pack(anchor="w", pady=(5, 0))

        status_card = ttk.Frame(root, style="Card.TFrame", padding=18)
        status_card.pack(fill="x", pady=(14, 12))
        ttk.Label(status_card, textvariable=self.status_var, style="Status.TLabel").pack(anchor="w")
        ttk.Label(status_card, textvariable=self.status_detail_var, style="Muted.TLabel").pack(anchor="w", pady=(5, 0))
        ttk.Label(status_card, textvariable=self.dependency_var, style="Muted.TLabel").pack(anchor="w", pady=(8, 0))

        controls = ttk.Frame(root, style="App.TFrame")
        controls.pack(fill="x")
        self.start_button = ttk.Button(controls, text="▶  Bật server", style="Primary.TButton", command=self.start_server)
        self.start_button.pack(side="left")
        self.stop_button = ttk.Button(controls, text="■  Tắt server", style="Stop.TButton", command=self.stop_server, state="disabled")
        self.stop_button.pack(side="left", padx=(10, 0))
        self.port_label = ttk.Label(controls, text=f"Cổng: {self.port}", style="Muted.TLabel")
        self.port_label.pack(side="right", pady=9)

        save_card = ttk.Frame(root, style="Card.TFrame", padding=14)
        save_card.pack(fill="x", pady=(12, 0))
        ttk.Label(save_card, text="Thư mục lưu mặc định", style="Muted.TLabel").pack(anchor="w")
        save_row = ttk.Frame(save_card, style="Card.TFrame")
        save_row.pack(fill="x", pady=(6, 0))
        self.save_dir_var = tk.StringVar(value=self.save_dir or "Mặc định: Downloads")
        ttk.Label(save_row, textvariable=self.save_dir_var, style="Muted.TLabel").pack(side="left", fill="x", expand=True)
        ttk.Button(save_row, text="Chọn thư mục", command=self.choose_save_dir).pack(side="right")

        actions = ttk.Frame(root, style="App.TFrame")
        actions.pack(fill="x", pady=(10, 0))
        ttk.Button(actions, text="Mở thư mục", command=self.open_save_dir).pack(side="left")
        ttk.Button(actions, text="Kiểm tra kết nối", command=self.request_health_check).pack(side="left", padx=(8, 0))
        self.ffmpeg_button = ttk.Button(actions, text="Cài ffmpeg", command=self.install_ffmpeg)
        self.ffmpeg_button.pack(side="left", padx=(8, 0))
        self.update_button = ttk.Button(actions, text="Kiểm tra cập nhật", command=self.check_for_updates)
        self.update_button.pack(side="left", padx=(8, 0))
        ttk.Checkbutton(
            actions,
            text="Tự chạy cùng Windows",
            variable=self.startup_var,
            command=self.toggle_startup,
        ).pack(side="right")

        jobs_header = ttk.Frame(root, style="App.TFrame")
        jobs_header.pack(fill="x", pady=(14, 5))
        ttk.Label(jobs_header, text="Lịch sử và hàng đợi", style="Muted.TLabel").pack(side="left")
        ttk.Button(jobs_header, text="Làm mới", command=self.request_jobs_refresh).pack(side="right")
        ttk.Button(jobs_header, text="Hủy tác vụ", command=self.cancel_selected_job).pack(side="right", padx=(0, 8))
        jobs_frame = ttk.Frame(root, style="Log.TFrame")
        jobs_frame.pack(fill="x")
        self.jobs_list = tk.Listbox(
            jobs_frame,
            height=5,
            bg="#090c14",
            fg="#b9c4da",
            selectbackground="#7657f2",
            selectforeground="#ffffff",
            relief="flat",
            borderwidth=0,
            font=("Segoe UI", 9),
            activestyle="none",
        )
        self.jobs_list.pack(side="left", fill="x", expand=True, padx=8, pady=6)
        jobs_scrollbar = ttk.Scrollbar(jobs_frame, orient="vertical", command=self.jobs_list.yview)
        jobs_scrollbar.pack(side="right", fill="y")
        self.jobs_list.configure(yscrollcommand=jobs_scrollbar.set)

        ttk.Label(root, text="Nhật ký hoạt động", style="Muted.TLabel").pack(anchor="w", pady=(18, 6))
        log_frame = ttk.Frame(root, style="Log.TFrame")
        log_frame.pack(fill="both", expand=True)
        self.log_text = tk.Text(
            log_frame,
            height=8,
            bg="#090c14",
            fg="#b9c4da",
            insertbackground="#ffffff",
            relief="flat",
            borderwidth=0,
            padx=12,
            pady=10,
            font=("Consolas", 9),
            state="disabled",
            wrap="word",
        )
        self.log_text.pack(side="left", fill="both", expand=True)
        scrollbar = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        scrollbar.pack(side="right", fill="y")
        self.log_text.configure(yscrollcommand=scrollbar.set)

    def choose_save_dir(self) -> None:
        selected = filedialog.askdirectory(title="Chọn thư mục lưu file", initialdir=self.save_dir or None)
        if not selected:
            return
        self.save_dir = selected
        self.save_dir_var.set(selected)
        self.save_settings()
        self.write_log(f"Đã chọn thư mục lưu: {selected}")
        if self.process and self.process.poll() is None:
            self.stop_server()
            self.after(400, self.start_server)

    def write_log(self, text: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        line = f"[{stamp}] {text}"
        self.log_text.configure(state="normal")
        self.log_text.insert("end", line + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")
        try:
            self.log_file.parent.mkdir(parents=True, exist_ok=True)
            with self.log_file.open("a", encoding="utf-8") as stream:
                stream.write(line + "\n")
        except OSError:
            pass

    def open_save_dir(self) -> None:
        path = Path(self.save_dir or (Path.home() / "Downloads"))
        path.mkdir(parents=True, exist_ok=True)
        os.startfile(path) if os.name == "nt" else webbrowser.open(path.as_uri())

    @staticmethod
    def version_key(value: str) -> tuple[int, ...]:
        numbers = re.findall(r"\d+", value or "")
        return tuple(int(number) for number in numbers[:4]) or (0,)

    def check_for_updates(self) -> None:
        self.update_button.configure(state="disabled")
        self.dependency_var.set(f"Đang kiểm tra bản cập nhật v{APP_VERSION}...")
        threading.Thread(target=self._check_updates_worker, daemon=True).start()

    def _check_updates_worker(self) -> None:
        try:
            request = urllib.request.Request(
                GITHUB_RELEASES_API,
                headers={
                    "Accept": "application/vnd.github+json",
                    "User-Agent": "YTQuickDownload/" + APP_VERSION,
                },
            )
            with urllib.request.urlopen(request, timeout=8) as response:
                release = json.loads(response.read().decode("utf-8"))
            tag = str(release.get("tag_name", "")).strip()
            assets = release.get("assets") or []
            asset = next(
                (item for item in assets if item.get("name") == SETUP_ASSET_NAME),
                None,
            )
            if not tag or not asset or not asset.get("browser_download_url"):
                raise RuntimeError(
                    "GitHub Release chưa có file " + SETUP_ASSET_NAME + "."
                )
            self.after(0, lambda: self.finish_update_check(tag, asset["browser_download_url"], None))
        except (OSError, ValueError, urllib.error.URLError, RuntimeError) as error:
            self.after(0, lambda error=error: self.finish_update_check(None, None, error))

    def finish_update_check(self, tag: str | None, download_url: str | None, error: Exception | None) -> None:
        self.update_button.configure(state="normal")
        if error:
            message = f"Không kiểm tra được cập nhật: {error}"
            self.dependency_var.set(message)
            self.write_log(message)
            return
        if self.version_key(tag or "0") <= self.version_key(APP_VERSION):
            self.dependency_var.set(f"Bạn đang dùng phiên bản mới nhất v{APP_VERSION}.")
            self.write_log(f"Đã kiểm tra GitHub: phiên bản hiện tại là mới nhất ({tag}).")
            return
        if not messagebox.askyesno(
            APP_NAME,
            f"Có phiên bản mới {tag} (bạn đang dùng v{APP_VERSION}).\nTải và cài đặt ngay?",
        ):
            return
        self.update_button.configure(state="disabled")
        self.dependency_var.set(f"Đang tải bản cập nhật {tag}...")
        threading.Thread(target=self._download_update_worker, args=(download_url, tag), daemon=True).start()

    def _download_update_worker(self, download_url: str, tag: str) -> None:
        destination = Path(tempfile.gettempdir()) / SETUP_ASSET_NAME
        try:
            request = urllib.request.Request(download_url, headers={"User-Agent": "YTQuickDownload/" + APP_VERSION})
            with urllib.request.urlopen(request, timeout=30) as response, destination.open("wb") as output:
                shutil.copyfileobj(response, output)
            self.after(0, lambda: self.launch_update(destination, tag))
        except (OSError, urllib.error.URLError) as error:
            self.after(0, lambda error=error: self.finish_update_download(error))

    def finish_update_download(self, error: Exception) -> None:
        self.update_button.configure(state="normal")
        self.dependency_var.set(f"Tải bản cập nhật thất bại: {error}")
        messagebox.showerror(APP_NAME, f"Không thể tải bản cập nhật:\n{error}")

    def launch_update(self, installer_path: Path, tag: str) -> None:
        try:
            subprocess.Popen([str(installer_path)], cwd=installer_path.parent)
            self.write_log(f"Đã tải bản cập nhật {tag}; đang mở trình cài đặt.")
            self.after(500, self.exit_app)
        except OSError as error:
            self.finish_update_download(error)

    def request_health_check(self) -> None:
        self.dependency_var.set("Đang kiểm tra môi trường...")
        threading.Thread(target=self.check_health, daemon=True).start()

    def request_jobs_refresh(self) -> None:
        if not self.process or self.process.poll() is not None:
            self.jobs_list.delete(0, "end")
            self.jobs_list.insert("end", "Server chưa chạy.")
            return
        if self.jobs_check_started:
            return
        self.jobs_check_started = True
        threading.Thread(target=self.fetch_jobs, daemon=True).start()

    def fetch_jobs(self) -> None:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/jobs", timeout=4) as response:
                data = json.loads(response.read().decode("utf-8"))
            self.after(0, lambda: self.apply_jobs(data.get("jobs", [])))
        except (OSError, ValueError, urllib.error.URLError) as error:
            self.after(0, lambda error=error: self.write_log(f"Không lấy được danh sách tác vụ: {error}"))
        finally:
            self.after(0, self.finish_jobs_refresh)

    def finish_jobs_refresh(self) -> None:
        self.jobs_check_started = False

    def apply_jobs(self, jobs: list[dict[str, object]]) -> None:
        known_ids = {str(job.get("id")) for job in jobs}
        for job in jobs:
            if job.get("state") in ("done", "error", "cancelled"):
                self.history_items = [item for item in self.history_items if item.get("id") != job.get("id")]
                self.history_items.append(job)
        self.history_items = self.history_items[-50:]
        self.save_history()
        all_jobs = jobs + [item for item in reversed(self.history_items) if str(item.get("id")) not in known_ids]
        self.jobs_list.delete(0, "end")
        self.job_ids = []
        if not all_jobs:
            self.jobs_list.insert("end", "Chưa có tác vụ nào.")
            return
        for job in all_jobs[:50]:
            job_id = str(job.get("id", ""))
            state = str(job.get("state", "unknown"))
            mode = str(job.get("mode", "")).upper()
            filename = str(job.get("filename") or ("Video " + mode))
            message = str(job.get("message") or state)
            marker = "✅" if state == "done" else "❌" if state == "error" else "⏳"
            self.job_ids.append(job_id)
            self.jobs_list.insert("end", f"{marker} [{mode}] {filename} — {message}")

    def cancel_selected_job(self) -> None:
        selection = self.jobs_list.curselection()
        if not selection or selection[0] >= len(self.job_ids):
            return
        job_id = self.job_ids[selection[0]]
        threading.Thread(target=self.cancel_job_request, args=(job_id,), daemon=True).start()

    def cancel_job_request(self, job_id: str) -> None:
        try:
            request = urllib.request.Request(
                f"http://127.0.0.1:{self.port}/cancel/{job_id}",
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=4):
                pass
            self.after(0, self.request_jobs_refresh)
        except (OSError, urllib.error.URLError) as error:
            self.after(0, lambda error=error: messagebox.showerror(APP_NAME, f"Không thể hủy tác vụ:\n{error}"))

    def toggle_startup(self) -> None:
        if os.name != "nt":
            self.startup_var.set(False)
            return
        try:
            import winreg

            key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as key:
                if self.startup_var.get():
                    if getattr(sys, "frozen", False):
                        command = f'"{sys.executable}"'
                    else:
                        command = f'"{sys.executable}" "{Path(__file__).resolve()}"'
                    winreg.SetValueEx("YTQuickDownload", 0, winreg.REG_SZ, command)
                else:
                    try:
                        winreg.DeleteValue(key, "YTQuickDownload")
                    except FileNotFoundError:
                        pass
            self.start_with_windows = self.startup_var.get()
            self.save_settings()
            self.write_log("Đã cập nhật tùy chọn tự chạy cùng Windows.")
        except OSError as error:
            self.startup_var.set(not self.startup_var.get())
            messagebox.showerror(APP_NAME, f"Không thể cập nhật khởi động cùng Windows:\n{error}")

    def install_ffmpeg(self) -> None:
        if shutil.which("ffmpeg"):
            messagebox.showinfo(APP_NAME, "ffmpeg đã được cài đặt và có trong PATH.")
            return
        if not shutil.which("winget"):
            webbrowser.open("https://www.gyan.dev/ffmpeg/builds/")
            self.write_log("Không có winget; đã mở trang tải ffmpeg.")
            return
        self.ffmpeg_button.configure(state="disabled")
        self.write_log("Đang cài ffmpeg bằng winget...")
        threading.Thread(target=self._install_ffmpeg_worker, daemon=True).start()

    def _install_ffmpeg_worker(self) -> None:
        result = subprocess.run(
            ["winget", "install", "--id", "Gyan.FFmpeg.Shared", "-e",
             "--accept-source-agreements", "--accept-package-agreements"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        output = (result.stdout or result.stderr or "").strip().splitlines()
        message = output[-1] if output else "Không có thông tin từ winget."
        self.after(0, lambda: self.finish_ffmpeg_install(result.returncode, message))

    def finish_ffmpeg_install(self, return_code: int, message: str) -> None:
        self.ffmpeg_button.configure(state="normal")
        self.write_log(f"winget: {message}")
        self.request_health_check()
        if return_code == 0:
            messagebox.showinfo(APP_NAME, "Đã cài ffmpeg. Hãy thử tải lại video.")
        else:
            messagebox.showwarning(APP_NAME, "Không cài được ffmpeg tự động. Hãy xem log hoặc cài thủ công.")

    def start_server(self) -> None:
        if self.process and self.process.poll() is None:
            return

        self.intentional_stop = False

        try:
            self.port = find_free_port()
        except RuntimeError as error:
            self.status_var.set("Không còn cổng trống")
            self.status_detail_var.set(str(error))
            messagebox.showerror(APP_NAME, str(error))
            return
        self.port_label.configure(text=f"Cổng: {self.port}")

        if getattr(sys, "frozen", False):
            command = [sys.executable, "--server"]
        else:
            command = [sys.executable, str(ROOT_DIR / "server" / "server.py")]

        environment = os.environ.copy()
        environment.setdefault("PYTHONUTF8", "1")
        environment["YTQD_PORT"] = str(self.port)
        if self.save_dir:
            environment["YTQD_SAVE_DIR"] = self.save_dir
        else:
            environment.pop("YTQD_SAVE_DIR", None)
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

        try:
            self.process = subprocess.Popen(
                command,
                cwd=ROOT_DIR,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=creation_flags,
                env=environment,
            )
        except OSError as error:
            self.status_var.set("Không thể bật server")
            self.status_detail_var.set(str(error))
            messagebox.showerror(APP_NAME, f"Không thể khởi động server:\n{error}")
            return

        self.status_var.set("Server đang khởi động...")
        self.status_detail_var.set(f"Đang kiểm tra cổng {self.port}.")
        self.dependency_var.set("Đang kiểm tra ffmpeg, Node/Deno và yt-dlp...")
        self.health_check_started = False
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.write_log("Đang khởi động server...")
        threading.Thread(target=self.read_process_output, daemon=True).start()

    def read_process_output(self) -> None:
        if not self.process or not self.process.stdout:
            return
        for line in self.process.stdout:
            self.log_queue.put(line.rstrip())

    def flush_logs(self) -> None:
        while True:
            try:
                line = self.log_queue.get_nowait()
            except queue.Empty:
                break
            if line:
                self.write_log(line)
        self.after(100, self.flush_logs)

    def refresh_process_state(self) -> None:
        if self.process:
            return_code = self.process.poll()
            if return_code is None:
                if self.status_var.get() == "Server đang khởi động...":
                    self.status_var.set("Server đang chạy")
                    self.status_detail_var.set(f"Extension có thể kết nối tại 127.0.0.1:{self.port}.")
                if not self.health_check_started:
                    self.health_check_started = True
                    threading.Thread(target=self.check_health, daemon=True).start()
                self.request_jobs_refresh()
            else:
                self.status_var.set("Server đang tắt")
                self.status_detail_var.set(f"Server đã dừng (mã {return_code}).")
                self.dependency_var.set("Chưa kiểm tra môi trường.")
                self.start_button.configure(state="normal")
                self.stop_button.configure(state="disabled")
                self.process = None
                if not self.intentional_stop and not self.shutting_down:
                    if self.auto_restart_count < self.max_auto_restarts:
                        self.auto_restart_count += 1
                        self.status_var.set("Server gặp lỗi, đang tự khôi phục...")
                        self.status_detail_var.set(
                            f"Lần khôi phục {self.auto_restart_count}/{self.max_auto_restarts}."
                        )
                        self.write_log(
                            f"Server đã dừng bất thường (mã {return_code}); "
                            f"tự khởi động lại lần {self.auto_restart_count}."
                        )
                        self.after(1500, self.start_server)
                    else:
                        self.write_log("Đã dừng tự khôi phục sau 3 lần thất bại.")
        self.after(500, self.refresh_process_state)

    def check_health(self) -> None:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/health", timeout=4) as response:
                health = json.loads(response.read().decode("utf-8"))
            self.after(0, lambda: self.apply_health(health))
        except (OSError, ValueError, urllib.error.URLError) as error:
            self.after(0, lambda error=error: self.apply_health_error(error))

    def apply_health_error(self, error: Exception) -> None:
        if isinstance(error, urllib.error.URLError):
            reason = getattr(error, "reason", error)
            detail = f"Không kết nối được server. Hãy bấm 'Bật server' rồi thử lại ({reason})."
        else:
            detail = f"Không kiểm tra được môi trường: {error}"
        self.dependency_var.set(detail)
        self.write_log(detail)

    def apply_health(self, health: dict[str, object]) -> None:
        self.auto_restart_count = 0
        missing = []
        if not health.get("ffmpeg"):
            missing.append("ffmpeg")
        if not health.get("deno") and not health.get("node"):
            missing.append("Deno/Node")
        if missing:
            self.dependency_var.set("⚠️ Thiếu: " + ", ".join(missing))
            self.write_log("Cảnh báo môi trường: thiếu " + ", ".join(missing))
        else:
            self.dependency_var.set(f"✅ yt-dlp {health.get('yt_dlp', 'sẵn sàng')} | ffmpeg và Node/Deno đã sẵn sàng")
        self.status_detail_var.set(
            f"Server đang chạy tại 127.0.0.1:{health.get('port', self.port)} · "
            f"Tác vụ đang chạy: {health.get('active_jobs', 0)}"
        )

    def stop_server(self) -> None:
        if not self.process or self.process.poll() is not None:
            return
        self.intentional_stop = True
        self.write_log("Đang dừng server...")
        if os.name == "nt":
            # PyInstaller one-file apps may create a child bootloader process.
            # Kill the whole tree so no hidden server remains after closing.
            subprocess.run(
                ["taskkill", "/PID", str(self.process.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        else:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=2)
        self.status_var.set("Server đang tắt")
        self.status_detail_var.set("Server đã được tắt.")
        self.start_button.configure(state="normal")
        self.stop_button.configure(state="disabled")
        self.process = None
        self.jobs_check_started = False
        self.jobs_list.delete(0, "end")
        self.jobs_list.insert("end", "Server chưa chạy.")

    def build_tray_image(self) -> Image.Image:
        image = Image.new("RGBA", (64, 64), (255, 0, 0, 255))
        draw = ImageDraw.Draw(image)
        draw.polygon([(25, 16), (49, 32), (25, 48)], fill="white")
        return image

    def start_tray(self) -> None:
        if self.tray_icon is not None:
            return
        menu = pystray.Menu(
            pystray.MenuItem("Mở bảng điều khiển", lambda icon, item: self.after(0, self.show_window)),
            pystray.MenuItem("Bật/tắt server", lambda icon, item: self.after(0, self.toggle_server)),
            pystray.MenuItem("Thoát", lambda icon, item: self.after(0, self.exit_app)),
        )
        self.tray_icon = pystray.Icon(APP_NAME, self.build_tray_image(), APP_NAME, menu)
        self.tray_thread = threading.Thread(target=self.tray_icon.run, daemon=True)
        self.tray_thread.start()

    def show_window(self) -> None:
        self.deiconify()
        self.lift()
        self.focus_force()

    def toggle_server(self) -> None:
        if self.process and self.process.poll() is None:
            self.stop_server()
        else:
            self.start_server()

    def exit_app(self) -> None:
        self.shutting_down = True
        if self.tray_icon is not None:
            self.tray_icon.stop()
            self.tray_icon = None
        if self.process and self.process.poll() is None:
            self.stop_server()
        release_single_instance()
        self.destroy()

    def on_close(self) -> None:
        self.start_tray()
        self.withdraw()


if __name__ == "__main__":
    if "--server" in sys.argv:
        run_embedded_server()
    else:
        if not acquire_single_instance():
            messagebox.showinfo(APP_NAME, "Ứng dụng đã đang chạy. Hãy mở lại từ biểu tượng ở khay hệ thống.")
            sys.exit(0)
        try:
            Controller().mainloop()
        finally:
            release_single_instance()
