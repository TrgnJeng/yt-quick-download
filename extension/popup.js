const btnMp3 = document.getElementById("btnMp3");
const btnMp4 = document.getElementById("btnMp4");
const statusEl = document.getElementById("status");
const downloadTab = document.getElementById("downloadTab");
const settingsTab = document.getElementById("settingsTab");
const queueTab = document.getElementById("queueTab");
const historyTab = document.getElementById("historyTab");
const downloadPanel = document.getElementById("downloadPanel");
const settingsPanel = document.getElementById("settingsPanel");
const queuePanel = document.getElementById("queuePanel");
const historyPanel = document.getElementById("historyPanel");
const saveDirEl = document.getElementById("saveDir");
const audioQualityEl = document.getElementById("audioQuality");
const videoQualityEl = document.getElementById("videoQuality");
const filenameTemplateEl = document.getElementById("filenameTemplate");
const subtitlesEl = document.getElementById("subtitles");
const subtitleLanguageEl = document.getElementById("subtitleLanguage");
const advancedSettingLabels = [
  filenameTemplateEl && filenameTemplateEl.closest("label"),
  subtitlesEl && subtitlesEl.closest("label"),
  subtitleLanguageEl && subtitleLanguageEl.closest("label"),
  document.querySelectorAll(".hint")[document.querySelectorAll(".hint").length - 1]
].filter(Boolean);
const sidePanelEnabledEl = document.getElementById("sidePanelEnabled");
const themeToggleEl = document.getElementById("themeToggle");
const progressWrap = document.getElementById("progressWrap");
const progressBar = document.getElementById("progressBar");
const progressLabel = document.getElementById("progressLabel");
const btnAddQueue = document.getElementById("btnAddQueue");
const btnStartQueue = document.getElementById("btnStartQueue");
const btnClearQueue = document.getElementById("btnClearQueue");
const btnClearHistory = document.getElementById("btnClearHistory");
const queueModeEl = document.getElementById("queueMode");
const queueList = document.getElementById("queueList");
const historyList = document.getElementById("historyList");
const queueCount = document.getElementById("queueCount");
const bulkUrlsEl = document.getElementById("bulkUrls");
const bulkDropZone = document.getElementById("bulkDropZone");
const btnAddBulkQueue = document.getElementById("btnAddBulkQueue");
const retryButton = document.getElementById("retryButton");
const cancelDownloadButton = document.getElementById("cancelDownloadButton");
const dependencyStatusEl = document.getElementById("dependencyStatus");

function applyTheme(theme) {
  document.body.classList.toggle("dark", theme === "dark");
}

async function getActiveWindowId() {
  const currentWindow = await chrome.windows.getLastFocused();
  if (!currentWindow || typeof currentWindow.id !== "number" || currentWindow.id < 0) {
    throw new Error("Không xác định được cửa sổ Chrome hiện tại.");
  }
  return currentWindow.id;
}

let queueItems = [];
let historyItems = [];
let queueRunning = false;
let lastFailedRequest = null;
let currentJobId = null;
let cancelRequested = false;

function setProgress(percent) {
  const value = Math.max(0, Math.min(100, Number(percent) || 0));
  progressWrap.hidden = false;
  progressBar.style.width = value + "%";
  progressLabel.textContent = value + "%";
}

function updateProgressFromMessage(message) {
  const match = String(message).match(/(\d+)%/);
  if (match) setProgress(Number(match[1]));
}

function setStatus(text, type) {
  statusEl.textContent = text;
  statusEl.className = "status" + (type ? " " + type : "");
  updateProgressFromMessage(text);
}

function setBusy(busy) {
  btnMp3.disabled = busy;
  btnMp4.disabled = busy;
  cancelDownloadButton.hidden = !busy || !currentJobId;
  cancelDownloadButton.disabled = cancelRequested;
}

function setCurrentJobId(jobId) {
  currentJobId = jobId || null;
  cancelDownloadButton.hidden = !currentJobId;
}

function notifyDownload(title, message) {
  if (!chrome.notifications) return;
  chrome.notifications.create({
    type: "basic",
    iconUrl: "icon.svg",
    title,
    message
  }).catch(() => {});
}

function updateDependencyStatus(health) {
  const missing = [];
  if (!health.ffmpeg) missing.push("ffmpeg");
  if (!health.deno && !health.node) missing.push("Deno/Node");
  dependencyStatusEl.className = "dependency-status" + (missing.length ? " warning" : " ready");
  dependencyStatusEl.textContent = missing.length
    ? "⚠️ Thiếu: " + missing.join(", ")
    : "✅ ffmpeg và JavaScript runtime đã sẵn sàng";
}

async function handleDownload(mode, requestedUrl, requestedTitle) {
  currentJobId = null;
  cancelRequested = false;
  setBusy(true);
  retryButton.hidden = true;
  setProgress(0);
  setStatus("⏳ Đang kiểm tra trang hiện tại...", "info");

  try {
    const [activeTab] = await chrome.tabs.query({ active: true, currentWindow: true });
    const pageUrl = requestedUrl || (activeTab && activeTab.url);
    const pageTitle = requestedTitle || (activeTab && activeTab.title);

    if (!pageUrl || !extractVideoId(pageUrl)) {
      setStatus("❌ Tab hiện tại không phải trang xem video YouTube.", "error");
      return;
    }

    const result = await downloadFromServer(pageUrl, mode, (msg) => setStatus(msg, "info"), {
      saveDir: saveDirEl.value.trim() || null,
      audioQuality: audioQualityEl.value,
      videoQuality: Number(videoQualityEl.value),
      filenameTemplate: filenameTemplateEl.value.trim() || "%(title).70s",
      subtitles: subtitlesEl.checked,
      subtitleLanguage: subtitleLanguageEl.value.trim() || "vi",
      onJob: setCurrentJobId
    });
    setStatus(
      result.savedPath
        ? "✅ Đã lưu: " + result.filename
        : "✅ Đã gửi tải xuống: " + result.filename,
      "success"
    );
    setProgress(100);
    const savedMessage = result.savedPath
      ? " được lưu ở " + result.savedPath
      : " vào thư mục tải xuống của Chrome";
    setStatus("✅ " + result.filename + " đã tải xong" + savedMessage, "success");
    await addHistory({
      title: pageTitle || result.filename,
      url: pageUrl,
      mode,
      filename: result.filename,
      savedPath: result.savedPath || "Chrome Downloads",
      time: Date.now()
    });
    notifyDownload("YT Quick Download", result.filename + " đã tải xong");
    lastFailedRequest = null;
  } catch (err) {
    if (cancelRequested) {
      retryButton.hidden = true;
      setStatus("Đã hủy tải.", "info");
      return;
    }
    lastFailedRequest = { mode, url: requestedUrl || null, title: requestedTitle || null };
    retryButton.hidden = false;
    setStatus("❌ " + err.message, "error");
  } finally {
    setCurrentJobId(null);
    setBusy(false);
  }
}

btnMp3.addEventListener("click", () => handleDownload("mp3"));
btnMp4.addEventListener("click", () => handleDownload("mp4"));

retryButton.addEventListener("click", () => {
  if (!lastFailedRequest) return;
  handleDownload(lastFailedRequest.mode, lastFailedRequest.url, lastFailedRequest.title);
});

cancelDownloadButton.addEventListener("click", async () => {
  if (!currentJobId || cancelRequested) return;
  cancelRequested = true;
  cancelDownloadButton.disabled = true;
  try {
    await serverFetch("/cancel/" + currentJobId, { method: "POST" });
    setStatus("⏳ Đang hủy tải...", "info");
  } catch (error) {
    cancelRequested = false;
    cancelDownloadButton.disabled = false;
    setStatus("Không thể hủy tải: " + error.message, "error");
  }
});

function extractBulkUrls(text) {
  return String(text || "")
    .split(/[\s,]+/)
    .map((value) => value.trim())
    .filter((value) => extractVideoId(value));
}

async function addBulkUrlsToQueue(text) {
  const urls = [...new Set(extractBulkUrls(text))];
  if (!urls.length) {
    setStatus("❌ Không tìm thấy link YouTube hợp lệ.", "error");
    return;
  }
  let added = 0;
  urls.forEach((url) => {
    if (queueItems.some((item) => item.url === url)) return;
    queueItems.push({ url, title: "YouTube " + extractVideoId(url), mode: queueModeEl.value });
    added += 1;
  });
  await saveQueue();
  renderQueue();
  bulkUrlsEl.value = "";
  setStatus(`✅ Đã thêm ${added} link vào hàng đợi.`, "success");
}

btnAddBulkQueue.addEventListener("click", () => addBulkUrlsToQueue(bulkUrlsEl.value));
bulkDropZone.addEventListener("dragover", (event) => {
  event.preventDefault();
  bulkDropZone.classList.add("dragging");
});
bulkDropZone.addEventListener("dragleave", () => bulkDropZone.classList.remove("dragging"));
bulkDropZone.addEventListener("drop", async (event) => {
  event.preventDefault();
  bulkDropZone.classList.remove("dragging");
  const file = event.dataTransfer.files[0];
  if (!file) return;
  if (file.name.toLowerCase().endsWith(".txt")) {
    await addBulkUrlsToQueue(await file.text());
  } else {
    setStatus("❌ Chỉ hỗ trợ file .txt.", "error");
  }
});

function showPanel(panel) {
  const panels = { download: downloadPanel, queue: queuePanel, history: historyPanel, settings: settingsPanel };
  const tabs = { download: downloadTab, queue: queueTab, history: historyTab, settings: settingsTab };
  Object.entries(panels).forEach(([name, element]) => { element.hidden = name !== panel; });
  Object.entries(tabs).forEach(([name, element]) => { element.classList.toggle("active", name === panel); });
  advancedSettingLabels.forEach((element) => { element.hidden = panel !== "settings"; });
  if (panel === "queue") renderQueue();
  if (panel === "history") renderHistory();
}

downloadTab.addEventListener("click", () => showPanel("download"));
queueTab.addEventListener("click", () => showPanel("queue"));
historyTab.addEventListener("click", () => showPanel("history"));
settingsTab.addEventListener("click", () => showPanel("settings"));
showPanel("download");

async function loadLists() {
  const data = await chrome.storage.local.get({ queueItems: [], historyItems: [] });
  queueItems = Array.isArray(data.queueItems) ? data.queueItems : [];
  historyItems = Array.isArray(data.historyItems) ? data.historyItems : [];
  renderQueue();
  renderHistory();
}

async function saveQueue() {
  await chrome.storage.local.set({ queueItems });
}

async function addHistory(item) {
  historyItems = [item, ...historyItems].slice(0, 50);
  await chrome.storage.local.set({ historyItems });
  renderHistory();
}

function renderQueue() {
  queueCount.textContent = String(queueItems.length);
  queueList.replaceChildren();
  if (!queueItems.length) {
    queueList.innerHTML = '<div class="list-empty">Chưa có video trong hàng đợi.</div>';
    return;
  }
  queueItems.forEach((item, index) => {
    const row = document.createElement("div");
    row.className = "queue-item";
    row.innerHTML = '<span class="item-text"></span><button class="remove-item" title="Xóa">×</button>';
    row.querySelector(".item-text").textContent = item.title || item.url;
    row.querySelector(".remove-item").addEventListener("click", async () => {
      queueItems.splice(index, 1);
      await saveQueue();
      renderQueue();
    });
    queueList.appendChild(row);
  });
}

function renderHistory() {
  historyList.replaceChildren();
  if (!historyItems.length) {
    historyList.innerHTML = '<div class="list-empty">Chưa có lịch sử tải.</div>';
    return;
  }
  historyItems.forEach((item) => {
    const row = document.createElement("div");
    row.className = "history-item";
    const date = new Date(item.time).toLocaleString("vi-VN");
    row.innerHTML = '<span class="item-text"></span>';
    row.querySelector(".item-text").textContent = item.title || item.filename;
    const meta = document.createElement("span");
    meta.className = "item-meta";
    meta.textContent = `${item.mode.toUpperCase()} · ${date}`;
    row.querySelector(".item-text").appendChild(meta);
    historyList.appendChild(row);
  });
}

btnAddQueue.addEventListener("click", async () => {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab || !tab.url || !extractVideoId(tab.url)) {
    setStatus("❌ Tab hiện tại không phải trang xem video YouTube.", "error");
    return;
  }
  if (queueItems.some((item) => item.url === tab.url)) {
    setStatus("ℹ️ Video này đã có trong hàng đợi.", "info");
    return;
  }
  queueItems.push({ url: tab.url, title: tab.title || tab.url, mode: queueModeEl.value });
  await saveQueue();
  renderQueue();
  setStatus("✅ Đã thêm video vào hàng đợi.", "success");
});

btnClearQueue.addEventListener("click", async () => {
  if (queueRunning) return;
  queueItems = [];
  await saveQueue();
  renderQueue();
});

btnClearHistory.addEventListener("click", async () => {
  historyItems = [];
  await chrome.storage.local.set({ historyItems });
  renderHistory();
});

btnStartQueue.addEventListener("click", async () => {
  if (queueRunning || !queueItems.length) return;
  queueRunning = true;
  setBusy(true);
  try {
    while (queueItems.length) {
      const item = queueItems[0];
      setStatus(`⏳ Đang tải hàng đợi: ${item.title}`, "info");
      const result = await downloadFromServer(item.url, item.mode, (msg) => setStatus(msg, "info"), {
        saveDir: saveDirEl.value.trim() || null,
        audioQuality: audioQualityEl.value,
        videoQuality: Number(videoQualityEl.value),
        filenameTemplate: filenameTemplateEl.value.trim() || "%(title).70s",
        subtitles: subtitlesEl.checked,
        subtitleLanguage: subtitleLanguageEl.value.trim() || "vi",
        onJob: setCurrentJobId
      });
      await addHistory({ title: item.title, url: item.url, mode: item.mode, filename: result.filename, savedPath: result.savedPath || "Chrome Downloads", time: Date.now() });
      queueItems.shift();
      await saveQueue();
      renderQueue();
    }
    setProgress(100);
    setStatus("✅ Đã tải xong toàn bộ hàng đợi.", "success");
    notifyDownload("YT Quick Download", "Đã tải xong toàn bộ hàng đợi.");
  } catch (err) {
    setStatus("❌ Hàng đợi dừng: " + err.message, "error");
  } finally {
    queueRunning = false;
    setBusy(false);
  }
});

chrome.storage.local.get(
  { saveDir: "", audioQuality: "320", videoQuality: 1080, filenameTemplate: "%(title).70s", subtitles: false, subtitleLanguage: "vi", sidePanelEnabled: false, theme: "light" },
  (settings) => {
    saveDirEl.value = settings.saveDir;
    audioQualityEl.value = settings.audioQuality;
    videoQualityEl.value = String(settings.videoQuality);
    filenameTemplateEl.value = settings.filenameTemplate;
    subtitlesEl.checked = Boolean(settings.subtitles);
    subtitleLanguageEl.value = settings.subtitleLanguage;
    sidePanelEnabledEl.checked = Boolean(settings.sidePanelEnabled);
    themeToggleEl.checked = settings.theme === "dark";
    applyTheme(settings.theme);
  }
);

loadLists();

[saveDirEl, audioQualityEl, videoQualityEl, filenameTemplateEl, subtitlesEl, subtitleLanguageEl, sidePanelEnabledEl, themeToggleEl].forEach((element) => {
  element.addEventListener("change", () => {
    chrome.storage.local.set({
      saveDir: saveDirEl.value.trim(),
      audioQuality: audioQualityEl.value,
      videoQuality: Number(videoQualityEl.value),
      filenameTemplate: filenameTemplateEl.value.trim() || "%(title).70s",
      subtitles: subtitlesEl.checked,
      subtitleLanguage: subtitleLanguageEl.value.trim() || "vi",
      sidePanelEnabled: sidePanelEnabledEl.checked,
      theme: themeToggleEl.checked ? "dark" : "light"
    });

    if (element === themeToggleEl) applyTheme(themeToggleEl.checked ? "dark" : "light");

    if (element === sidePanelEnabledEl) {
      const enabled = sidePanelEnabledEl.checked;
      const isSidePanel = location.pathname.endsWith("sidepanel.html");
      chrome.runtime.sendMessage({
        type: "SET_SIDE_PANEL_MODE",
        enabled
      }).then(async () => {
        if (!enabled && isSidePanel) {
          if (!chrome.sidePanel.close || !chrome.action.openPopup) {
            throw new Error("Chrome cần phiên bản mới để tự chuyển sang Popup.");
          }
          const windowId = await getActiveWindowId();
          await chrome.sidePanel.close({ windowId });
          await chrome.action.openPopup({ windowId });
        }
      }).catch((error) => setStatus("Không thể chuyển sang Popup: " + error.message, "error"));
      if (enabled && location.pathname.endsWith("popup.html")) {
        getActiveWindowId().then((windowId) => chrome.sidePanel.open({ windowId }))
          .then(() => window.close())
          .catch((error) => setStatus("KhÃ´ng má»Ÿ Ä‘Æ°á»£c Side Panel: " + error.message, "error"));
      }
    }
  });
});

// Khi mở popup: kiểm tra máy chủ yt-dlp đã chạy chưa.
(async () => {
  try {
    const health = await serverFetch("/health");
    updateDependencyStatus(health);
    if (health && health.ffmpeg === false) {
      setStatus("⚠️ Máy chủ đang chạy nhưng chưa có ffmpeg (cần để tạo MP3/MP4).", "error");
    }
  } catch (err) {
    setStatus("⚠️ " + err.message, "error");
  }
})();
