/* Enregistrement du Service Worker unique et gestion de l'invite d'installation PWA. */
(function () {
  "use strict";

  if ("serviceWorker" in navigator) {
    window.addEventListener("load", function () {
      navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(function () {
        // Sur HTTP réseau local (hors localhost/HTTPS), le Service Worker est ignoré sans bloquer l'interface.
      });
    });
  }

  let deferredInstallPrompt = null;
  const installBtn = document.getElementById("pwa-install-btn");

  window.addEventListener("beforeinstallprompt", function (event) {
    event.preventDefault();
    deferredInstallPrompt = event;
    if (installBtn) {
      installBtn.hidden = false;
    }
  });

  if (installBtn) {
    installBtn.addEventListener("click", async function () {
      if (!deferredInstallPrompt) {
        return;
      }
      installBtn.disabled = true;
      try {
        deferredInstallPrompt.prompt();
        const choice = await deferredInstallPrompt.userChoice;
        if (choice && choice.outcome === "accepted") {
          installBtn.hidden = true;
        }
      } catch (_err) {
        // Ignore si l'invite a été annulée.
      } finally {
        deferredInstallPrompt = null;
        installBtn.disabled = false;
      }
    });
  }

  window.addEventListener("appinstalled", function () {
    deferredInstallPrompt = null;
    if (installBtn) {
      installBtn.hidden = true;
    }
  });
})();
