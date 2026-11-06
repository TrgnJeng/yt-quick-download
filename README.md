# YouTube Quick Download (yt-dlp)

Extension Chrome (Manifest V3) + một máy chủ Python nhỏ chạy trên máy bạn.
Extension gửi link YouTube tới `http://127.0.0.1:8765`, máy chủ dùng **yt-dlp** và **ffmpeg** để tải/chuyển đổi, rồi Chrome tải file về.

Chỉ nên dùng cho nội dung bạn có quyền tải. Tải video YouTube có thể vi phạm điều khoản dịch vụ của YouTube.

## 1. Cài các thành phần (làm một lần)

### Windows

1. Cài **Python 3.10+** từ https://www.python.org/downloads/ (nhớ tick **Add python.exe to PATH**).
2. Mở PowerShell hoặc Terminal rồi chạy:

```
winget install Gyan.FFmpeg
winget install DenoLand.Deno
```

3. **Đóng rồi mở lại** cửa sổ terminal (để nhận PATH mới), sau đó vào thư mục `server` và chạy:

```
py -m pip install -U -r requirements.txt
```

### macOS

```
brew install ffmpeg deno
cd server
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -U -r requirements.txt
```

(Deno là JavaScript runtime mà yt-dlp cần để lấy video YouTube; ffmpeg dùng để tạo MP3 và ghép MP4.)

## 2. Chạy máy chủ (mỗi lần dùng)

- Windows: bấm đúp `server/start-server.bat`, hoặc trong thư mục `server` chạy `py server.py`.
- macOS: trong thư mục `server`, chạy `source .venv/bin/activate` rồi `python server.py`.

Thấy dòng `✅ YT Quick Download server đang chạy tại http://127.0.0.1:8765` là được. **Giữ cửa sổ này mở** khi dùng extension.

## 3. Nạp extension vào Chrome

1. Vào `chrome://extensions`, bật **Developer mode**.
2. Bấm **Load unpacked** và chọn thư mục `yt-quick-download` (thư mục chứa `manifest.json`).
3. Nếu đã nạp bản cũ: bấm nút ⟳ (Reload) trên thẻ extension, rồi tải lại tab YouTube.

## 4. Dùng

Mở một video YouTube rồi bấm biểu tượng extension. Trong tab **Cài đặt**, có thể chọn thư mục lưu
trên máy, chất lượng MP3 và chất lượng video tối đa. Để trống thư mục lưu, Chrome sẽ hỏi vị trí
cho mỗi lần tải. Sau đó quay lại tab **Tải xuống** và bấm MP3 hoặc MP4. Nút **⚡ Tải Nhanh**
trên trang YouTube vẫn dùng thiết lập mặc định và tải qua Chrome.

## Khắc phục lỗi

| Thông báo | Cách xử lý |
|---|---|
| Không kết nối được máy chủ yt-dlp | Chưa chạy `server.py`, hoặc cổng 8765 bị chiếm. |
| Chưa cài ffmpeg | Cài ffmpeg, mở lại terminal rồi chạy lại server. |
| Không lấy được định dạng / Requested format is not available | Cài Deno, rồi chạy `python -m pip install -U yt-dlp yt-dlp-ejs` và khởi động lại server. |
| YouTube yêu cầu đăng nhập (Sign in to confirm you're not a bot) | Đăng nhập YouTube trên Chrome, rồi chạy server với biến `YTQD_COOKIES_BROWSER=chrome` (Windows PowerShell: `$env:YTQD_COOKIES_BROWSER="chrome"; py server.py`). |
| Đang chạy tốt rồi bỗng lỗi | YouTube đổi cách chặn: cập nhật bằng `python -m pip install -U yt-dlp yt-dlp-ejs` rồi chạy lại server. |

## Bảo mật

Máy chủ chỉ nghe ở `127.0.0.1`, chỉ nhận link YouTube và từ chối yêu cầu đến từ website lạ. File tạm nằm trong thư mục tạm của hệ thống và tự xóa sau 30 phút.
