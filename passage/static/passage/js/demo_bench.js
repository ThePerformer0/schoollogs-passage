/* Banc de démonstration multi-badges (DEMO_MODE=1) : codes signés par le serveur, zéro secret côté client. */
(function () {
  "use strict";

  const root = document.getElementById("demo-bench-root");
  if (!root) {
    return;
  }

  const codesUrl = root.getAttribute("data-codes-url") || "/responsable/demo/codes/";
  const timerBar = document.getElementById("demo-timer-bar");
  const timerLabel = document.getElementById("demo-timer-label");
  const modeSingleBtn = document.getElementById("demo-mode-single");
  const modeGridBtn = document.getElementById("demo-mode-grid");
  const singlePanel = document.getElementById("demo-single-panel");
  const gridPanel = document.getElementById("demo-grid-panel");
  const singleTitle = document.getElementById("demo-single-title");
  const singleQr = document.getElementById("demo-single-qr");
  const singleCodeText = document.getElementById("demo-single-code");
  const prevBtn = document.getElementById("demo-prev-btn");
  const nextBtn = document.getElementById("demo-next-btn");
  const gridContainer = document.getElementById("demo-grid-container");
  const errorBox = document.getElementById("demo-error-box");

  let badges = [];
  let currentIndex = 0;
  let stepSeconds = 30;
  let clockOffsetMs = 0;
  let lastFetchedStep = null;
  let isFetching = false;
  let viewMode = "single";

  function showError(message) {
    if (!errorBox) {
      return;
    }
    errorBox.textContent = message;
    errorBox.hidden = !message;
  }

  function renderSingle() {
    if (!singleQr || badges.length === 0) {
      return;
    }
    if (currentIndex >= badges.length) {
      currentIndex = 0;
    }
    if (currentIndex < 0) {
      currentIndex = badges.length - 1;
    }
    const item = badges[currentIndex];
    if (singleTitle) {
      singleTitle.textContent = item.label + " (" + (currentIndex + 1) + " / " + badges.length + ")";
    }
    window.PassageCrypto.renderQrIntoElement(singleQr, item.code);
    if (singleCodeText) {
      singleCodeText.textContent = item.code;
    }
  }

  function renderGrid() {
    if (!gridContainer) {
      return;
    }
    gridContainer.innerHTML = "";
    badges.forEach(function (item) {
      const tile = document.createElement("div");
      tile.className = "bench-tile";

      const qrBox = document.createElement("div");
      qrBox.className = "qr bench-qr";
      window.PassageCrypto.renderQrIntoElement(qrBox, item.code);

      const caption = document.createElement("span");
      caption.className = "bench-label mono";
      caption.textContent = item.label;

      tile.appendChild(qrBox);
      tile.appendChild(caption);
      gridContainer.appendChild(tile);
    });
  }

  function renderActiveView() {
    if (viewMode === "single") {
      if (singlePanel) {
        singlePanel.hidden = false;
      }
      if (gridPanel) {
        gridPanel.hidden = true;
      }
      if (modeSingleBtn) {
        modeSingleBtn.classList.remove("btn-secondary");
      }
      if (modeGridBtn) {
        modeGridBtn.classList.add("btn-secondary");
      }
      renderSingle();
    } else {
      if (singlePanel) {
        singlePanel.hidden = true;
      }
      if (gridPanel) {
        gridPanel.hidden = false;
      }
      if (modeSingleBtn) {
        modeSingleBtn.classList.add("btn-secondary");
      }
      if (modeGridBtn) {
        modeGridBtn.classList.remove("btn-secondary");
      }
      renderGrid();
    }
  }

  async function fetchFreshCodes() {
    if (isFetching) {
      return;
    }
    isFetching = true;
    try {
      const t0 = Date.now();
      const resp = await fetch(codesUrl, {
        method: "GET",
        credentials: "same-origin",
        cache: "no-store",
        headers: { "Accept": "application/json" }
      });
      if (!resp.ok) {
        showError("Impossible de charger les codes de démonstration.");
        return;
      }
      const t1 = Date.now();
      const data = await resp.json();
      const serverMs = Date.parse(data.server_time);
      if (Number.isFinite(serverMs)) {
        clockOffsetMs = serverMs - Math.round((t0 + t1) / 2);
      }
      if (data.code_step_seconds) {
        stepSeconds = Number(data.code_step_seconds) || 30;
      }
      lastFetchedStep = Number(data.time_step);
      badges = Array.isArray(data.badges) ? data.badges : [];
      showError("");
      renderActiveView();
    } catch (_err) {
      showError("Connexion au serveur interrompue pour le rafraîchissement des codes.");
    } finally {
      isFetching = false;
    }
  }

  function tick() {
    const adjustedSec = Math.floor((Date.now() + clockOffsetMs) / 1000);
    const currentStep = Math.floor(adjustedSec / stepSeconds);
    const elapsed = ((adjustedSec % stepSeconds) + stepSeconds) % stepSeconds;
    const left = stepSeconds - elapsed;

    if (timerBar) {
      const pct = Math.max(0, Math.min(100, (left / stepSeconds) * 100));
      timerBar.style.width = pct.toFixed(1) + "%";
    }
    if (timerLabel) {
      timerLabel.textContent = "Renouvellement dans " + left + " s";
    }

    if (lastFetchedStep !== null && currentStep !== lastFetchedStep) {
      fetchFreshCodes();
    }
  }

  if (modeSingleBtn) {
    modeSingleBtn.addEventListener("click", function () {
      viewMode = "single";
      renderActiveView();
    });
  }
  if (modeGridBtn) {
    modeGridBtn.addEventListener("click", function () {
      viewMode = "grid";
      renderActiveView();
    });
  }
  if (prevBtn) {
    prevBtn.addEventListener("click", function () {
      currentIndex -= 1;
      renderSingle();
    });
  }
  if (nextBtn) {
    nextBtn.addEventListener("click", function () {
      currentIndex += 1;
      renderSingle();
    });
  }

  document.addEventListener("keydown", function (event) {
    if (viewMode !== "single" || badges.length === 0) {
      return;
    }
    if (event.key === "ArrowRight" || event.key === " ") {
      event.preventDefault();
      currentIndex += 1;
      renderSingle();
    } else if (event.key === "ArrowLeft") {
      event.preventDefault();
      currentIndex -= 1;
      renderSingle();
    }
  });

  fetchFreshCodes().then(tick);
  window.setInterval(tick, 500);
})();
