/* Page « Mon badge » : génération locale hors ligne du QR rotatif + recalage horloge serveur. */
(function () {
  "use strict";

  const BADGE_STORAGE_KEY = "passage_my_badge_v1";

  const activeSection = document.getElementById("my-badge-active");
  const emptySection = document.getElementById("my-badge-empty");
  const qrContainer = document.getElementById("my-badge-qr");
  const timerBar = document.getElementById("my-badge-timer-bar");
  const timerLabel = document.getElementById("my-badge-timer-label");
  const textCodeEl = document.getElementById("my-badge-code-text");
  const syncNoteEl = document.getElementById("my-badge-clock-note");

  function loadBadgeRecord() {
    try {
      const raw = localStorage.getItem(BADGE_STORAGE_KEY);
      if (!raw) {
        return null;
      }
      const parsed = JSON.parse(raw);
      if (!parsed || !parsed.public_id || !parsed.secret) {
        return null;
      }
      return parsed;
    } catch (_err) {
      return null;
    }
  }

  function saveBadgeRecord(record) {
    try {
      localStorage.setItem(BADGE_STORAGE_KEY, JSON.stringify(record));
    } catch (_err) {
      // Ignore storage quota errors cleanly.
    }
  }

  async function recalibrateClock(record) {
    if (!navigator.onLine) {
      if (syncNoteEl) {
        syncNoteEl.textContent = "Mode hors ligne : génération locale active.";
      }
      return;
    }
    try {
      const t0 = Date.now();
      const resp = await fetch("/api/time/", {
        method: "GET",
        cache: "no-store",
        headers: { "Accept": "application/json" }
      });
      if (!resp.ok) {
        return;
      }
      const t1 = Date.now();
      const data = await resp.json();
      const serverMs = Date.parse(data.server_time);
      if (Number.isFinite(serverMs)) {
        const midpoint = Math.round((t0 + t1) / 2);
        record.clock_offset_ms = serverMs - midpoint;
        if (data.code_step_seconds) {
          record.code_step_seconds = Number(data.code_step_seconds);
        }
        saveBadgeRecord(record);
        if (syncNoteEl) {
          syncNoteEl.textContent = "Horloge recalée sur le serveur.";
        }
      }
    } catch (_err) {
      if (syncNoteEl) {
        syncNoteEl.textContent = "Mode hors ligne : génération locale active.";
      }
    }
  }

  const record = loadBadgeRecord();
  if (!record) {
    if (emptySection) {
      emptySection.hidden = false;
    }
    if (activeSection) {
      activeSection.hidden = true;
    }
    return;
  }

  if (emptySection) {
    emptySection.hidden = true;
  }
  if (activeSection) {
    activeSection.hidden = false;
  }

  let lastRenderedStep = null;

  async function refreshBadgeDisplay() {
    const stepSeconds = Number(record.code_step_seconds) || 30;
    const offsetMs = Number(record.clock_offset_ms) || 0;
    const adjustedEpochSec = Math.floor((Date.now() + offsetMs) / 1000);
    const currentStep = window.PassageCrypto.computeTimeStep(adjustedEpochSec, stepSeconds);
    const elapsedInStep = ((adjustedEpochSec % stepSeconds) + stepSeconds) % stepSeconds;
    const secondsLeft = stepSeconds - elapsedInStep;

    if (timerBar) {
      const pct = Math.max(0, Math.min(100, (secondsLeft / stepSeconds) * 100));
      timerBar.style.width = pct.toFixed(1) + "%";
    }
    if (timerLabel) {
      timerLabel.textContent = "Valable encore " + secondsLeft + " s";
    }

    if (currentStep !== lastRenderedStep) {
      lastRenderedStep = currentStep;
      const code = await window.PassageCrypto.signBadgeCode(
        record.public_id,
        record.secret,
        currentStep
      );
      window.PassageCrypto.renderQrIntoElement(qrContainer, code);
      if (textCodeEl) {
        textCodeEl.textContent = code;
      }
    }
  }

  recalibrateClock(record).finally(function () {
    refreshBadgeDisplay();
  });

  window.setInterval(refreshBadgeDisplay, 500);
  window.addEventListener("online", function () {
    recalibrateClock(record).then(refreshBadgeDisplay);
  });
})();
