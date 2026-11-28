/* Écran de scan Agent : caméra QR (jsQR), saisie manuelle, file persistante hors ligne et synchro idempotente CSRF. */
(function () {
  "use strict";

  const DEVICE_ID_KEY = "passage_scanner_device_id_v1";
  const QUEUE_KEY = "passage_scanner_queue_v1";
  const CHECKPOINTS_KEY = "passage_scanner_checkpoints_v1";
  const SELECTED_CP_KEY = "passage_scanner_selected_checkpoint_v1";
  const SCAN_DEBOUNCE_MS = 2500;

  const netbar = document.getElementById("scanner-netbar");
  const netBadge = document.getElementById("scanner-net-badge");
  const pendingLabel = document.getElementById("scanner-pending-count");
  const syncBtn = document.getElementById("scanner-sync-btn");
  const checkpointSelect = document.getElementById("checkpoint-select");
  const cameraVideo = document.getElementById("scanner-video");
  const cameraCanvas = document.getElementById("scanner-canvas");
  const cameraStatus = document.getElementById("scanner-camera-status");
  const cameraToggleBtn = document.getElementById("scanner-camera-toggle");
  const feedbackBox = document.getElementById("scanner-feedback");
  const sessionAlertBox = document.getElementById("scanner-session-alert");
  const manualForm = document.getElementById("scanner-manual-form");
  const manualInput = document.getElementById("scanner-manual-code");

  if (!checkpointSelect || !syncBtn) {
    return;
  }

  let cameraStream = null;
  let scanTimerId = null;
  let isSyncing = false;
  let lastDecodedCode = "";
  let lastDecodedAt = 0;

  function generateUuid() {
    if (window.crypto && typeof window.crypto.randomUUID === "function") {
      return window.crypto.randomUUID();
    }
    const bytes = new Uint8Array(16);
    if (window.crypto && window.crypto.getRandomValues) {
      window.crypto.getRandomValues(bytes);
    } else {
      for (let i = 0; i < 16; i += 1) {
        bytes[i] = Math.floor(Math.random() * 256);
      }
    }
    let hex = "";
    for (let i = 0; i < bytes.length; i += 1) {
      hex += bytes[i].toString(16).padStart(2, "0");
    }
    return hex.slice(0, 8) + "-" + hex.slice(8, 12) + "-" + hex.slice(12, 16) + "-" + hex.slice(16, 20) + "-" + hex.slice(20);
  }

  function getOrCreateDeviceId() {
    let deviceId = localStorage.getItem(DEVICE_ID_KEY);
    if (!deviceId) {
      deviceId = "scanner-" + generateUuid().slice(0, 16);
      localStorage.setItem(DEVICE_ID_KEY, deviceId);
    }
    return deviceId;
  }

  function getCsrfToken() {
    const match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
    return match ? decodeURIComponent(match[1]) : "";
  }

  function loadQueue() {
    try {
      const raw = localStorage.getItem(QUEUE_KEY);
      const parsed = raw ? JSON.parse(raw) : [];
      return Array.isArray(parsed) ? parsed : [];
    } catch (_err) {
      return [];
    }
  }

  function saveQueue(queue) {
    localStorage.setItem(QUEUE_KEY, JSON.stringify(queue));
  }

  function showNeutralFeedback(message) {
    if (!feedbackBox) {
      return;
    }
    feedbackBox.className = "scan-result";
    feedbackBox.textContent = message;
    feedbackBox.hidden = false;
  }

  function showRejectedFeedback(message) {
    if (!feedbackBox) {
      return;
    }
    feedbackBox.className = "scan-result scan-result-err";
    feedbackBox.textContent = message;
    feedbackBox.hidden = false;
  }

  function showSessionAlert(htmlMessage) {
    if (!sessionAlertBox) {
      return;
    }
    sessionAlertBox.innerHTML = htmlMessage;
    sessionAlertBox.hidden = false;
  }

  function hideSessionAlert() {
    if (sessionAlertBox) {
      sessionAlertBox.hidden = true;
    }
  }

  function updateStatusBar() {
    const online = navigator.onLine;
    const queue = loadQueue();
    const count = queue.length;

    if (netbar) {
      netbar.setAttribute("data-net", online ? "online" : "offline");
    }
    if (netBadge) {
      netBadge.className = online ? "badge b-ok" : "badge b-warn";
      netBadge.textContent = online ? "En ligne" : "Hors ligne";
    }
    if (pendingLabel) {
      pendingLabel.textContent =
        count + (count > 1 ? " scans en attente" : " scan en attente");
    }
    syncBtn.disabled = isSyncing || count === 0;
    syncBtn.textContent = isSyncing ? "Synchronisation..." : "Synchroniser";
  }

  function saveCheckpointsToLocal(checkpoints) {
    try {
      localStorage.setItem(CHECKPOINTS_KEY, JSON.stringify(checkpoints));
    } catch (_err) {
      // Ignore quota issues.
    }
  }

  function renderCheckpoints(checkpoints) {
    if (!Array.isArray(checkpoints) || checkpoints.length === 0) {
      return;
    }
    const previous = localStorage.getItem(SELECTED_CP_KEY) || checkpointSelect.value;
    checkpointSelect.innerHTML = "";
    checkpoints.forEach(function (cp) {
      const option = document.createElement("option");
      option.value = String(cp.id);
      option.textContent = cp.label;
      checkpointSelect.appendChild(option);
    });
    if (previous && checkpointSelect.querySelector('option[value="' + previous + '"]')) {
      checkpointSelect.value = previous;
    }
    if (checkpointSelect.value) {
      localStorage.setItem(SELECTED_CP_KEY, checkpointSelect.value);
    }
  }

  function initCheckpoints() {
    // Sauvegarde d'abord la liste rendue par le serveur (ou recharge depuis localStorage hors ligne).
    const domCheckpoints = Array.from(checkpointSelect.options).map(function (opt) {
      return { id: Number(opt.value), label: opt.textContent };
    });
    if (domCheckpoints.length > 0) {
      saveCheckpointsToLocal(domCheckpoints);
      const savedChoice = localStorage.getItem(SELECTED_CP_KEY);
      if (savedChoice && checkpointSelect.querySelector('option[value="' + savedChoice + '"]')) {
        checkpointSelect.value = savedChoice;
      }
    } else {
      try {
        const cached = JSON.parse(localStorage.getItem(CHECKPOINTS_KEY) || "[]");
        renderCheckpoints(cached);
      } catch (_err) {
        // Ignore parse error.
      }
    }

    checkpointSelect.addEventListener("change", function () {
      localStorage.setItem(SELECTED_CP_KEY, checkpointSelect.value);
    });

    if (navigator.onLine) {
      fetch("/api/checkpoints/", {
        method: "GET",
        credentials: "same-origin",
        headers: { "Accept": "application/json" }
      })
        .then(function (resp) {
          return resp.ok ? resp.json() : null;
        })
        .then(function (data) {
          if (data && Array.isArray(data.checkpoints)) {
            saveCheckpointsToLocal(data.checkpoints);
            renderCheckpoints(data.checkpoints);
          }
        })
        .catch(function () {
          // Mode hors ligne : on conserve la liste locale.
        });
    }
  }

  function enqueueScan(rawCode) {
    const cleaned = (rawCode || "").trim();
    if (!cleaned) {
      return;
    }
    const checkpointId = Number(checkpointSelect.value);
    if (!checkpointId) {
      showNeutralFeedback("Choisissez un point de contrôle avant de scanner.");
      return;
    }

    const queue = loadQueue();
    queue.push({
      client_event_id: generateUuid(),
      checkpoint_id: checkpointId,
      code: cleaned,
      local_scanned_at: new Date().toISOString()
    });
    saveQueue(queue);

    // SCN-05 : message neutre uniquement, jamais « badge valide » avant réponse serveur.
    showNeutralFeedback("Scan enregistré");
    updateStatusBar();

    if (navigator.onLine && !isSyncing) {
      syncPendingScans();
    }
  }

  async function syncPendingScans() {
    if (isSyncing) {
      return;
    }
    const snapshot = loadQueue();
    if (snapshot.length === 0) {
      updateStatusBar();
      return;
    }
    if (!navigator.onLine) {
      updateStatusBar();
      return;
    }

    isSyncing = true;
    updateStatusBar();

    try {
      const response = await fetch("/api/sync/", {
        method: "POST",
        credentials: "same-origin",
        headers: {
          "Content-Type": "application/json",
          "Accept": "application/json",
          "X-CSRFToken": getCsrfToken()
        },
        body: JSON.stringify({
          device_id: getOrCreateDeviceId(),
          device_time: new Date().toISOString(),
          events: snapshot
        })
      });

      if (response.status === 401) {
        // SCN-09 : la file locale reste intacte après expiration de session.
        showSessionAlert(
          "Session expirée : vos " +
            snapshot.length +
            ' scan(s) restent conservés localement. <a href="/connexion/">Se reconnecter</a> puis synchroniser.'
        );
        return;
      }

      if (!response.ok) {
        return;
      }

      const data = await response.json();
      if (!data || !Array.isArray(data.results)) {
        return;
      }

      hideSessionAlert();

      // SCN-07 : ne retirer de la file que les scans explicitement accusés par le serveur.
      const acknowledgedIds = new Set();
      let rejectedCount = 0;
      let acceptedCount = 0;
      data.results.forEach(function (item) {
        if (
          item &&
          item.client_event_id &&
          (item.status === "accepted" || item.status === "duplicate" || item.status === "rejected")
        ) {
          acknowledgedIds.add(item.client_event_id);
          if (item.status === "rejected") {
            rejectedCount += 1;
          } else {
            acceptedCount += 1;
          }
        }
      });

      const currentQueue = loadQueue();
      const remaining = currentQueue.filter(function (evt) {
        return !acknowledgedIds.has(evt.client_event_id);
      });
      saveQueue(remaining);

      if (rejectedCount > 0 && acceptedCount === 0) {
        showRejectedFeedback(
          "Badge refusé par le serveur (" +
            rejectedCount +
            (rejectedCount > 1 ? " rejets" : " rejet") +
            ")."
        );
      } else if (rejectedCount > 0) {
        showRejectedFeedback(
          "Synchronisation terminée : " +
            acceptedCount +
            " transmis, " +
            rejectedCount +
            (rejectedCount > 1 ? " refusés." : " refusé.")
        );
      } else if (acknowledgedIds.size > 0) {
        showNeutralFeedback(
          "Synchronisation terminée (" + acknowledgedIds.size + " scan(s) transmis)."
        );
      }
    } catch (_err) {
      // Réseau interrompu en cours de route : toute la file reste intacte pour la reprise.
    } finally {
      isSyncing = false;
      updateStatusBar();
    }
  }

  function stopCamera() {
    if (scanTimerId !== null) {
      window.clearInterval(scanTimerId);
      scanTimerId = null;
    }
    if (cameraStream) {
      cameraStream.getTracks().forEach(function (track) {
        track.stop();
      });
      cameraStream = null;
    }
    if (cameraVideo) {
      cameraVideo.srcObject = null;
      cameraVideo.hidden = true;
    }
    if (cameraStatus) {
      cameraStatus.textContent = "Caméra arrêtée. Appuyez sur Activer la caméra ou utilisez la saisie manuelle.";
      cameraStatus.hidden = false;
    }
    if (cameraToggleBtn) {
      cameraToggleBtn.textContent = "Activer la caméra";
    }
  }

  async function startCamera() {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      if (cameraStatus) {
        cameraStatus.textContent =
          "Caméra indisponible dans ce navigateur (contexte localhost/HTTPS requis). Utilisez la saisie manuelle ci-dessous.";
        cameraStatus.hidden = false;
      }
      return;
    }

    try {
      cameraStream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: { ideal: "environment" } },
        audio: false
      });
      cameraVideo.srcObject = cameraStream;
      cameraVideo.setAttribute("playsinline", "true");
      await cameraVideo.play();
      cameraVideo.hidden = false;
      if (cameraStatus) {
        cameraStatus.hidden = true;
      }
      if (cameraToggleBtn) {
        cameraToggleBtn.textContent = "Arrêter la caméra";
      }

      const ctx = cameraCanvas.getContext("2d", { willReadFrequently: true });
      scanTimerId = window.setInterval(function () {
        if (!cameraVideo || cameraVideo.readyState !== cameraVideo.HAVE_ENOUGH_DATA) {
          return;
        }
        if (typeof window.jsQR !== "function") {
          return;
        }
        cameraCanvas.width = cameraVideo.videoWidth;
        cameraCanvas.height = cameraVideo.videoHeight;
        ctx.drawImage(cameraVideo, 0, 0, cameraCanvas.width, cameraCanvas.height);
        const imageData = ctx.getImageData(0, 0, cameraCanvas.width, cameraCanvas.height);
        const decoded = window.jsQR(imageData.data, imageData.width, imageData.height, {
          inversionAttempts: "dontInvert"
        });
        if (decoded && decoded.data) {
          const nowMs = Date.now();
          if (decoded.data !== lastDecodedCode || nowMs - lastDecodedAt > SCAN_DEBOUNCE_MS) {
            lastDecodedCode = decoded.data;
            lastDecodedAt = nowMs;
            enqueueScan(decoded.data);
          }
        }
      }, 180);
    } catch (_err) {
      stopCamera();
      if (cameraStatus) {
        cameraStatus.textContent =
          "Accès caméra refusé ou indisponible. Utilisez la saisie manuelle ci-dessous.";
        cameraStatus.hidden = false;
      }
    }
  }

  if (cameraToggleBtn) {
    cameraToggleBtn.addEventListener("click", function () {
      if (cameraStream) {
        stopCamera();
      } else {
        startCamera();
      }
    });
  }

  if (manualForm && manualInput) {
    manualForm.addEventListener("submit", function (event) {
      event.preventDefault();
      enqueueScan(manualInput.value);
      manualInput.value = "";
      manualInput.focus();
    });
  }

  syncBtn.addEventListener("click", function () {
    syncPendingScans();
  });

  window.addEventListener("online", function () {
    updateStatusBar();
    syncPendingScans();
  });
  window.addEventListener("offline", updateStatusBar);
  window.addEventListener("beforeunload", stopCamera);

  getOrCreateDeviceId();
  initCheckpoints();
  updateStatusBar();
})();
