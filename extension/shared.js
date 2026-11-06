// Địa chỉ máy chủ yt-dlp chạy trên máy bạn (xem thư mục server/).
// Launcher có thể chuyển sang cổng dự phòng nếu 8765 đang bị chiếm.
const SERVER_PORTS = Array.from({ length: 16 }, (_, index) => 8765 + index);
let activeServerUrl = "http://127.0.0.1:8765";
const POLL_INTERVAL_MS = 1000;
const JOB_TIMEOUT_MS = 15 * 60 * 1000;

/**
 * Trích xuất video ID từ URL YouTube.
 * Hỗ trợ: youtube.com/watch?v=, youtu.be/, /shorts/, /live/, /embed/
 * Trả về null nếu URL không phải trang xem video.
 */
function extractVideoId(rawUrl) {
  try {
    const u = new URL(rawUrl);
    const host = u.hostname.replace(/^(www\.|m\.|music\.)/, "");
    let id = null;

    if (host === "youtu.be") {
      id = u.pathname.slice(1).split("/")[0];
    } else if (host === "youtube.com") {
      if (u.pathname === "/watch") {
        id = u.searchParams.get("v");
      } else {
        const m = u.pathname.match(/^\/(shorts|live|embed)\/([^/?]+)/);
        if (m) id = m[2];
      }
    }
    return id && /^[\w-]{11}$/.test(id) ? id : null;
  } catch (e) {
    return null;
  }
}

function sanitizeFilename(name) {
  return String(name || "").replace(/[\\/:*?"<>|\u0000-\u001f]/g, "_").trim();
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function formatBytes(bytes) {
  const value = Number(bytes);
  if (!Number.isFinite(value) || value <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  const index = Math.min(Math.floor(Math.log(value) / Math.log(1024)), units.length - 1);
  return (value / Math.pow(1024, index)).toFixed(index === 0 ? 0 : 1) + " " + units[index];
}

function formatEta(seconds) {
  const value = Number(seconds);
  if (!Number.isFinite(value) || value < 0) return "--:--";
  const minutes = Math.floor(value / 60);
  const remaining = Math.floor(value % 60);
  return String(minutes).padStart(2, "0") + ":" + String(remaining).padStart(2, "0");
}

function formatProgress(job) {
  const percent = Number(job && job.percent);
  if (!Number.isFinite(percent)) return job && job.message ? job.message : "Đang tải...";
  const speed = Number(job.speed) > 0 ? formatBytes(job.speed) + "/s" : "đang tính tốc độ";
  const eta = Number(job.eta) >= 0 ? "còn " + formatEta(job.eta) : "đang tính thời gian";
  return "Đang tải " + Math.round(percent) + "% · " + speed + " · " + eta;
}

/**
 * Gọi máy chủ yt-dlp và trả về JSON. Ném lỗi có thông báo dễ hiểu.
 */
async function serverFetch(path, options) {
  let response;
  const ports = [
    Number(activeServerUrl.split(":").pop()),
    ...SERVER_PORTS
  ].filter((port, index, all) => all.indexOf(port) === index);
  for (const port of ports) {
    const baseUrl = "http://127.0.0.1:" + port;
    try {
      response = await fetch(baseUrl + path, options);
      activeServerUrl = baseUrl;
      break;
    } catch (e) {
      // Thử cổng dự phòng tiếp theo.
    }
  }
  if (!response) {
    throw new Error("Không kết nối được máy chủ yt-dlp. Hãy chạy server.py rồi thử lại.");
  }

  let data = null;
  try {
    data = await response.json();
  } catch (e) {
    data = null;
  }

  if (!response.ok) {
    throw new Error((data && data.error) || "Máy chủ báo lỗi (HTTP " + response.status + ").");
  }
  return data;
}

/**
 * Nhờ máy chủ yt-dlp tải video/âm thanh, đợi xong rồi kích hoạt chrome.downloads.
 * @param {string} pageUrl  URL trang YouTube
 * @param {"mp3"|"mp4"} mode
 * @param {(msg: string) => void} [onStatus]
 * @param {{saveDir?: string|null, audioQuality?: string, videoQuality?: number}} [settings]
 * @returns {Promise<{filename: string, downloadId: number}>}
 */
async function downloadFromServer(pageUrl, mode, onStatus, settings) {
  const report = typeof onStatus === "function" ? onStatus : () => {};

  const videoId = extractVideoId(pageUrl);
  if (!videoId) {
    throw new Error("Đây không phải trang xem video YouTube.");
  }
  const cleanUrl = "https://www.youtube.com/watch?v=" + videoId;

  report("⏳ Đang gửi yêu cầu...");
  const started = await serverFetch("/start", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      url: cleanUrl,
      mode,
      save_dir: settings && settings.saveDir ? settings.saveDir : null,
      audio_quality: (settings && settings.audioQuality) || "320",
      video_quality: (settings && settings.videoQuality) || 1080,
      filename_template: (settings && settings.filenameTemplate) || "%(title).70s",
      subtitles: Boolean(settings && settings.subtitles),
      subtitle_language: (settings && settings.subtitleLanguage) || "vi"
    })
  });
  const jobId = started.id;
  if (settings && typeof settings.onJob === "function") settings.onJob(jobId);

  const deadline = Date.now() + JOB_TIMEOUT_MS;
  let job = null;
  let lastMessage = "";
  let lastProgressMessage = "";

  for (;;) {
    if (Date.now() > deadline) {
      throw new Error("Quá thời gian chờ (15 phút).");
    }
    await sleep(POLL_INTERVAL_MS);

    // Gọi một API của extension để service worker không bị Chrome tắt khi đang chờ.
    try {
      await chrome.runtime.getPlatformInfo();
    } catch (e) {
      /* bỏ qua */
    }

    job = await serverFetch("/status/" + jobId);

    if (job.state === "downloading") {
      const progressMessage = formatProgress(job);
      if (progressMessage !== lastProgressMessage) {
        lastProgressMessage = progressMessage;
        report("⏳ " + progressMessage);
      }
    }

    if (job.state === "cancelled") {
      throw new Error(job.message || "Đã hủy tải.");
    }
    if (job.state === "error") {
      throw new Error(job.message || "Máy chủ không tải được video.");
    }
    if (job.state === "done") {
      break;
    }
    if (job.message && job.message !== lastMessage) {
      lastMessage = job.message;
      report("⏳ " + job.message);
    }
  }

  const ext = mode === "mp3" ? "mp3" : "mp4";
  const filename = sanitizeFilename(job.filename) || "youtube_" + videoId + "." + ext;

  if (job.saved_path) {
    return { filename, savedPath: job.saved_path };
  }

  report("⬇️ Đang bắt đầu tải xuống...");

  const downloadId = await new Promise((resolve, reject) => {
    chrome.downloads.download(
      { url: activeServerUrl + "/file/" + jobId, filename, saveAs: false },
      (id) => {
        if (chrome.runtime.lastError || id === undefined) {
          reject(new Error((chrome.runtime.lastError && chrome.runtime.lastError.message) || "Không thể bắt đầu tải."));
        } else {
          resolve(id);
        }
      }
    );
  });

  return { filename, downloadId };
}
