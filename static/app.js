const defaultQuestion =
  "Revenue is up this fiscal week versus last fiscal week. What was the biggest driver of the increase?";

const questionInput = document.querySelector("#questionInput");
const status = document.querySelector("#status");
const singleAgentCard = document.querySelector("#singleAgentCard");
const twoAgentCard = document.querySelector("#twoAgentCard");
let loadTimer;

questionInput.value = defaultQuestion;
questionInput.addEventListener("input", () => {
  clearTimeout(loadTimer);
  loadTimer = setTimeout(() => loadDemo(questionInput.value || defaultQuestion), 250);
});

loadDemo(defaultQuestion);

async function loadDemo(question) {
  setStatus("Running the seeded analysis…");
  try {
    const response = await fetch("/api/demo", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question }),
    });
    const payload = await response.json();
    renderSingleAgent(payload.single_agent);
    renderTwoAgent(payload.two_agent);
    setStatus(payload.scenario_note);
  } catch {
    setStatus("The server did not respond. Start `python3 server.py` and reload the page.");
  }
}

function setStatus(message) {
  status.textContent = message;
}

function renderSingleAgent(data) {
  singleAgentCard.innerHTML = `
    <div class="card-header">
      <span class="panel-tag">Firm agent + optimizer</span>
      <span class="token-pill">${data.query_shape}</span>
    </div>
    <div class="driver-grid compact-grid">
      <div><strong>Source</strong>${data.source_table}</div>
      <div><strong>Rows scanned</strong>${data.rows_scanned.toLocaleString()}</div>
      <div><strong>Call context</strong>${data.context_tokens} tok</div>
      <div><strong>Driver</strong>${data.top_driver.product_family}</div>
    </div>

    <p class="section-label">Query plan</p>
    <div class="spec-box">${data.plan_highlight}</div>

    <p class="section-label">SQL</p>
    <pre>${escapeHtml(data.sql)}</pre>

    <p class="section-label">Answer</p>
    <div class="answer-box">${data.answer}</div>
  `;
}

function renderTwoAgent(data) {
  const dbPlusHandoff = data.database_agent_tokens + data.handoff_tokens;
  twoAgentCard.innerHTML = `
    <div class="card-header">
      <span class="panel-tag">Firm agent + native agent + optimizer</span>
      <span class="token-pill">${data.query_shape}</span>
    </div>
    <div class="driver-grid compact-grid">
      <div><strong>Source</strong>${data.source_table}</div>
      <div><strong>Rows scanned</strong>${data.rows_scanned.toLocaleString()}</div>
      <div class="token-breakdown">
        <strong>Per-call context</strong>
        <span>Firm call<em>${data.firm_agent_tokens} tok</em></span>
        <span>DB + handoff<em>${dbPlusHandoff} tok</em></span>
        <small>never loaded together</small>
      </div>
      <div><strong>Driver</strong>${data.top_driver.product_family}</div>
    </div>

    <p class="section-label">Query plan</p>
    <div class="spec-box">${data.plan_highlight}</div>

    <p class="section-label">SQL</p>
    <pre>${escapeHtml(data.sql)}</pre>

    <p class="section-label">Answer</p>
    <div class="answer-box">${data.answer}</div>
  `;
}

function escapeHtml(text) {
  return text
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}
