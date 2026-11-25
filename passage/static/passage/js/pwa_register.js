/* Enregistrement silencieux du Service Worker unique (PWA hors ligne). */
(function () {
  "use strict";

  if ("serviceWorker" in navigator) {
    window.addEventListener("load", function () {
      navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(function () {
        // Sur HTTP réseau local (hors localhost/HTTPS), le Service Worker est ignoré sans bloquer l'interface.
      });
    });
  }
})();
