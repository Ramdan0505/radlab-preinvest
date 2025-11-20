// CONFIG
const baseUrl = ""; // set to "/api" ONLY if your API is actually under that prefix
document.getElementById("baseUrlDisplay").textContent = baseUrl || "/";

async function callApi(path, options = {}) {
  const resp = await fetch(baseUrl + path, {
    headers: { "Content-Type": "application/json" },
    ...options
  });
  const text = await resp.text();
  let data;
  try { data = JSON.parse(text); } catch { data = text; }
  if (!resp.ok) throw { status: resp.status, body: data };
  return data;
}

function pretty(obj) {
  return JSON.stringify(obj, null, 2);
}

// Keep last case id in memory so search can work without manual entry
let lastCaseId = "";

// INGEST
const ingestBtn = document.getElementById("ingestBtn");
ingestBtn.addEventListener("click", async () => {
  const text = document.getElementById("ingestText").value.trim();
  const metadataRaw = document.getElementById("ingestMetadata").value.trim();
  const status = document.getElementById("ingestStatus");
  const result = document.getElementById("ingestResult");

  if (!text) {
    status.textContent = "Enter text.";
    return;
  }

  let metadata;
try {
    metadata = metadataRaw ? JSON.parse(metadataRaw) : { source: "ui" };
} catch {
    status.textContent = "Invalid metadata JSON.";
    return;
}


  ingestBtn.disabled = true;
  status.textContent = "Ingesting...";
  result.textContent = "{}";

  try {
    const body = { text };
    if (metadata) body.metadata = metadata;

    // JSON ingest endpoint; backend will generate a case_id if absent
    const res = await callApi("/ingest", {
      method: "POST",
      body: JSON.stringify(body)
    });

    status.textContent = "Ingest OK.";
    result.textContent = pretty(res);

    // Capture and display the case_id for subsequent searches
    if (res && res.case_id) {
      lastCaseId = res.case_id;
      const caseInput = document.getElementById("caseId");
      if (caseInput) caseInput.value = res.case_id;
    }
  } catch (err) {
    status.textContent = `Error: ${err.status ?? "?"}`;
    result.textContent = pretty(err.body);
  } finally {
    ingestBtn.disabled = false;
  }
});

document.getElementById("ingestClearBtn").addEventListener("click", () => {
  document.getElementById("ingestText").value = "";
  document.getElementById("ingestMetadata").value = "";
  document.getElementById("ingestStatus").textContent = "";
  document.getElementById("ingestResult").textContent = "{}";
});

// SEARCH
const searchBtn = document.getElementById("searchBtn");
searchBtn.addEventListener("click", async () => {
  const query = document.getElementById("searchQuery").value.trim();
  const top_k = parseInt(document.getElementById("searchTopK").value || "5", 10);
  const include_metadata = document.getElementById("searchIncludeMeta").checked;

  const status = document.getElementById("searchStatus");
  const result = document.getElementById("searchResult");
  const caseInput = document.getElementById("caseId");

  const case_id = (caseInput && caseInput.value.trim()) || lastCaseId;

  if (!query) {
    status.textContent = "Enter query.";
    return;
  }
  if (!case_id) {
    status.textContent = "Missing case_id: ingest text first or enter a case id.";
    return;
  }

  searchBtn.disabled = true;
  status.textContent = "Searching...";
  result.textContent = "{}";

  try {
    // Backend ignores include_metadata in our current model, but it's harmless to include.
    const body = { case_id, query, top_k, include_metadata };

    const res = await callApi("/search", {
      method: "POST",
      body: JSON.stringify(body)
    });

    status.textContent = "Search OK.";
    result.textContent = pretty(res);
  } catch (err) {
    status.textContent = `Error: ${err.status ?? "?"}`;
    result.textContent = pretty(err.body);
  } finally {
    searchBtn.disabled = false;
  }
});

document.getElementById("searchClearBtn").addEventListener("click", () => {
  document.getElementById("searchQuery").value = "";
  document.getElementById("searchResult").textContent = "{}";
  document.getElementById("searchStatus").textContent = "";
});
