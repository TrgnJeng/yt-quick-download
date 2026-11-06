#!/usr/bin/env python3
"""
YT Quick Download - máy chủ yt-dlp chạy ngay trên máy của bạn.

Extension gửi link YouTube tới máy chủ này (chỉ nghe ở 127.0.0.1), máy chủ dùng
yt-dlp + ffmpeg để tải và chuyển đổi, sau đó extension nhận file qua
chrome.downloads.

Chạy:   python server.py        (Windows: py server.py)
Dừng:   Ctrl + C

Mặc định server lưu thẳng file vào thư mục Downloads của bạn (không đi qua trình
tải của Chrome nên IDM không can thiệp được).

Biến môi trường (không bắt buộc):
  YTQD_SAVE_DIR          Thư mục lưu file. Mặc định: thư mục Downloads của bạn.
                         Đặt YTQD_SAVE_DIR=browser để Chrome tải file về thay vì
                         server tự lưu (dùng chrome.downloads).
  YTQD_PORT              Cổng, mặc định 8765 (nếu đổi phải sửa SERVER_URL trong
                         shared.js và host_permissions trong manifest.json)
  YTQD_COOKIES_BROWSER   Tên trình duyệt để lấy cookie đăng nhập YouTube
                         (chrome, firefox, edge, brave...). Chỉ dùng khi YouTube
                         báo "Sign in to confirm you're not a bot".
"""
import json
import mimetypes
import os
import re
import shutil
import sys
import tempfile
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlparse

# Tránh lỗi font/encoding khi in tiếng Việt ra console (đặc biệt trên Windows).
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# PyInstaller --windowed khÃ´ng cÃ³ console, khi Ä‘Ã³ stdout/stderr cÃ³ thá»ƒ lÃ  None.
# GÃ¡n output rá»—ng Ä‘á»ƒ server váº«n tráº£ HTTP bÃ¬nh thÆ°á»ng trong app GUI.
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")

try:
    import yt_dlp
    from yt_dlp.version import __version__ as YTDLP_VERSION
except ImportError:
    print("Chưa cài yt-dlp. Hãy chạy:  python -m pip install -U yt-dlp yt-dlp-ejs")
    sys.exit(1)

HOST = "127.0.0.1"
PORT = int(os.environ.get("YTQD_PORT", "8765"))
COOKIES_BROWSER = os.environ.get("YTQD_COOKIES_BROWSER", "").strip()

_save_env = os.environ.get("YTQD_SAVE_DIR", "").strip()
if _save_env.lower() == "browser":
    SAVE_DIR = None  # để extension dùng chrome.downloads
else:
    SAVE_DIR = _save_env or os.path.join(os.path.expanduser("~"), "Downloads")

BASE_DIR = os.path.join(tempfile.gettempdir(), "yt-quick-download")
JOB_TTL_SECONDS = 30 * 60  # xóa file tạm sau 30 phút kể từ khi xong
MAX_PARALLEL_JOBS = 2

JOBS = {}
JOBS_LOCK = threading.Lock()
SLOTS = threading.Semaphore(MAX_PARALLEL_JOBS)
CANCEL_EVENTS = {}

VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


class JobCancelled(Exception):
    """Raised from a yt-dlp progress hook when the user cancels a job."""


# --------------------------------------------------------------------------
# Tiện ích
# --------------------------------------------------------------------------
def extract_video_id(raw_url):
    """Lấy video ID từ URL YouTube; trả về None nếu không hợp lệ."""
    try:
        u = urlparse(raw_url)
    except ValueError:
        return None
    if u.scheme not in ("http", "https"):
        return None

    host = (u.hostname or "").lower()
    host = re.sub(r"^(www\.|m\.|music\.)", "", host)

    video_id = None
    if host == "youtu.be":
        video_id = u.path.lstrip("/").split("/")[0]
    elif host == "youtube.com":
        if u.path == "/watch":
            video_id = (parse_qs(u.query).get("v") or [None])[0]
        else:
            m = re.match(r"^/(shorts|live|embed)/([^/?]+)", u.path)
            if m:
                video_id = m.group(2)

    if video_id and VIDEO_ID_RE.match(video_id):
        return video_id
    return None


def clean_error(exc):
    """Rút gọn và việt hóa các lỗi hay gặp của yt-dlp."""
    text = ANSI_RE.sub("", str(exc)).strip()
    text = re.sub(r"^ERROR:\s*", "", text)
    low = text.lower()

    if "ffmpeg" in low and ("not found" in low or "not installed" in low or "ffprobe" in low):
        return "Chưa cài ffmpeg (hoặc ffmpeg chưa nằm trong PATH)."
    if "sign in to confirm" in low or "not a bot" in low:
        return ("YouTube yêu cầu đăng nhập. Đặt biến YTQD_COOKIES_BROWSER=chrome "
                "rồi chạy lại server (xem README).")
    if "requested format is not available" in low:
        return ("Không lấy được định dạng. Hãy cài Deno và chạy: "
                "python -m pip install -U yt-dlp yt-dlp-ejs")
    if "video unavailable" in low or "private video" in low:
        return "Video không khả dụng hoặc ở chế độ riêng tư."
    if ("name or service not known" in low
            or "temporary failure in name resolution" in low
            or "getaddrinfo failed" in low
            or "err_name_not_resolved" in low
            or "could not resolve host" in low):
        return ("Không phân giải được địa chỉ YouTube/googlevideo. "
                "Hãy kiểm tra Internet, DNS hoặc VPN/proxy rồi thử lại.")
    return text[:300] or "Lỗi không xác định."


def get_job(job_id):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        return dict(job) if job else None


def set_job(job_id, **fields):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if job is not None:
            job.update(fields)


def cleanup_old_jobs():
    now = time.time()
    expired = []
    with JOBS_LOCK:
        for job_id, job in list(JOBS.items()):
            finished = job.get("finished")
            if finished and now - finished > JOB_TTL_SECONDS:
                expired.append(job_id)
                del JOBS[job_id]
    for job_id in expired:
        shutil.rmtree(os.path.join(BASE_DIR, job_id), ignore_errors=True)


# --------------------------------------------------------------------------
# Tải bằng yt-dlp
# --------------------------------------------------------------------------
def download(job_id, video_id, mode, job_dir, audio_quality, video_quality,
             filename_template, subtitles, subtitle_language, cancel_event):
    def hook(d):
        if cancel_event.is_set():
            raise JobCancelled()
        status = d.get("status")
        if status == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            done = d.get("downloaded_bytes") or 0
            percent = min(100, int(done * 100 / total)) if total else None
            speed = d.get("speed")
            eta = d.get("eta")
            if total:
                set_job(job_id, message="Đang tải %d%%" % percent, percent=percent,
                        downloaded_bytes=done, total_bytes=total, speed=speed, eta=eta)
            else:
                set_job(job_id, message="Đang tải...", downloaded_bytes=done,
                        speed=speed, eta=eta)
        elif status == "finished":
            set_job(job_id, message="Đang xử lý (ffmpeg)", percent=100, speed=None, eta=None)

    opts = {
        "outtmpl": os.path.join(job_dir, filename_template + ".%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "windowsfilenames": True,
        "progress_hooks": [hook],
        "socket_timeout": 30,
        "retries": 5,
    }

    if mode == "mp3":
        opts.update({
            "format": "bestaudio/best",
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": str(audio_quality),
            }],
        })
        wanted_ext = ".mp3"
    else:
        opts.update({
            "format": ("bv*[height<=%d][vcodec^=avc1]+ba[ext=m4a]/"
                       "b[height<=%d][ext=mp4]/"
                       "bv*[height<=%d]+ba/"
                       "b[height<=%d]/b") % (
                           video_quality, video_quality, video_quality, video_quality
                       ),
            "merge_output_format": "mp4",
        })
        wanted_ext = ".mp4"

    if COOKIES_BROWSER:
        opts["cookiesfrombrowser"] = (COOKIES_BROWSER,)

    if subtitles:
        opts.update({
            "writesubtitles": True,
            "writeautomaticsub": True,
            "subtitleslangs": [subtitle_language],
            "subtitlesformat": "vtt",
        })

    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download(["https://www.youtube.com/watch?v=" + video_id])

    candidates = [
        n for n in os.listdir(job_dir)
        if os.path.isfile(os.path.join(job_dir, n))
        and not n.lower().endswith((".part", ".ytdl", ".temp"))
    ]
    for name in candidates:
        if name.lower().endswith(wanted_ext):
            return os.path.join(job_dir, name)
    if candidates:  # yt-dlp trả về định dạng khác (ví dụ .webm): lấy file lớn nhất
        candidates.sort(key=lambda n: os.path.getsize(os.path.join(job_dir, n)), reverse=True)
        return os.path.join(job_dir, candidates[0])
    raise RuntimeError("Không tìm thấy file sau khi tải xong.")


def move_to_save_dir(src, save_dir):
    """Chuyển file đã tải vào SAVE_DIR, tự thêm (1), (2)... nếu trùng tên."""
    os.makedirs(save_dir, exist_ok=True)
    base, ext = os.path.splitext(os.path.basename(src))
    dest = os.path.join(save_dir, base + ext)
    n = 1
    while os.path.exists(dest):
        dest = os.path.join(save_dir, "%s (%d)%s" % (base, n, ext))
        n += 1
    shutil.move(src, dest)
    return dest


def run_job(job_id, video_id, mode, save_dir, audio_quality, video_quality,
            filename_template, subtitles, subtitle_language, cancel_event):
    job_dir = os.path.join(BASE_DIR, job_id)
    os.makedirs(job_dir, exist_ok=True)
    with SLOTS:
        try:
            if cancel_event.is_set():
                raise JobCancelled()
            set_job(job_id, state="running", message="Đang kết nối YouTube")
            path = download(job_id, video_id, mode, job_dir, audio_quality, video_quality,
                            filename_template, subtitles, subtitle_language, cancel_event)
            saved_path = None
            subtitle_paths = []
            if save_dir:
                set_job(job_id, message="Đang lưu file")
                saved_path = move_to_save_dir(path, save_dir)
                for name in os.listdir(job_dir):
                    if name.lower().endswith((".vtt", ".srt", ".ass")):
                        subtitle_paths.append(move_to_save_dir(os.path.join(job_dir, name), save_dir))
                print("[%s] Đã lưu: %s" % (job_id[:8], saved_path))
            set_job(job_id, state="done", message="Hoàn tất",
                    path=None if saved_path else path,
                    saved_path=saved_path,
                    subtitle_paths=subtitle_paths,
                    filename=os.path.basename(saved_path or path),
                    finished=time.time())
        except JobCancelled:
            set_job(job_id, state="cancelled", message="Đã hủy tải", finished=time.time())
            shutil.rmtree(job_dir, ignore_errors=True)
        except Exception as exc:  # noqa: BLE001 - trả lỗi về cho extension
            print("[%s] Lỗi: %s" % (job_id[:8], ANSI_RE.sub("", str(exc))))
            set_job(job_id, state="error", message=clean_error(exc), finished=time.time())
            shutil.rmtree(job_dir, ignore_errors=True)


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    server_version = "YTQuickDownload/2.0"

    def log_message(self, fmt, *args):
        sys.stderr.write("[%s] %s\n" % (time.strftime("%H:%M:%S"), fmt % args))

    # Chặn website lạ gọi vào server local (CSRF / DNS rebinding).
    def _allowed(self):
        host = (self.headers.get("Host") or "").lower()
        if host not in ("127.0.0.1:%d" % PORT, "localhost:%d" % PORT):
            return False
        origin = self.headers.get("Origin")
        if origin and not origin.startswith("chrome-extension://"):
            return False
        return True

    def _cors_headers(self):
        origin = self.headers.get("Origin")
        if origin and origin.startswith("chrome-extension://"):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Access-Control-Allow-Private-Network", "true")

    def _send_json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self._cors_headers()
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        if not self._allowed():
            return self._send_json(403, {"error": "Bị từ chối."})
        self.send_response(204)
        self._cors_headers()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        if not self._allowed():
            return self._send_json(403, {"error": "Bị từ chối."})

        path = urlparse(self.path).path

        if path == "/health":
            with JOBS_LOCK:
                active_jobs = sum(1 for job in JOBS.values() if job.get("state") in ("queued", "running"))
            return self._send_json(200, {
                "ok": True,
                "port": PORT,
                "active_jobs": active_jobs,
                "yt_dlp": YTDLP_VERSION,
                "ffmpeg": bool(shutil.which("ffmpeg")),
                "deno": bool(shutil.which("deno")),
                "node": bool(shutil.which("node")),
            })

        if path == "/jobs":
            with JOBS_LOCK:
                jobs = [
                    {
                        "id": job_id,
                        "state": job.get("state"),
                        "message": job.get("message"),
                        "filename": job.get("filename"),
                        "saved_path": job.get("saved_path"),
                        "mode": job.get("mode"),
                        "created": job.get("created"),
                        "finished": job.get("finished"),
                    }
                    for job_id, job in JOBS.items()
                ]
            jobs.sort(key=lambda job: job.get("created") or 0, reverse=True)
            return self._send_json(200, {"jobs": jobs[:50]})

        m = re.fullmatch(r"/status/([0-9a-f]{32})", path)
        if m:
            job = get_job(m.group(1))
            if not job:
                return self._send_json(404, {"error": "Không tìm thấy tác vụ (server đã khởi động lại?)."})
            return self._send_json(200, {
                "state": job.get("state"),
                "message": job.get("message"),
                "percent": job.get("percent"),
                "downloaded_bytes": job.get("downloaded_bytes", 0),
                "total_bytes": job.get("total_bytes"),
                "speed": job.get("speed"),
                "eta": job.get("eta"),
                "filename": job.get("filename"),
                "saved_path": job.get("saved_path"),
            })

        m = re.fullmatch(r"/file/([0-9a-f]{32})", path)
        if m:
            return self._send_file(m.group(1))

        return self._send_json(404, {"error": "Không tìm thấy."})

    def _send_empty(self, status):
        self.send_response(status)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_HEAD(self):
        if not self._allowed():
            return self._send_empty(403)
        m = re.fullmatch(r"/file/([0-9a-f]{32})", urlparse(self.path).path)
        if not m:
            return self._send_empty(404)
        self._send_file(m.group(1), head_only=True)

    def _parse_range(self, size):
        """Trả về None (không có Range), "invalid", hoặc (start, end) gồm cả hai đầu."""
        header = self.headers.get("Range")
        if not header:
            return None
        m = re.fullmatch(r"bytes=(\d*)-(\d*)", header.strip())
        if not m or (not m.group(1) and not m.group(2)):
            return "invalid"
        if not m.group(1):  # "bytes=-N": N byte cuối
            start = max(0, size - int(m.group(2)))
            end = size - 1
        else:
            start = int(m.group(1))
            end = min(int(m.group(2)), size - 1) if m.group(2) else size - 1
        if start >= size or start > end:
            return "invalid"
        return start, end

    def _send_file(self, job_id, head_only=False):
        job = get_job(job_id)
        file_path = job.get("path") if job else None
        if not job or job.get("state") != "done" or not file_path or not os.path.isfile(file_path):
            if head_only:
                return self._send_empty(404)
            return self._send_json(404, {"error": "File không còn tồn tại."})

        size = os.path.getsize(file_path)
        rng = self._parse_range(size)
        if rng == "invalid":
            self.send_response(416)
            self.send_header("Content-Range", "bytes */%d" % size)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        start, end = rng if rng else (0, size - 1)

        name = os.path.basename(file_path)
        ascii_name = re.sub(r"[^A-Za-z0-9._ -]", "_", name)
        ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
        if name.lower().endswith(".mp3"):
            ctype = "audio/mpeg"

        try:
            self.send_response(206 if rng else 200)
            self.send_header("Content-Type", ctype)
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(max(0, end - start + 1)))
            if rng:
                self.send_header("Content-Range", "bytes %d-%d/%d" % (start, end, size))
            self.send_header(
                "Content-Disposition",
                "attachment; filename=\"%s\"; filename*=UTF-8''%s" % (ascii_name, quote(name)),
            )
            self.end_headers()
            if head_only:
                return

            with open(file_path, "rb") as f:
                f.seek(start)
                remaining = end - start + 1
                while remaining > 0:
                    chunk = f.read(min(256 * 1024, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        except ConnectionError:
            # Trình duyệt/trình tải đóng kết nối giữa chừng (Windows: WinError 10053). Bỏ qua.
            pass

    def do_POST(self):
        if not self._allowed():
            return self._send_json(403, {"error": "Bị từ chối."})
        request_path = urlparse(self.path).path
        cancel_match = re.fullmatch(r"/cancel/([0-9a-f]{32})", request_path)
        if cancel_match:
            job_id = cancel_match.group(1)
            with JOBS_LOCK:
                job = JOBS.get(job_id)
                cancel_event = CANCEL_EVENTS.get(job_id)
            if not job or not cancel_event:
                return self._send_json(404, {"error": "KhĂ´ng tĂ¬m tháº¥y tĂ¡c vá»¥."})
            cancel_event.set()
            set_job(job_id, message="Äang há»§y táº£i")
            return self._send_json(202, {"ok": True, "id": job_id})

        if request_path != "/start":
            return self._send_json(404, {"error": "Không tìm thấy."})

        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > 4096:
                raise ValueError("bad length")
            data = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(data, dict):
                raise ValueError("bad body")
        except (ValueError, UnicodeDecodeError):
            return self._send_json(400, {"error": "Yêu cầu không hợp lệ."})

        mode = data.get("mode")
        video_id = extract_video_id(str(data.get("url", "")))
        audio_quality = str(data.get("audio_quality", "320"))
        video_quality = int(data.get("video_quality", 1080))
        save_dir = data.get("save_dir")
        if save_dir is not None and not isinstance(save_dir, str):
            save_dir = None
        # LÆ°u trá»±c tiáº¿p vÃ o thÆ° má»¥c server Ä‘á»ƒ trÃ¡nh Chrome/IDM can thiá»‡p.
        # Chá»‰ dÃ¹ng chrome.downloads khi YTQD_SAVE_DIR=browser.
        if save_dir is None:
            save_dir = SAVE_DIR
        filename_template = str(data.get("filename_template", "%(title).70s")).strip()
        filename_template = filename_template.replace("/", "_").replace("\\", "_")
        subtitles = bool(data.get("subtitles", False))
        subtitle_language = str(data.get("subtitle_language", "vi")).strip() or "vi"
        if len(filename_template) > 180 or not filename_template:
            return self._send_json(400, {"error": "Mẫu tên file không hợp lệ."})
        if len(subtitle_language) > 20 or not re.fullmatch(r"[A-Za-z0-9,._-]+", subtitle_language):
            return self._send_json(400, {"error": "Ngôn ngữ phụ đề không hợp lệ."})
        if audio_quality not in ("128", "192", "256", "320"):
            return self._send_json(400, {"error": "Chất lượng MP3 không hợp lệ."})
        if video_quality not in (360, 480, 720, 1080):
            return self._send_json(400, {"error": "Chất lượng video không hợp lệ."})
        if mode not in ("mp3", "mp4") or not video_id:
            return self._send_json(400, {"error": "Link không phải video YouTube hoặc định dạng không hợp lệ."})

        cleanup_old_jobs()
        job_id = uuid.uuid4().hex
        cancel_event = threading.Event()
        with JOBS_LOCK:
            JOBS[job_id] = {
                "state": "queued",
                "message": "Đang xếp hàng",
                "percent": 0,
                "downloaded_bytes": 0,
                "total_bytes": None,
                "speed": None,
                "eta": None,
                "mode": mode,
                "video_id": video_id,
                "created": time.time(),
            }
        CANCEL_EVENTS[job_id] = cancel_event
        threading.Thread(
            target=run_job,
            args=(job_id, video_id, mode, save_dir, audio_quality, video_quality,
                  filename_template, subtitles, subtitle_language, cancel_event),
            daemon=True,
        ).start()
        print("[%s] Bắt đầu %s: %s" % (job_id[:8], mode.upper(), video_id))
        return self._send_json(202, {"id": job_id})


class QuietServer(ThreadingHTTPServer):
    """Không in traceback khi phía client tự đóng kết nối."""

    def handle_error(self, request, client_address):
        if isinstance(sys.exc_info()[1], ConnectionError):
            return
        super().handle_error(request, client_address)


def main():
    shutil.rmtree(BASE_DIR, ignore_errors=True)
    os.makedirs(BASE_DIR, exist_ok=True)

    if not shutil.which("ffmpeg"):
        print("⚠️  Không tìm thấy ffmpeg trong PATH. Cần ffmpeg để tạo MP3 và ghép MP4.")
    if not (shutil.which("deno") or shutil.which("node")):
        print("⚠️  Không thấy Deno/Node. YouTube hiện cần một JavaScript runtime (khuyên dùng Deno).")

    try:
        server = QuietServer((HOST, PORT), Handler)
    except OSError as exc:
        print("Không mở được cổng %d: %s" % (PORT, exc))
        print("Có thể server đang chạy ở cửa sổ khác, hoặc cổng đang bị chiếm.")
        sys.exit(1)

    print("✅ YT Quick Download server đang chạy tại http://%s:%d  (yt-dlp %s)" % (HOST, PORT, YTDLP_VERSION))
    if SAVE_DIR:
        print("   File tải xong sẽ được lưu vào: %s" % SAVE_DIR)
    else:
        print("   Chế độ browser: Chrome sẽ tải file về qua chrome.downloads.")
    print("   Giữ cửa sổ này mở khi dùng extension. Nhấn Ctrl+C để dừng.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nĐã dừng.")
    finally:
        server.server_close()
        shutil.rmtree(BASE_DIR, ignore_errors=True)


if __name__ == "__main__":
    main()
