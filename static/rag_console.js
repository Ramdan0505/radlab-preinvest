// CONFIG
const baseUrl = "";  // same-origin (http://127.0.0.1:8080)
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

// ---------------- TEXT INGEST ----------------
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
    result.textContent = pretty(err.body ?? err);
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

// ---------------- FILE INGEST (/ingest_file) ----------------
const ingestFileInput = document.getElementById("ingestFile");
const ingestFileBtn = document.getElementById("ingestFileBtn");
const ingestFileStatus = document.getElementById("ingestFileStatus");
const ingestFileResult = document.getElementById("ingestFileResult");

ingestFileBtn.addEventListener("click", async () => {
  const file = ingestFileInput.files[0];
  ingestFileStatus.textContent = "";
  ingestFileResult.textContent = "{}";

  if (!file) {
    ingestFileStatus.textContent = "Choose a file first.";
    return;
  }

  const form = new FormData();
  form.append("file", file);

  ingestFileBtn.disabled = true;
  ingestFileStatus.textContent = "Uploading & ingesting file...";
  try {
    const resp = await fetch(baseUrl + "/ingest_file", {
      method: "POST",
      body: form      // IMPORTANT: do NOT set Content-Type; browser sets multipart boundary
    });

    const text = await resp.text();
    let data;
    try { data = JSON.parse(text); } catch { data = text; }

    if (!resp.ok) {
      throw { status: resp.status, body: data };
    }

    ingestFileStatus.textContent = "File ingest OK.";
    ingestFileResult.textContent = pretty(data);

    // if backend returned a case_id, remember it so search/case viewer can use it
    if (data && data.case_id) {
      lastCaseId = data.case_id;
    }
  } catch (err) {
    ingestFileStatus.textContent = `Error: ${err.status ?? "?"}`;
    ingestFileResult.textContent = pretty(err.body ?? err);
  } finally {
    ingestFileBtn.disabled = false;
  }
});

// ---------------- SEARCH ----------------
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
    status.textContent = "Missing case_id: ingest first (text or file).";
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
    result.textContent = pretty(err.body ?? err);
  } finally {
    searchBtn.disabled = false;
  }
});

document.getElementById("searchClearBtn").addEventListener("click", () => {
  document.getElementById("searchQuery").value = "";
  document.getElementById("searchResult").textContent = "{}";
  document.getElementById("searchStatus").textContent = "";
});

// ---------------- CASE VIEWER ----------------
const loadCasesBtn = document.getElementById("loadCasesBtn");
const caseListDisplay = document.getElementById("caseListDisplay");
const caseDetails = document.getElementById("caseDetails");

loadCasesBtn.addEventListener("click", async () => {
  caseListDisplay.innerHTML = "Loading...";

  try {
    const res = await callApi("/cases");
    caseListDisplay.innerHTML = "";

    for (const c of res.cases || []) {
      const li = document.createElement("li");
      li.textContent = c.case_id;
      li.style.cursor = "pointer";
      li.style.color = "#22c55e";

      li.onclick = async () => {
        const details = await callApi(`/cases/${c.case_id}`);
        caseDetails.textContent = pretty(details);
      };

      caseListDisplay.appendChild(li);
    }

    if (!res.cases || res.cases.length === 0) {
      caseListDisplay.textContent = "No cases yet. Ingest something first.";
    }
  } catch (err) {
    caseListDisplay.textContent = pretty(err.body ?? err);
  }
});

const explainCaseBtn = document.getElementById("explainCaseBtn");
const explainStatus = document.getElementById("explainStatus");
const explainResult = document.getElementById("explainResult");

explainCaseBtn.addEventListener("click", async () => {
  if (!lastCaseId) {
    explainStatus.textContent = "No case selected.";
    return;
  }

  explainCaseBtn.disabled = true;
  explainStatus.textContent = "Summarizing case...";
  explainResult.textContent = "{}";

  try {
    const res = await callApi("/explain_case", {
      method: "POST",
      body: JSON.stringify({ case_id: lastCaseId })
    });

    explainStatus.textContent = "AI summary ready.";
    explainResult.textContent = res.summary || pretty(res);
  } catch (err) {
    explainStatus.textContent = `Error: ${err.status ?? "?"}`;
    explainResult.textContent = pretty(err.body ?? err);
  } finally {
    explainCaseBtn.disabled = false;
  }
});

// Copy / Download AI report
const copyExplainBtn = document.getElementById("copyExplainBtn");
const downloadExplainBtn = document.getElementById("downloadExplainBtn");

copyExplainBtn.addEventListener("click", async () => {
  const text = explainResult.textContent.trim();
  if (!text || text === "{}") {
    explainStatus.textContent = "Nothing to copy yet. Run Explain Case first.";
    return;
  }

  try {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      await navigator.clipboard.writeText(text);
      explainStatus.textContent = "Report copied to clipboard.";
    } else {
      // Fallback: select text
      const range = document.createRange();
      range.selectNodeContents(explainResult);
      const sel = window.getSelection();
      sel.removeAllRanges();
      sel.addRange(range);
      try {
        const ok = document.execCommand("copy");
        explainStatus.textContent = ok ? "Report copied to clipboard." : "Copy failed.";
      } finally {
        sel.removeAllRanges();
      }
    }
  } catch (e) {
    explainStatus.textContent = "Copy failed.";
    console.error("Copy report failed:", e);
  }
});

downloadExplainBtn.addEventListener("click", () => {
  const text = explainResult.textContent.trim();
  if (!text || text === "{}") {
    explainStatus.textContent = "Nothing to download yet. Run Explain Case first.";
    return;
  }

  const blob = new Blob([text], { type: "text/markdown" });

  // Use case ID in filename if available
  const caseIdForName = (typeof lastCaseId === "string" && lastCaseId) ? lastCaseId : "unknown_case";
  const filename = `dfir_report_${caseIdForName}.md`;

  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);

  explainStatus.textContent = `Report downloaded as ${filename}.`;
});

// ---------------- MITRE ATT&CK extraction from AI summary ----------------
const mitreExtractBtn = document.getElementById("mitreExtractBtn");
const mitreStatus = document.getElementById("mitreStatus");
const mitreResult = document.getElementById("mitreResult");

mitreExtractBtn.addEventListener("click", async () => {
  const summary = explainResult.textContent.trim();
  if (!lastCaseId) {
    mitreStatus.textContent = "No case selected.";
    return;
  }
  if (!summary || summary === "{}") {
    mitreStatus.textContent = "No summary yet. Run Explain Case first.";
    return;
  }

  mitreExtractBtn.disabled = true;
  mitreStatus.textContent = "Extracting MITRE techniques...";
  mitreResult.textContent = "{}";

  try {
    const body = { case_id: lastCaseId, summary };
    const res = await callApi("/mitre_tags", {
      method: "POST",
      body: JSON.stringify(body)
    });

    mitreStatus.textContent = "MITRE extraction OK.";
    // Expect res.tags to be either an array or raw text; pretty-print if structured
    mitreResult.textContent = res.tags ? pretty(res.tags) : pretty(res);
  } catch (err) {
    mitreStatus.textContent = `Error: ${err.status ?? "?"}`;
    mitreResult.textContent = pretty(err.body ?? err);
  } finally {
    mitreExtractBtn.disabled = false;
  }
});

