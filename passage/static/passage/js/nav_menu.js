/* Menu burger responsive pour la navigation Responsable sur téléphone (< 720 px). */
(function () {
  "use strict";

  const toggle = document.getElementById("manager-nav-toggle");
  const nav = document.getElementById("manager-nav-links");
  const label = document.getElementById("manager-nav-toggle-label");

  if (!toggle || !nav) {
    return;
  }

  function setOpen(isOpen) {
    toggle.setAttribute("aria-expanded", isOpen ? "true" : "false");
    nav.classList.toggle("is-open", isOpen);
    if (label) {
      label.textContent = isOpen ? "Fermer" : "Menu";
    }
  }

  toggle.addEventListener("click", function () {
    const currentlyOpen = toggle.getAttribute("aria-expanded") === "true";
    setOpen(!currentlyOpen);
  });

  nav.querySelectorAll("a").forEach(function (link) {
    link.addEventListener("click", function () {
      setOpen(false);
    });
  });

  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape" && toggle.getAttribute("aria-expanded") === "true") {
      setOpen(false);
      toggle.focus();
    }
  });
})();
