/* Filtres instantanés et pagination légère pour les tableaux (zéro dépendance). */
(function () {
  "use strict";

  function normalizeText(value) {
    return String(value || "")
      .toLowerCase()
      .normalize("NFD")
      .replace(/[\u0300-\u036f]/g, "")
      .trim();
  }

  function enhanceTable(table) {
    const tbody = table.querySelector("tbody");
    if (!tbody) {
      return;
    }
    const rows = Array.from(tbody.querySelectorAll("tr"));
    if (rows.length === 0) {
      return;
    }

    const wrap = table.closest(".table-wrap");
    if (!wrap || !wrap.parentNode) {
      return;
    }

    const pageSize = Math.max(1, parseInt(table.getAttribute("data-page-size") || "10", 10));
    const searchPlaceholder = table.getAttribute("data-search") || "";
    const filterSpec = table.getAttribute("data-filters") || "";
    const dateFilterKey = table.getAttribute("data-date-filter") || "";

    const hasFilters = Boolean(searchPlaceholder || filterSpec || dateFilterKey);
    const hasPaging = rows.length > pageSize;

    if (!hasFilters && !hasPaging) {
      return;
    }

    // Index normalized text and filter tokens once per row for instant filtering.
    const rowIndex = rows.map(function (row) {
      const attrs = {};
      Array.from(row.attributes).forEach(function (attr) {
        if (attr.name.startsWith("data-f-")) {
          const key = attr.name.slice(7);
          attrs[key] = attr.value
            .split("|")
            .map(function (part) { return part.trim(); })
            .filter(Boolean);
        }
      });
      return {
        row: row,
        text: normalizeText(row.textContent),
        attrs: attrs
      };
    });

    let currentPage = 1;
    let showAll = false;
    let searchInput = null;
    let dateInput = null;
    let resetBtn = null;
    const selectFilters = [];

    // 1. Optional filter toolbar above .table-wrap (only if >= 2 rows).
    if (hasFilters && rows.length >= 2) {
      const toolbar = document.createElement("div");
      toolbar.className = "table-filters";

      if (searchPlaceholder) {
        searchInput = document.createElement("input");
        searchInput.type = "search";
        searchInput.className = "filter-input";
        searchInput.placeholder = searchPlaceholder;
        searchInput.setAttribute("aria-label", searchPlaceholder);
        toolbar.appendChild(searchInput);
      }

      if (filterSpec) {
        filterSpec.split("|").forEach(function (item) {
          const parts = item.split(":");
          const key = (parts[0] || "").trim();
          const allLabel = (parts[1] || "Tous").trim();
          if (!key) {
            return;
          }
          const uniqueValues = new Set();
          rowIndex.forEach(function (entry) {
            (entry.attrs[key] || []).forEach(function (val) {
              uniqueValues.add(val);
            });
          });
          if (uniqueValues.size === 0) {
            return;
          }

          const select = document.createElement("select");
          select.className = "filter-select";
          select.setAttribute("aria-label", allLabel);

          const defaultOpt = document.createElement("option");
          defaultOpt.value = "";
          defaultOpt.textContent = allLabel;
          select.appendChild(defaultOpt);

          Array.from(uniqueValues)
            .sort(function (a, b) { return a.localeCompare(b, "fr"); })
            .forEach(function (val) {
              const opt = document.createElement("option");
              opt.value = val;
              opt.textContent = val;
              select.appendChild(opt);
            });

          toolbar.appendChild(select);
          selectFilters.push({ key: key, el: select });
        });
      }

      if (dateFilterKey) {
        dateInput = document.createElement("input");
        dateInput.type = "date";
        dateInput.className = "filter-date";
        dateInput.setAttribute("aria-label", "Filtrer par date");
        toolbar.appendChild(dateInput);
      }

      resetBtn = document.createElement("button");
      resetBtn.type = "button";
      resetBtn.className = "btn-pager btn-pager-ghost";
      resetBtn.textContent = "Effacer";
      resetBtn.hidden = true;
      toolbar.appendChild(resetBtn);

      wrap.parentNode.insertBefore(toolbar, wrap);
    }

    // Empty-filter-result placeholder row.
    const colCount = table.querySelectorAll("thead th").length || 1;
    const emptyRow = document.createElement("tr");
    emptyRow.className = "table-empty-row";
    emptyRow.hidden = true;
    const emptyCell = document.createElement("td");
    emptyCell.colSpan = colCount;
    emptyCell.className = "empty-state";
    emptyCell.textContent = "Aucun résultat pour ces critères.";
    emptyRow.appendChild(emptyCell);
    tbody.appendChild(emptyRow);

    // 2. Pager footer bar below .table-wrap.
    const bar = document.createElement("div");
    bar.className = "table-pager";

    const info = document.createElement("span");
    info.className = "table-pager-info mono";

    const controls = document.createElement("div");
    controls.className = "table-pager-controls";

    const prevBtn = document.createElement("button");
    prevBtn.type = "button";
    prevBtn.className = "btn-pager";
    prevBtn.textContent = "Précédent";

    const pageBadge = document.createElement("span");
    pageBadge.className = "table-pager-page mono";

    const nextBtn = document.createElement("button");
    nextBtn.type = "button";
    nextBtn.className = "btn-pager";
    nextBtn.textContent = "Suivant";

    const allBtn = document.createElement("button");
    allBtn.type = "button";
    allBtn.className = "btn-pager btn-pager-ghost";
    allBtn.textContent = "Tout afficher";

    controls.appendChild(prevBtn);
    controls.appendChild(pageBadge);
    controls.appendChild(nextBtn);
    controls.appendChild(allBtn);
    bar.appendChild(info);
    bar.appendChild(controls);

    wrap.parentNode.insertBefore(bar, wrap.nextSibling);

    function isAnyFilterActive() {
      if (searchInput && searchInput.value.trim()) {
        return true;
      }
      if (dateInput && dateInput.value.trim()) {
        return true;
      }
      return selectFilters.some(function (f) { return Boolean(f.el.value); });
    }

    function getMatchingEntries() {
      const q = searchInput ? normalizeText(searchInput.value) : "";
      const dateVal = dateInput ? dateInput.value.trim() : "";

      return rowIndex.filter(function (entry) {
        if (q && entry.text.indexOf(q) === -1) {
          return false;
        }
        if (dateVal) {
          const rowDates = entry.attrs[dateFilterKey] || [];
          if (rowDates.indexOf(dateVal) === -1) {
            return false;
          }
        }
        for (let i = 0; i < selectFilters.length; i += 1) {
          const f = selectFilters[i];
          if (f.el.value) {
            const values = entry.attrs[f.key] || [];
            if (values.indexOf(f.el.value) === -1) {
              return false;
            }
          }
        }
        return true;
      });
    }

    function render() {
      const filtered = isAnyFilterActive();
      if (resetBtn) {
        resetBtn.hidden = !filtered;
      }

      const matched = getMatchingEntries();
      const matchedCount = matched.length;
      const totalPages = Math.max(1, Math.ceil(matchedCount / pageSize));
      if (currentPage > totalPages) {
        currentPage = totalPages;
      }

      // Hide all original rows first, then reveal only matched rows for current page.
      rows.forEach(function (row) {
        row.hidden = true;
      });

      if (matchedCount === 0) {
        emptyRow.hidden = false;
        bar.hidden = false;
        info.textContent = "0 résultat (filtré sur " + rows.length + ")";
        controls.hidden = true;
        return;
      }

      emptyRow.hidden = true;
      const needPagerControls = matchedCount > pageSize;
      bar.hidden = !needPagerControls && !filtered;
      controls.hidden = !needPagerControls;

      const suffix = filtered ? " (filtré sur " + rows.length + ")" : "";

      if (showAll || !needPagerControls) {
        matched.forEach(function (entry) {
          entry.row.hidden = false;
        });
        info.textContent = "1 à " + matchedCount + " sur " + matchedCount + suffix;
        if (needPagerControls) {
          pageBadge.hidden = true;
          prevBtn.hidden = true;
          nextBtn.hidden = true;
          allBtn.textContent = "Réduire";
        }
        return;
      }

      const start = (currentPage - 1) * pageSize;
      const end = Math.min(start + pageSize, matchedCount);
      for (let i = start; i < end; i += 1) {
        matched[i].row.hidden = false;
      }

      info.textContent = (start + 1) + " à " + end + " sur " + matchedCount + suffix;
      pageBadge.hidden = false;
      pageBadge.textContent = currentPage + " / " + totalPages;
      prevBtn.hidden = false;
      nextBtn.hidden = false;
      prevBtn.disabled = currentPage <= 1;
      nextBtn.disabled = currentPage >= totalPages;
      allBtn.textContent = "Tout afficher";
    }

    function onFilterChange() {
      currentPage = 1;
      showAll = false;
      render();
    }

    if (searchInput) {
      searchInput.addEventListener("input", onFilterChange);
    }
    if (dateInput) {
      dateInput.addEventListener("change", onFilterChange);
    }
    selectFilters.forEach(function (f) {
      f.el.addEventListener("change", onFilterChange);
    });
    if (resetBtn) {
      resetBtn.addEventListener("click", function () {
        if (searchInput) {
          searchInput.value = "";
        }
        if (dateInput) {
          dateInput.value = "";
        }
        selectFilters.forEach(function (f) {
          f.el.value = "";
        });
        onFilterChange();
      });
    }

    prevBtn.addEventListener("click", function () {
      if (currentPage > 1) {
        currentPage -= 1;
        render();
      }
    });

    nextBtn.addEventListener("click", function () {
      currentPage += 1;
      render();
    });

    allBtn.addEventListener("click", function () {
      showAll = !showAll;
      render();
    });

    render();
  }

  document.querySelectorAll("table[data-page-size], table[data-search], table[data-filters]").forEach(enhanceTable);
})();
