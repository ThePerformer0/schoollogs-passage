/* Contrôles du champ mot de passe : afficher/masquer et saisie manuelle uniquement. */
(function () {
  "use strict";

  const passwordInput = document.getElementById("id_password");
  const toggleButton = document.getElementById("toggle-password");

  if (!passwordInput) {
    return;
  }

  // Empêche copier, couper, coller et glisser-déposer dans le champ mot de passe.
  ["paste", "copy", "cut", "drop"].forEach(function (eventName) {
    passwordInput.addEventListener(eventName, function (event) {
      event.preventDefault();
    });
  });

  if (!toggleButton) {
    return;
  }

  const iconShow = toggleButton.querySelector('[data-icon="show"]');
  const iconHide = toggleButton.querySelector('[data-icon="hide"]');

  toggleButton.addEventListener("click", function () {
    const revealing = passwordInput.type === "password";
    passwordInput.type = revealing ? "text" : "password";
    toggleButton.setAttribute("aria-pressed", revealing ? "true" : "false");
    toggleButton.setAttribute(
      "aria-label",
      revealing ? "Masquer le mot de passe" : "Afficher le mot de passe"
    );

    if (iconShow && iconHide) {
      iconShow.classList.toggle("icon-hidden", revealing);
      iconHide.classList.toggle("icon-hidden", !revealing);
    }
  });
})();
