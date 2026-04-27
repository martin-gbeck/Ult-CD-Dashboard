const DATA_URL = "champion_ultimate_cooldowns.json";

const state = {
  champions: [],
  selected: new Set(),
  query: "",
  haste: 0,
  rank: "1",
};

const championList = document.querySelector("#championList");
const resultsGrid = document.querySelector("#resultsGrid");
const emptyState = document.querySelector("#emptyState");
const selectedCount = document.querySelector("#selectedCount");
const searchInput = document.querySelector("#searchInput");
const hasteInput = document.querySelector("#hasteInput");
const clearButton = document.querySelector("#clearButton");
const selectAllButton = document.querySelector("#selectAllButton");
const rankButtons = Array.from(document.querySelectorAll("[data-rank]"));

function parseCooldowns(value) {
  if (!value) return [];
  return value.match(/\d+(?:\.\d+)?/g)?.map(Number) ?? [];
}

function adjustedCooldown(base, haste) {
  return base * (100 / (100 + haste));
}

function formatSeconds(value) {
  if (!Number.isFinite(value)) return "-";
  const rounded = Math.round(value * 10) / 10;
  return Number.isInteger(rounded) ? `${rounded}s` : `${rounded.toFixed(1)}s`;
}

function normalizeChampion(raw) {
  const cooldowns = parseCooldowns(raw.ultimate_cooldown);
  return {
    ...raw,
    cooldowns,
    searchable: `${raw.name} ${raw.ultimate ?? ""}`.toLowerCase(),
  };
}

function visibleChampions() {
  const query = state.query.trim().toLowerCase();
  if (!query) return state.champions;
  return state.champions.filter((champion) => champion.searchable.includes(query));
}

function selectedChampions() {
  return state.champions.filter((champion) => state.selected.has(champion.name));
}

function getRanks(champion) {
  if (state.rank === "all") return champion.cooldowns.map((_, index) => index);
  const selectedIndex = Number(state.rank) - 1;
  if (!champion.cooldowns.length) return [];
  return [Math.min(selectedIndex, champion.cooldowns.length - 1)];
}

function renderChampionList() {
  const visible = visibleChampions();
  championList.replaceChildren(
    ...visible.map((champion) => {
      const row = document.createElement("label");
      row.className = `champion-row${state.selected.has(champion.name) ? " is-selected" : ""}`;

      const image = document.createElement("img");
      image.src = champion.img_path;
      image.alt = "";
      image.loading = "lazy";

      const text = document.createElement("div");
      const name = document.createElement("div");
      name.className = "champion-name";
      name.textContent = champion.name;
      const ult = document.createElement("div");
      ult.className = "champion-ult";
      ult.textContent = champion.ultimate ?? "Unknown";
      text.append(name, ult);

      const checkbox = document.createElement("input");
      checkbox.className = "checkbox";
      checkbox.type = "checkbox";
      checkbox.checked = state.selected.has(champion.name);
      checkbox.addEventListener("change", () => {
        if (checkbox.checked) {
          state.selected.add(champion.name);
        } else {
          state.selected.delete(champion.name);
        }
        render();
      });

      row.append(image, text, checkbox);
      return row;
    }),
  );
}

function createCooldownRow(champion, rankIndex) {
  const base = champion.cooldowns[rankIndex];
  const row = document.createElement("div");
  row.className = "cooldown-row";

  if (state.rank !== "all") {
    row.classList.add("is-active");
  }

  const label = document.createElement("div");
  label.className = "cooldown-label";
  label.textContent = champion.cooldowns.length === 1 ? "Cooldown" : `Rank ${rankIndex + 1}`;

  const baseValue = document.createElement("div");
  baseValue.className = "cooldown-value";
  baseValue.textContent = formatSeconds(base);

  const adjustedValue = document.createElement("div");
  adjustedValue.className = "cooldown-value adjusted";
  adjustedValue.textContent = formatSeconds(adjustedCooldown(base, state.haste));

  row.append(label, baseValue, adjustedValue);
  return row;
}

function createResultCard(champion) {
  const card = document.createElement("article");
  card.className = `result-card${champion.cooldowns.length ? "" : " has-warning"}`;

  const header = document.createElement("div");
  header.className = "card-header";

  const image = document.createElement("img");
  image.src = champion.img_path;
  image.alt = "";
  image.loading = "lazy";

  const title = document.createElement("div");
  title.className = "card-title";
  const name = document.createElement("h2");
  name.textContent = champion.name;
  const ultimate = document.createElement("p");
  ultimate.textContent = champion.ultimate ?? "Unknown ultimate";
  title.append(name, ultimate);
  header.append(image, title);

  const body = document.createElement("div");
  body.className = "cooldown-grid";

  const heading = document.createElement("div");
  heading.className = "cooldown-row";
  heading.innerHTML = `
    <span class="cooldown-label">Rank</span>
    <span class="cooldown-label">Base</span>
    <span class="cooldown-label">Adjusted</span>
  `;
  body.append(heading);

  const ranks = getRanks(champion);
  if (ranks.length) {
    body.append(...ranks.map((rankIndex) => createCooldownRow(champion, rankIndex)));
  } else {
    const warning = document.createElement("div");
    warning.className = "warning-text";
    warning.textContent = champion.error ?? "No direct cooldown";
    body.append(warning);
  }

  card.append(header, body);
  return card;
}

function renderResults() {
  const selected = selectedChampions();
  selectedCount.textContent = selected.length.toString();
  emptyState.hidden = selected.length > 0;
  resultsGrid.replaceChildren(...selected.map(createResultCard));
}

function render() {
  renderChampionList();
  renderResults();
}

function setRank(rank) {
  state.rank = rank;
  rankButtons.forEach((button) => {
    button.classList.toggle("is-active", button.dataset.rank === rank);
  });
  renderResults();
}

async function loadData() {
  const response = await fetch(DATA_URL);
  if (!response.ok) {
    throw new Error(`Unable to load ${DATA_URL}`);
  }
  const data = await response.json();
  state.champions = Object.values(data)
    .map(normalizeChampion)
    .sort((a, b) => a.name.localeCompare(b.name));
  render();
}

searchInput.addEventListener("input", (event) => {
  state.query = event.target.value;
  renderChampionList();
});

hasteInput.addEventListener("input", (event) => {
  state.haste = Math.max(0, Number(event.target.value) || 0);
  renderResults();
});

clearButton.addEventListener("click", () => {
  state.selected.clear();
  render();
});

selectAllButton.addEventListener("click", () => {
  visibleChampions().forEach((champion) => state.selected.add(champion.name));
  render();
});

rankButtons.forEach((button) => {
  button.addEventListener("click", () => setRank(button.dataset.rank));
});

loadData().catch((error) => {
  emptyState.hidden = false;
  emptyState.querySelector("h2").textContent = error.message;
});
