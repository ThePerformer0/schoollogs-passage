/* Affiche le lien d'activation unique sous forme de QR code local sur l'écran Responsable. */
(function () {
  "use strict";

  const qrBox = document.getElementById("activation-link-qr");
  if (!qrBox || !window.PassageCrypto) {
    return;
  }
  const url = qrBox.getAttribute("data-activation-url");
  if (url) {
    window.PassageCrypto.renderQrIntoElement(qrBox, url);
  }
})();
