(() => {
  const WRAP_ID = "yt-quick-dl-wrap";
  const STYLE_ID = "yt-quick-dl-style";

  function isWatchPage() {
    return location.pathname === "/watch";
  }

  function injectStyle() {
    if (document.getElementById(STYLE_ID)) return;
    const style = document.createElement("style");
    style.id = STYLE_ID;
    style.textContent = `
      #${WRAP_ID} {
        position: relative;
        display: inline-flex;
        margin-left: 8px;
        flex-shrink: 0;
        z-index: 100000;
        pointer-events: auto;
      }
      #${WRAP_ID} .ytqd-main {
        height: 36px;
        padding: 0 16px;
        border: none;
        border-radius: 18px;
        font-size: 14px;
        font-weight: 500;
        font-family: "Roboto", "Arial", sans-serif;
        cursor: pointer;
        color: #fff;
        background: linear-gradient(135deg, #ff3d5a, #ff8a3d);
        white-space: nowrap;
      }
      #${WRAP_ID} .ytqd-main:hover { filter: brightness(1.12); }
      #${WRAP_ID} .ytqd-main:disabled { opacity: 0.7; cursor: wait; }
      #${WRAP_ID} .ytqd-menu {
        display: none;
        position: absolute;
        top: 42px;
        left: 0;
        min-width: 170px;
        z-index: 9999;
        padding: 6px;
        border-radius: 12px;
        background: var(--yt-spec-menu-background, #282828);
        box-shadow: 0 4px 24px rgba(0, 0, 0, 0.45);
      }
      #${WRAP_ID} .ytqd-menu.open { display: block; }
      #${WRAP_ID} .ytqd-item {
        display: block;
        width: 100%;
        padding: 9px 12px;
        border: none;
        border-radius: 8px;
        background: transparent;
        color: var(--yt-spec-text-primary, #f1f1f1);
        font-size: 14px;
        text-align: left;
        cursor: pointer;
      }
      #${WRAP_ID} .ytqd-item:hover {
        background: var(--yt-spec-badge-chip-background, rgba(255, 255, 255, 0.12));
      }
    `;
    document.head.appendChild(style);
  }

  async function startDownload(mode, mainBtn, menu) {
    menu.classList.remove("open");
    const originalText = "⚡ Tải Nhanh";
    mainBtn.disabled = true;
    mainBtn.textContent = "⏳ Đang xử lý...";

    let finalText = "✅ Đã gửi tải";
    let onProgress = null;
    try {
      // Nhận tiến trình (ví dụ "⏳ Đang tải 45%") từ background.js để hiện lên nút.
      onProgress = (message) => {
        if (message && message.type === "YT_QUICK_PROGRESS") {
          mainBtn.textContent = message.text;
        }
      };
      chrome.runtime.onMessage.addListener(onProgress);

      const res = await chrome.runtime.sendMessage({
        type: "YT_QUICK_DOWNLOAD",
        url: location.href,
        mode
      });
      if (!res || !res.ok) {
        finalText = "❌ " + ((res && res.error) || "Lỗi không xác định");
        console.warn("[YT Quick Download]", res && res.error);
      }
    } catch (err) {
      finalText = "❌ Hãy tải lại trang (extension vừa cập nhật)";
      console.warn("[YT Quick Download]", err);
    } finally {
      try {
        if (onProgress) chrome.runtime.onMessage.removeListener(onProgress);
      } catch (e) {
        /* extension context đã bị vô hiệu hóa */
      }
    }

    mainBtn.textContent = finalText;
    setTimeout(() => {
      mainBtn.textContent = originalText;
      mainBtn.disabled = false;
    }, 4000);
  }

  function buildWidget() {
    const wrap = document.createElement("div");
    wrap.id = WRAP_ID;

    const mainBtn = document.createElement("button");
    mainBtn.className = "ytqd-main";
    mainBtn.type = "button";
    mainBtn.textContent = "⚡ Tải Nhanh";

    const menu = document.createElement("div");
    menu.className = "ytqd-menu";

    const itemMp3 = document.createElement("button");
    itemMp3.className = "ytqd-item";
    itemMp3.type = "button";
    itemMp3.textContent = "🎵 Tải MP3 (Audio)";

    const itemMp4 = document.createElement("button");
    itemMp4.className = "ytqd-item";
    itemMp4.type = "button";
    itemMp4.textContent = "🎬 Tải MP4 (Video)";

    menu.appendChild(itemMp3);
    menu.appendChild(itemMp4);
    wrap.appendChild(mainBtn);
    wrap.appendChild(menu);

    const openMenu = (e) => {
      e.preventDefault();
      e.stopPropagation();
      menu.classList.toggle("open");
    };
    mainBtn.addEventListener("pointerdown", openMenu, true);

    const downloadMp3 = (e) => {
      e.preventDefault();
      e.stopPropagation();
      startDownload("mp3", mainBtn, menu);
    };
    const downloadMp4 = (e) => {
      e.preventDefault();
      e.stopPropagation();
      startDownload("mp4", mainBtn, menu);
    };
    itemMp3.addEventListener("click", downloadMp3, true);
    itemMp4.addEventListener("click", downloadMp4, true);
    document.addEventListener("click", (e) => {
      if (!wrap.contains(e.target)) menu.classList.remove("open");
    });

    return wrap;
  }

  function removeWidget() {
    const existing = document.getElementById(WRAP_ID);
    if (existing) existing.remove();
  }

  function inject() {
    if (!isWatchPage()) {
      removeWidget();
      return;
    }
    if (document.getElementById(WRAP_ID)) return;

    const target =
      document.querySelector("ytd-watch-metadata #top-level-buttons-computed") ||
      document.querySelector("#top-level-buttons-computed") ||
      document.querySelector("ytd-watch-metadata #actions");

    if (!target) return;

    injectStyle();
    target.appendChild(buildWidget());
  }

  // YouTube là SPA: theo dõi điều hướng và thay đổi DOM để chèn lại nút khi cần.
  let timer = null;
  function scheduleInject() {
    clearTimeout(timer);
    timer = setTimeout(inject, 300);
  }

  window.addEventListener("yt-navigate-finish", scheduleInject);
  new MutationObserver(scheduleInject).observe(document.body, {
    childList: true,
    subtree: true
  });

  inject();
})();
