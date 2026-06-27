// Stage 0 — wiring check only. Pings the API so we can confirm the static
// frontend and the FastAPI backend are served together and talking.
// The playback engine and leaderboard arrive in Stage 6.

async function checkHealth() {
  const el = document.getElementById("health");
  try {
    const res = await fetch("/api/health");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    el.textContent = `${data.status} (stage ${data.stage})`;
    el.className = "ok";
  } catch (err) {
    el.textContent = `unreachable — ${err.message}`;
    el.className = "err";
  }
}

checkHealth();
