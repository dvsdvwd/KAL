const state = {
  mode: "perps",
  symbol: "PF_XBTUSD",
  interval: "15m",
  snapshot: null,
  selectedMarket: null,
  loading: false,
  settingsLoaded: false,
};

const $ = (id) => document.getElementById(id);
const money = (value) => new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" }).format(value ?? 0);
const probability = (value) => value == null ? "No quote" : `${Number(value).toFixed(0)}%`;

async function request(path, options) {
  const response = await fetch(path, options);
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || `Request failed (${response.status})`);
  return payload;
}

function loadGoogleIdentity() {
  return new Promise((resolve, reject) => {
    if (window.google?.accounts?.id) {
      resolve();
      return;
    }
    const script = document.createElement("script");
    script.src = "https://accounts.google.com/gsi/client";
    script.async = true;
    script.onload = resolve;
    script.onerror = () => reject(new Error("Could not load Google Identity Services"));
    document.head.append(script);
  });
}

function showSignedInUser(user) {
  $("authState").textContent = user.name || user.email || "SIGNED IN";
  const signOutButton = document.createElement("button");
  signOutButton.className = "google-setup";
  signOutButton.type = "button";
  signOutButton.textContent = "SIGN OUT";
  signOutButton.addEventListener("click", async () => {
    await request("/api/auth/logout", { method: "POST" });
    window.location.reload();
  });
  $("googleSignInArea").replaceChildren(signOutButton);
}

async function initializeGoogleSignIn() {
  try {
    const config = await request("/api/config");
    if (!config.googleClientId) return;
    await loadGoogleIdentity();
    const session = await request("/api/auth/me");
    if (session.authenticated) {
      showSignedInUser(session.user);
      return;
    }
    window.google.accounts.id.initialize({
      client_id: config.googleClientId,
      callback: async ({ credential }) => {
        try {
          const result = await request("/api/auth/google", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ credential }),
          });
          showSignedInUser(result.user);
          showToast("Signed in to the dashboard");
        } catch (error) {
          showToast(error.message);
        }
      },
    });
    window.google.accounts.id.renderButton($("googleSignInArea"), {
      theme: "outline",
      size: "small",
      text: "signin_with",
      shape: "rectangular",
    });
  } catch (error) {
    showToast(error.message);
  }
}

function showToast(message) {
  const toast = $("toast");
  toast.textContent = message;
  toast.classList.add("show");
  window.clearTimeout(showToast.timeout);
  showToast.timeout = window.setTimeout(() => toast.classList.remove("show"), 3200);
}

function renderChart(candles, levels = {}) {
  const canvas = $("priceChart");
  const context = canvas.getContext("2d");
  const bounds = canvas.getBoundingClientRect();
  const ratio = window.devicePixelRatio || 1;
  canvas.width = Math.max(1, Math.floor(bounds.width * ratio));
  canvas.height = Math.max(1, Math.floor(bounds.height * ratio));
  context.setTransform(ratio, 0, 0, ratio, 0, 0);
  const width = bounds.width;
  const height = bounds.height;
  context.clearRect(0, 0, width, height);

  if (!candles?.length) {
    $("chartEmpty").classList.remove("hidden");
    return;
  }
  $("chartEmpty").classList.add("hidden");
  const values = candles.map((candle) => ({
    open: Number(candle.open),
    high: Number(candle.high),
    low: Number(candle.low),
    close: Number(candle.close),
  }));
  const low = Math.min(...values.map((candle) => candle.low));
  const high = Math.max(...values.map((candle) => candle.high));
  const range = high - low || Math.max(Math.abs(high) * 0.0004, 0.01);
  const pad = { left: 8, right: 72, top: 14, bottom: 22 };
  const chartWidth = width - pad.left - pad.right;
  const chartHeight = height - pad.top - pad.bottom;
  const y = (value) => pad.top + (high - value) / range * chartHeight;

  context.font = "9px 'DM Mono', monospace";
  context.textAlign = "right";
  context.textBaseline = "middle";
  for (let index = 0; index < 4; index += 1) {
    const lineY = pad.top + chartHeight * index / 3;
    const value = high - range * index / 3;
    context.strokeStyle = "#e6ebe4";
    context.lineWidth = 1;
    context.beginPath();
    context.moveTo(pad.left, lineY);
    context.lineTo(width - pad.right + 4, lineY);
    context.stroke();
    context.fillStyle = "#7b8981";
    context.fillText(value.toFixed(2), width - 5, lineY);
  }

  const spacing = chartWidth / values.length;
  const bodyWidth = Math.max(1, Math.min(7, spacing * 0.66));
  values.forEach((candle, index) => {
    const x = pad.left + spacing * (index + 0.5);
    const color = candle.close >= candle.open ? "#16785b" : "#c1493e";
    const bodyTop = Math.min(y(candle.open), y(candle.close));
    const bodyHeight = Math.max(1, Math.abs(y(candle.open) - y(candle.close)));
    context.strokeStyle = color;
    context.fillStyle = color;
    context.lineWidth = 1;
    context.beginPath();
    context.moveTo(x, y(candle.high));
    context.lineTo(x, y(candle.low));
    context.stroke();
    context.fillRect(x - bodyWidth / 2, bodyTop, bodyWidth, bodyHeight);
  });

  context.save();
  context.setLineDash([4, 4]);
  [[levels.rangeHigh, "20H", "#d88a32"], [levels.rangeLow, "20L", "#d88a32"], [levels.vwap, "VWAP", "#3784a8"]]
    .forEach(([value, label, color]) => {
      if (value == null) return;
      const levelY = y(value);
      context.strokeStyle = color;
      context.fillStyle = color;
      context.lineWidth = 1;
      context.beginPath();
      context.moveTo(pad.left, levelY);
      context.lineTo(width - pad.right + 4, levelY);
      context.stroke();
      context.textAlign = "left";
      context.fillText(label, pad.left + 4, levelY - 7);
    });
  context.restore();

  const firstTime = new Date(candles[0].time).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  const lastTime = new Date(candles.at(-1).time).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  $("chartRange").textContent = `${firstTime} — ${lastTime} UTC`;
}

function renderKalshiChart(market) {
  const canvas = $("priceChart");
  const context = canvas.getContext("2d");
  const bounds = canvas.getBoundingClientRect();
  const ratio = window.devicePixelRatio || 1;
  canvas.width = Math.max(1, Math.floor(bounds.width * ratio));
  canvas.height = Math.max(1, Math.floor(bounds.height * ratio));
  context.setTransform(ratio, 0, 0, ratio, 0, 0);
  const width = bounds.width;
  const height = bounds.height;
  context.clearRect(0, 0, width, height);
  $("chartEmpty").classList.add("hidden");
  const bid = market?.yesBid;
  const ask = market?.yesAsk;
  const centerY = height / 2;
  context.fillStyle = "#74817b";
  context.font = "10px 'DM Mono', monospace";
  context.fillText("CURRENT YES QUOTE RANGE", 12, 24);
  context.fillStyle = "#e7ece5";
  context.fillRect(12, centerY - 5, width - 24, 10);
  if (bid != null && ask != null) {
    const left = 12 + (width - 24) * Math.max(0, Math.min(100, bid)) / 100;
    const right = 12 + (width - 24) * Math.max(0, Math.min(100, ask)) / 100;
    context.fillStyle = "#d88a32";
    context.fillRect(left, centerY - 5, Math.max(right - left, 3), 10);
  }
  context.fillStyle = "#74817b";
  context.textAlign = "left";
  context.fillText("0%", 12, centerY + 28);
  context.textAlign = "right";
  context.fillText("100%", width - 12, centerY + 28);
  $("chartRange").textContent = "Current quote only · no historical chart";
}

function renderMarkets(markets) {
  const body = $("marketsBody");
  body.replaceChildren();
  if (!markets?.length) {
    const row = body.insertRow();
    const cell = row.insertCell();
    cell.colSpan = 4;
    cell.className = "table-message";
    cell.textContent = "No open markets returned";
    return;
  }
  for (const market of markets) {
    const row = body.insertRow();
    row.className = market.ticker === state.selectedMarket ? "selected" : "";
    row.addEventListener("click", () => {
      state.selectedMarket = market.ticker;
      renderSelectedMarket();
      renderMarkets(markets);
    });
    const titleCell = row.insertCell();
    titleCell.textContent = market.title;
    const ticker = document.createElement("span");
    ticker.className = "ticker";
    ticker.textContent = market.ticker;
    titleCell.append(ticker);
    row.insertCell().textContent = probability(market.yesBid);
    row.insertCell().textContent = probability(market.yesAsk);
    row.insertCell().textContent = market.closeTime ? new Date(market.closeTime).toLocaleDateString() : "—";
  }
}

function renderSelectedMarket() {
  const markets = state.snapshot?.markets || [];
  const selected = markets.find((market) => market.ticker === state.selectedMarket) || markets[0];
  if (!selected) return;
  state.selectedMarket = selected.ticker;
  $("contractTitle").textContent = selected.title;
  $("contractBid").textContent = probability(selected.yesBid);
  $("contractAsk").textContent = probability(selected.yesAsk);
  const mean = selected.yesBid == null || selected.yesAsk == null ? 0 : (selected.yesBid + selected.yesAsk) / 2;
  $("probabilityFill").style.width = `${Math.max(0, Math.min(100, mean))}%`;
  if (state.mode === "events") {
    $("chartEyebrow").textContent = "KALSHI EVENT CONTRACT";
    $("currentPrice").textContent = mean ? `${mean.toFixed(0)}%` : "No quote";
    $("priceChange").textContent = "YES MIDPOINT";
    $("signalBadge").textContent = "READ ONLY";
    $("signalBadge").className = "signal-badge flat";
    renderKalshiChart(selected);
  }
}

function renderSnapshot(snapshot) {
  state.snapshot = snapshot;
  if (!state.selectedMarket || !snapshot.markets.some((market) => market.ticker === state.selectedMarket)) {
    state.selectedMarket = snapshot.markets[0]?.ticker || null;
  }
  const account = snapshot.account;
  if (!state.settingsLoaded) {
    $("paperBalance").value = account.startingBalance;
    $("paperLeverage").value = String(account.leverage);
    $("autoSize").checked = account.autoSize;
    $("orderNotional").value = account.orderNotional;
    state.settingsLoaded = true;
  }
  $("paperBalance").disabled = snapshot.bot.running;
  $("paperLeverage").disabled = snapshot.bot.running;
  $("autoSize").disabled = snapshot.bot.running;
  $("orderNotional").disabled = snapshot.bot.running || $("autoSize").checked;
  $("saveSettings").disabled = snapshot.bot.running;
  $("equityValue").textContent = money(account.equity);
  $("pnlValue").textContent = money(account.dailyPnl);
  $("pnlValue").style.color = account.dailyPnl > 0 ? "#78c7a6" : account.dailyPnl < 0 ? "#eb867d" : "white";
  const position = account.position;
  $("positionValue").textContent = position ? `${position.side.toUpperCase()} ${position.quantity.toFixed(5)} · ${position.leverage}×` : "None";
  $("botState").textContent = snapshot.bot.running ? "RUNNING · PAPER" : "PAUSED";
  $("botDot").parentElement.parentElement.classList.toggle("running", snapshot.bot.running);
  $("botButton").textContent = snapshot.bot.running ? "STOP PAPER BOT" : "START PAPER BOT";
  $("botButton").classList.toggle("stop", snapshot.bot.running);
  $("symbolSelect").disabled = snapshot.bot.running;
  $("intervalSelect").disabled = snapshot.bot.running;
  $("botCaption").textContent = snapshot.bot.running
    ? `${snapshot.bot.action}. Paper state resets when this server stops.`
    : "Paper simulation only. No live orders are sent.";

  $("currentPrice").textContent = money(snapshot.price);
  $("signalBadge").textContent = snapshot.signal;
  $("signalBadge").className = `signal-badge ${snapshot.signal.toLowerCase()}`;
  $("emaRead").textContent = snapshot.signal;
  $("emaRead").className = snapshot.signal === "UP" ? "positive" : snapshot.signal === "DOWN" ? "negative" : "";
  const rsi = snapshot.indicators.rsi14;
  $("rsiRead").textContent = rsi == null ? "--" : rsi.toFixed(1);
  const macd = snapshot.indicators.macd;
  $("macdRead").textContent = macd == null ? "--" : macd.histogram.toFixed(2);
  $("macdRead").className = macd?.histogram > 0 ? "positive" : macd?.histogram < 0 ? "negative" : "";
  $("vwapRead").textContent = snapshot.indicators.vwap == null ? "--" : money(snapshot.indicators.vwap);
  $("rangeHighRead").textContent = money(snapshot.indicators.rangeHigh);
  $("rangeLowRead").textContent = money(snapshot.indicators.rangeLow);
  const priceAction = snapshot.indicators.priceAction;
  $("priceActionRead").textContent = priceAction.state.replace(" + ", " / ").replace(" EXPANSION", "");
  $("priceActionRead").className = priceAction.state.includes("EXPANSION") || priceAction.state === "VOLUME SPIKE" ? "alert" : "";
  const rangeRatio = priceAction.rangeMultiple == null ? "--" : `${priceAction.rangeMultiple.toFixed(1)}× range`;
  const volumeRatio = priceAction.volumeMultiple == null ? "--" : `${priceAction.volumeMultiple.toFixed(1)}× volume`;
  $("priceActionDetail").textContent = `${priceAction.direction} · ${rangeRatio} · ${volumeRatio}`;
  $("updatedAt").textContent = `Updated ${new Date().toLocaleTimeString()}`;
  renderMarkets(snapshot.markets);
  renderSelectedMarket();
  renderChart(snapshot.candles, snapshot.indicators);
  if (state.mode === "events") renderSelectedMarket();
}

async function refresh() {
  if (state.loading) return;
  state.loading = true;
  $("refreshButton").classList.add("spinning");
  try {
    const snapshot = await request(`/api/snapshot?symbol=${encodeURIComponent(state.symbol)}&interval=${encodeURIComponent(state.interval)}`);
    renderSnapshot(snapshot);
  } catch (error) {
    $("updatedAt").textContent = "Feed unavailable";
    $("chartEmpty").textContent = error.message;
    $("chartEmpty").classList.remove("hidden");
    showToast(error.message);
  } finally {
    state.loading = false;
    $("refreshButton").classList.remove("spinning");
  }
}

async function toggleBot() {
  const action = state.snapshot?.bot.running ? "stop" : "start";
  try {
    await request("/api/bot", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action, symbol: state.symbol, interval: state.interval }),
    });
    await refresh();
    showToast(action === "start" ? "Paper bot started" : "Paper bot paused");
  } catch (error) {
    showToast(error.message);
  }
}

async function applySettings() {
  try {
    await request("/api/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        startingBalance: Number($("paperBalance").value),
        leverage: Number($("paperLeverage").value),
        autoSize: $("autoSize").checked,
        orderNotional: Number($("orderNotional").value),
      }),
    });
    await refresh();
    showToast("Paper settings applied; paper history reset");
  } catch (error) {
    showToast(error.message);
  }
}

function updateManualSizing() {
  $("orderNotional").disabled = $("autoSize").checked || Boolean(state.snapshot?.bot.running);
}

function setMode(mode) {
  state.mode = mode;
  const events = mode === "events";
  $("perpsTab").classList.toggle("active", !events);
  $("eventsTab").classList.toggle("active", events);
  $("perpsTab").setAttribute("aria-selected", String(!events));
  $("eventsTab").setAttribute("aria-selected", String(events));
  $("symbolControl").hidden = events;
  $("intervalControl").hidden = events;
  $("contractPanel").hidden = !events;
  $("botButton").disabled = events;
  $("botCaption").textContent = events ? "Event contracts are read-only in this prototype." : "Paper simulation only. No live orders are sent.";
  if (events) renderSelectedMarket();
  else if (state.snapshot) renderSnapshot(state.snapshot);
}

$("symbolSelect").addEventListener("change", (event) => {
  state.symbol = event.target.value;
  refresh();
});
$("intervalSelect").addEventListener("change", (event) => {
  state.interval = event.target.value;
  refresh();
});
$("refreshButton").addEventListener("click", refresh);
$("botButton").addEventListener("click", toggleBot);
$("saveSettings").addEventListener("click", applySettings);
$("autoSize").addEventListener("change", updateManualSizing);
$("perpsTab").addEventListener("click", () => setMode("perps"));
$("eventsTab").addEventListener("click", () => setMode("events"));
window.addEventListener("resize", () => {
  if (!state.snapshot) return;
  if (state.mode === "perps") renderChart(state.snapshot.candles, state.snapshot.indicators);
  else renderSelectedMarket();
});

initializeGoogleSignIn();
refresh();
window.setInterval(refresh, 15000);
