// CONFIG
const baseUrl = ""; // change to "/api" if needed
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

// INGEST
const ingestBtn = document.getElementById("ingestBtn");
ingestBtn.addEventListener("click", async () => {
  const text = document.getElementById("ingestText").value.trim();
  const metadataRaw = document.getElementById("ingestMetadata").value.trim();
  const status = document.getElementById("ingestStatus");
  const result = document.getElementById("ingestResult");

  if (!text) return status.textContent = "Enter text.";

  let metadata;
  if (metadataRaw) {
    try { metadata = JSON.parse(metadataRaw); }
    catch { return status.textContent = "Invalid metadata JSON."; }
  }

  ingestBtn.disabled = true;
  status.textContent = "Ingesting...";
  result.textContent = "{}";

  try {
    const body = { text };
    if (metadata) body.metadata = metadata;

    const res = await callApi("/ingest", {
      method: "POST",
      body: JSON.stringify(body)
    });

    status.textContent = "Ingest OK.";
    result.textContent = pretty(res);
  } catch (err) {
    status.textContent = `Error: ${err.status}`;
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
  const top_k = parseInt(document.getElementById("searchTopK").value || "5");
  const include_metadata = document.getElementById("searchIncludeMeta").checked;

  const status = document.getElementById("searchStatus");
  const result = document.getElementById("searchResult");

  if (!query) return status.textContent = "Enter query.";

  searchBtn.disabled = true;
  status.textContent = "Searching...";
  result.textContent = "{}";

  try {
    const body = { query, top_k, include_metadata };
    const res = await callApi("/search", {
      method: "POST",
      body: JSON.stringify(body)
    });

    status.textContent = "Search OK.";
    result.textContent = pretty(res);
  } catch (err) {
    status.textContent = `Error: ${err.status}`;
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
