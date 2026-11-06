importScripts("shared.js");

function notifyDownload(title, message) {
  if (!chrome.notifications) return;
  chrome.notifications.create({
    type: "basic",
    iconUrl: "logoYTB.png",
    title,
    message
  }).catch(() => {});
}

async function applySidePanelMode(enabled) {
  await chrome.action.setPopup({ popup: enabled ? "" : "popup.html" });
  await chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: enabled });
}

function createContextMenus() {
  chrome.contextMenus.removeAll().then(() => {
    chrome.contextMenus.create({
      id: "ytqd-mp3",
      title: "YT Quick Download: Tải MP3",
      contexts: ["page", "link"],
      documentUrlPatterns: ["https://*.youtube.com/*", "https://youtu.be/*"]
    });
    chrome.contextMenus.create({
      id: "ytqd-mp4",
      title: "YT Quick Download: Tải MP4",
      contexts: ["page", "link"],
      documentUrlPatterns: ["https://*.youtube.com/*", "https://youtu.be/*"]
    });
  });
}

async function downloadWithSettings(url, mode) {
  const settings = await chrome.storage.local.get({
    saveDir: "", audioQuality: "320", videoQuality: 1080,
    filenameTemplate: "%(title).70s", subtitles: false, subtitleLanguage: "vi"
  });
  return downloadFromServer(url, mode, () => {}, {
    saveDir: settings.saveDir || null,
    audioQuality: settings.audioQuality,
    videoQuality: Number(settings.videoQuality),
    filenameTemplate: settings.filenameTemplate,
    subtitles: Boolean(settings.subtitles),
    subtitleLanguage: settings.subtitleLanguage
  });
}

chrome.contextMenus.onClicked.addListener((info, tab) => {
  const url = info.linkUrl || (tab && tab.url);
  const mode = info.menuItemId === "ytqd-mp3" ? "mp3" : "mp4";
  if (!url || !extractVideoId(url)) return;
  downloadWithSettings(url, mode)
    .then((result) => notifyDownload("YT Quick Download", result.filename + " đã tải xong"))
    .catch((error) => notifyDownload("YT Quick Download", "Tải lỗi: " + error.message));
});

chrome.commands.onCommand.addListener(async (command) => {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  const mode = command === "download-mp3" ? "mp3" : "mp4";
  if (!tab || !tab.url || !extractVideoId(tab.url)) {
    notifyDownload("YT Quick Download", "Tab hiện tại không phải video YouTube.");
    return;
  }
  downloadWithSettings(tab.url, mode)
    .then((result) => notifyDownload("YT Quick Download", result.filename + " đã tải xong"))
    .catch((error) => notifyDownload("YT Quick Download", "Tải lỗi: " + error.message));
});

createContextMenus();

chrome.storage.local.get({ sidePanelEnabled: false }).then((settings) => {
  applySidePanelMode(Boolean(settings.sidePanelEnabled));
});

chrome.runtime.onInstalled.addListener(async () => {
  createContextMenus();
  const settings = await chrome.storage.local.get({ sidePanelEnabled: false });
  await applySidePanelMode(Boolean(settings.sidePanelEnabled));
});

chrome.runtime.onStartup.addListener(async () => {
  createContextMenus();
  const settings = await chrome.storage.local.get({ sidePanelEnabled: false });
  await applySidePanelMode(Boolean(settings.sidePanelEnabled));
});

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (!message || message.type !== "SET_SIDE_PANEL_MODE") return;

  applySidePanelMode(Boolean(message.enabled))
    .then(() => sendResponse({ ok: true }))
    .catch((error) => sendResponse({ ok: false, error: error.message }));
  return true;
});

// Nhận yêu cầu tải từ nút "⚡ Tải Nhanh" trong content.js
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message && message.type === "YT_QUICK_DOWNLOAD") {
    const tabId = sender.tab ? sender.tab.id : null;

    // Gửi tiến trình ngược lại cho nút trên trang.
    const onStatus = (text) => {
      if (tabId !== null) {
        chrome.tabs.sendMessage(tabId, { type: "YT_QUICK_PROGRESS", text }).catch(() => {});
      }
    };

    downloadFromServer(message.url, message.mode, onStatus)
      .then((result) => {
        notifyDownload("YT Quick Download", result.filename + " đã tải xong");
        sendResponse({ ok: true, filename: result.filename });
      })
      .catch((err) => sendResponse({ ok: false, error: err.message }));
    return true; // giữ kênh phản hồi bất đồng bộ
  }
});
