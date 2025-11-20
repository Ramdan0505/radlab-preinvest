// CONFIG
const baseUrl = ""; 
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
    const body = { text, metadata };
    const res = await callApi("/ingest", {
      method: "POST",
      body: JSON.stringify(body)
    });

    status.textContent = "Ingest OK.";
    result.textContent = pretty(res);

    if (res && res.case_id) {
      lastCaseId = res.case_id;
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

  const case_id = lastCaseId;

  if (!query) {
    status.textContent = "Enter query.";
    return;
  }
  if (!case_id) {
    status.textContent = "Missing case_id: ingest first.";
    return;
  }

  searchBtn.disabled = true;
  status.textContent = "Searching...";
  result.textContent = "{}";

  try {
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


// CASE VIEWER
const loadCasesBtn = document.getElementById("loadCasesBtn");
const caseList = document.getElementById("caseList");
const caseDetails = document.getElementById("caseDetails");

loadCasesBtn.addEventListener("click", async () => {
  caseList.textContent = "Loading...";
  try {
    const res = await callApi("/cases");
    caseList.textContent = pretty(res);

    // Click handler for selecting a case
    caseList.onclick = async (evt) => {
      const text = evt.target.textContent;
      const match = text.match(/"case_id": "([^"]+)"/);
      if (!match) return;

      const case_id = match[1];
      const details = await callApi(`/cases/${case_id}`);
      caseDetails.textContent = pretty(details);
    };
  } catch (err) {
    caseList.textContent = pretty(err.body);
  }
});
