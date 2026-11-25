/* Consommation unique du lien d'activation et enregistrement local du secret sur le support. */
(function () {
  "use strict";

  const BADGE_STORAGE_KEY = "passage_my_badge_v1";
  const form = document.getElementById("activate-badge-form");
  const statusBox = document.getElementById("activate-status");
  const submitBtn = document.getElementById("activate-badge-btn");

  if (!form) {
    return;
  }

  function getCsrfToken() {
    const input = form.querySelector('input[name="csrfmiddlewaretoken"]');
    return input ? input.value : "";
  }

  function showError(message) {
    if (!statusBox) {
      return;
    }
    statusBox.className = "alert alert-error";
    statusBox.setAttribute("role", "alert");
    statusBox.textContent = message;
    statusBox.hidden = false;
  }

  form.addEventListener("submit", async function (event) {
    event.preventDefault();
    if (submitBtn) {
      submitBtn.disabled = true;
      submitBtn.textContent = "Activation en cours...";
    }

    try {
      const response = await fetch(window.location.pathname, {
        method: "POST",
        credentials: "same-origin",
        headers: {
          "Accept": "application/json",
          "X-CSRFToken": getCsrfToken(),
          "X-Requested-With": "XMLHttpRequest"
        }
      });

      const data = await response.json().catch(function () {
        return {};
      });

      if (!response.ok) {
        showError(data.detail || "Ce lien d'activation n'est plus valable.");
        if (submitBtn) {
          submitBtn.disabled = false;
          submitBtn.textContent = "Activer mon badge";
        }
        return;
      }

      const serverMs = Date.parse(data.server_time);
      const clockOffsetMs = Number.isFinite(serverMs) ? serverMs - Date.now() : 0;

      const record = {
        public_id: data.public_id,
        secret: data.secret,
        code_step_seconds: Number(data.code_step_seconds) || 30,
        clock_offset_ms: clockOffsetMs,
        activated_at: new Date().toISOString()
      };

      localStorage.setItem(BADGE_STORAGE_KEY, JSON.stringify(record));
      window.location.replace("/mon-badge/");
    } catch (_err) {
      showError("Impossible de contacter le serveur. Vérifiez votre connexion puis réessayez.");
      if (submitBtn) {
        submitBtn.disabled = false;
        submitBtn.textContent = "Activer mon badge";
      }
    }
  });
})();
