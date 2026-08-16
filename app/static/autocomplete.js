// Shared ticker-search autocomplete used by fair_value.js and app.js.
window.attachTickerSearch = function attachTickerSearch(inputEl, resultsEl, tickersSource, onSelect) {
  let selectedIdx = -1;
  let currentMatches = [];

  function tickers() {
    return typeof tickersSource === "function" ? (tickersSource() || []) : (tickersSource || []);
  }

  function render() {
    if (!currentMatches.length) {
      resultsEl.innerHTML = "";
      resultsEl.classList.add("hidden");
      return;
    }
    resultsEl.classList.remove("hidden");
    resultsEl.innerHTML = currentMatches.map((m, i) =>
      `<li data-ticker="${m.ticker}" class="${i === selectedIdx ? "selected" : ""}">
         <span class="ticker">${m.ticker}</span>
         <span class="sector">${m.sector || ""}</span>
       </li>`
    ).join("");
  }

  function filter(query) {
    const q = query.trim().toUpperCase();
    if (!q) return [];
    const all = tickers();
    const prefix = all.filter(t => t.ticker.toUpperCase().startsWith(q));
    const contains = all.filter(t => !t.ticker.toUpperCase().startsWith(q) && t.ticker.toUpperCase().includes(q));
    return prefix.concat(contains).slice(0, 10);
  }

  function commit(ticker) {
    inputEl.value = "";
    currentMatches = [];
    selectedIdx = -1;
    render();
    onSelect(ticker);
  }

  inputEl.addEventListener("input", () => {
    currentMatches = filter(inputEl.value);
    selectedIdx = currentMatches.length > 0 ? 0 : -1;
    render();
  });

  inputEl.addEventListener("focus", () => {
    if (inputEl.value.trim()) {
      currentMatches = filter(inputEl.value);
      selectedIdx = currentMatches.length > 0 ? 0 : -1;
      render();
    }
  });

  inputEl.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      if (currentMatches.length) {
        selectedIdx = Math.min(selectedIdx + 1, currentMatches.length - 1);
        render();
      }
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      if (currentMatches.length) {
        selectedIdx = Math.max(selectedIdx - 1, 0);
        render();
      }
    } else if (e.key === "Enter") {
      if (selectedIdx >= 0 && currentMatches[selectedIdx]) {
        e.preventDefault();
        commit(currentMatches[selectedIdx].ticker);
      }
    } else if (e.key === "Escape") {
      inputEl.value = "";
      currentMatches = [];
      selectedIdx = -1;
      render();
      inputEl.blur();
    }
  });

  resultsEl.addEventListener("click", (e) => {
    const li = e.target.closest("li[data-ticker]");
    if (!li) return;
    commit(li.dataset.ticker);
  });

  document.addEventListener("click", (e) => {
    if (!inputEl.contains(e.target) && !resultsEl.contains(e.target)) {
      currentMatches = [];
      selectedIdx = -1;
      render();
    }
  });
};
