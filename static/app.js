const MANUAL_TOOLS = [
  { tool_name: "quality_report", label: "quality_report (데이터 품질 점검: 빈칸·음수·비슷한 값)" },
  { tool_name: "group_rate", label: "group_rate (단과대별 폐강률)" },
  { tool_name: "top_bottom_n", label: "top_bottom_n (신청 인원 상위·하위 10개)" },
  { tool_name: "fill_rate", label: "fill_rate (단과대별 충원율)" },
  { tool_name: "distribution", label: "distribution (분반당 신청 인원 구간 분포)" },
  { tool_name: "weekday_building", label: "weekday_building (요일별·건물별 수업 분포)" },
  { tool_name: "compare_one_vs_all", label: "compare_one_vs_all (최다 단과대 vs 전체 평균·중앙값)" },
];

const statusBanner = document.getElementById("status-banner");
const sectionAuth = document.getElementById("section-auth");
const appWorkspace = document.getElementById("app-workspace");
const passwordInput = document.getElementById("password-input");
const btnLogin = document.getElementById("btn-login");
const btnLogout = document.getElementById("btn-logout");

const fileInput = document.getElementById("file-input");
const btnInspect = document.getElementById("btn-inspect");
const questionInput = document.getElementById("question-input");
const btnAsk = document.getElementById("btn-ask");
const btnSuggest = document.getElementById("btn-suggest");
const btnRun = document.getElementById("btn-run");
const btnExplain = document.getElementById("btn-explain");
const btnViewPreview = document.getElementById("btn-view-preview");
const btnViewEdit = document.getElementById("btn-view-edit");
const btnResetReport = document.getElementById("btn-reset-report");
const btnCopy = document.getElementById("btn-copy");
const btnDownload = document.getElementById("btn-download");

const sectionInspect = document.getElementById("section-inspect");
const sectionSuggest = document.getElementById("section-suggest");
const sectionResults = document.getElementById("section-results");
const sectionReport = document.getElementById("section-report");

const inspectMetrics = document.getElementById("inspect-metrics");
const warningList = document.getElementById("warning-list");
const previewTable = document.getElementById("preview-table");
const suggestList = document.getElementById("suggest-list");
const manualToolList = document.getElementById("manual-tool-list");
const resultsContainer = document.getElementById("results-container");
const reportPreview = document.getElementById("report-preview");
const reportEditor = document.getElementById("report-editor");

const activeCharts = [];
let originalReportText = "";
let lastReportMode = "B";
let currentReportView = "preview"; // "preview" | "edit"

function showBanner(message, type = "info") {
  statusBanner.textContent = message;
  statusBanner.className = `banner ${type}`;
  statusBanner.classList.remove("hidden");
}

function setBusy(isBusy, message = "처리 중...") {
  const buttons = [btnLogin, btnInspect, btnAsk, btnSuggest, btnRun, btnExplain];
  buttons.forEach((b) => {
    if (b) b.disabled = isBusy;
  });
  if (isBusy) {
    showBanner(message, "info");
  }
}

function buildTable(tableEl, rows) {
  tableEl.innerHTML = "";
  if (!rows || rows.length === 0) {
    tableEl.innerHTML = "<tr><td>표시할 데이터가 없습니다.</td></tr>";
    return;
  }
  const cols = Object.keys(rows[0]);
  const thead = document.createElement("thead");
  const headRow = document.createElement("tr");
  cols.forEach((c) => {
    const th = document.createElement("th");
    th.textContent = c;
    headRow.appendChild(th);
  });
  thead.appendChild(headRow);
  tableEl.appendChild(thead);

  const tbody = document.createElement("tbody");
  rows.forEach((r) => {
    const tr = document.createElement("tr");
    cols.forEach((c) => {
      const td = document.createElement("td");
      const val = r[c];
      td.textContent = val === null || val === undefined ? "(빈칸)" : String(val);
      tr.appendChild(td);
    });
    tbody.appendChild(tr);
  });
  tableEl.appendChild(tbody);
}

function renderManualTools() {
  manualToolList.innerHTML = "";
  MANUAL_TOOLS.forEach((item) => {
    const label = document.createElement("label");
    label.className = "suggest-item";

    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.className = "tool-checkbox";
    cb.dataset.toolName = item.tool_name;
    cb.dataset.params = "{}";

    const box = document.createElement("div");
    const title = document.createElement("div");
    title.className = "suggest-title";
    title.textContent = item.label;
    box.appendChild(title);

    label.appendChild(cb);
    label.appendChild(box);
    manualToolList.appendChild(label);
  });
}

function renderResultCards(results, introBanner = "") {
  while (activeCharts.length > 0) {
    const ch = activeCharts.pop();
    ch.destroy();
  }
  resultsContainer.innerHTML = "";

  if (introBanner) {
    const info = document.createElement("div");
    info.className = "banner info";
    info.textContent = introBanner;
    resultsContainer.appendChild(info);
  }

  (results || []).forEach((item, idx) => {
    const card = document.createElement("div");
    card.className = "result-card";

    const title = document.createElement("h3");
    title.textContent = `${idx + 1}. ${item.title} (${item.tool_name})`;
    card.appendChild(title);

    const meta = document.createElement("div");
    meta.className = "result-meta";
    meta.innerHTML = `
      <p><strong>기준(정의):</strong> ${item.definition}</p>
      <p><strong>사용한 행 수:</strong> ${item.used_rows}행 | <strong>제외한 행 수:</strong> ${item.excluded_rows}행 (${item.excluded_reason})</p>
      <p><strong>사용한 열:</strong> ${(item.used_columns || []).join(", ")} | <strong>단위:</strong> ${item.unit}</p>
      <p><strong>주의사항:</strong> ${(item.cautions || []).join(" / ")}</p>
    `;
    card.appendChild(meta);

    if (item.chart && item.chart.labels && item.chart.labels.length > 0 && typeof Chart !== "undefined") {
      const chartWrap = document.createElement("div");
      chartWrap.className = "chart-wrap";
      const canvas = document.createElement("canvas");
      chartWrap.appendChild(canvas);
      card.appendChild(chartWrap);

      const chart = new Chart(canvas, {
        type: item.chart.type || "bar",
        data: {
          labels: item.chart.labels,
          datasets: [
            {
              label: item.chart.label || item.title,
              data: item.chart.values,
              backgroundColor: "rgba(37, 99, 235, 0.7)",
              borderColor: "rgba(30, 58, 138, 1)",
              borderWidth: 1,
            },
          ],
        },
        options: {
          responsive: true,
          plugins: { legend: { display: true } },
          scales: { y: { beginAtZero: true } },
        },
      });
      activeCharts.push(chart);
    }

    const tableWrap = document.createElement("div");
    tableWrap.className = "table-wrap";
    const table = document.createElement("table");
    buildTable(table, item.rows || []);
    tableWrap.appendChild(table);
    card.appendChild(tableWrap);

    resultsContainer.appendChild(card);
  });

  sectionResults.classList.remove("hidden");
  sectionReport.classList.remove("hidden");
}

function renderMarkdownToPreview(mdText) {
  if (typeof marked !== "undefined" && typeof DOMPurify !== "undefined") {
    const rawHtml = marked.parse(mdText || "");
    reportPreview.innerHTML = DOMPurify.sanitize(rawHtml);
  } else {
    reportPreview.textContent = mdText || "";
  }
}

function showReportView(view) {
  currentReportView = view;
  if (view === "preview") {
    renderMarkdownToPreview(reportEditor.value);
    reportPreview.classList.remove("hidden");
    reportEditor.classList.add("hidden");
    btnViewPreview.classList.add("active-toggle");
    btnViewEdit.classList.remove("active-toggle");
  } else {
    reportEditor.classList.remove("hidden");
    reportPreview.classList.add("hidden");
    btnViewEdit.classList.add("active-toggle");
    btnViewPreview.classList.remove("active-toggle");
  }
}

function markdownToHumanPlainText(md) {
  return (md || "")
    .replace(/^#{1,6}\s+(.+)$/gm, "\n■ $1")
    .replace(/\*\*(.+?)\*\*/g, "$1")
    .replace(/\*(.+?)\*/g, "$1")
    .replace(/`([^`]+)`/g, "$1")
    .replace(/^[\-\*]\s+/gm, "• ")
    .replace(/^\|[\s\-:|]+\|$/gm, "")
    .replace(/^\|(.+)\|$/gm, (_, inner) =>
      inner
        .split("|")
        .map((c) => c.trim())
        .join(" | ")
    )
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

function buildStyledHtmlForPaste(previewEl) {
  const clone = previewEl.cloneNode(true);
  clone.querySelectorAll("h1, h2, h3").forEach((el) => {
    el.style.color = "#1e3a8a";
    el.style.marginTop = "14px";
    el.style.marginBottom = "6px";
  });
  clone.querySelectorAll("table").forEach((tbl) => {
    tbl.style.borderCollapse = "collapse";
    tbl.style.width = "100%";
    tbl.style.margin = "10px 0";
    tbl.style.fontSize = "13px";
  });
  clone.querySelectorAll("th, td").forEach((cell) => {
    cell.style.border = "1px solid #cbd5e1";
    cell.style.padding = "6px 10px";
    cell.style.textAlign = "left";
  });
  clone.querySelectorAll("th").forEach((th) => {
    th.style.backgroundColor = "#f1f5f9";
    th.style.fontWeight = "bold";
  });
  return `<div style="font-family: 'Malgun Gothic', sans-serif; line-height: 1.6; color: #111827;">${clone.innerHTML}</div>`;
}

async function checkAuthStatus() {
  try {
    const res = await fetch("/auth/status");
    const data = await res.json();
    if (data.authenticated) {
      sectionAuth.classList.add("hidden");
      appWorkspace.classList.remove("hidden");
      btnLogout.classList.remove("hidden");
    } else {
      sectionAuth.classList.remove("hidden");
      appWorkspace.classList.add("hidden");
      btnLogout.classList.add("hidden");
      if (!data.password_configured) {
        showBanner(".env 파일에 8자리 이상의 APP_PASSWORD를 먼저 설정해 주세요.", "error");
      }
    }
  } catch (err) {
    console.error("[오류] 인증 상태 확인 실패:", err);
  }
}

async function handleLogin() {
  const pw = passwordInput.value;
  if (pw.length < 8) {
    showBanner("비밀번호는 8자리 이상 입력해야 합니다.", "error");
    return;
  }
  setBusy(true, "비밀번호를 확인하고 있습니다...");
  try {
    const res = await fetch("/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password: pw }),
    });
    const data = await res.json();
    if (!res.ok) {
      throw new Error(data.error || "비밀번호 확인에 실패했습니다.");
    }
    passwordInput.value = "";
    sectionAuth.classList.add("hidden");
    appWorkspace.classList.remove("hidden");
    btnLogout.classList.remove("hidden");
    showBanner("잠금이 해제되었습니다. 엑셀(.xls) 파일을 업로드해 주세요.", "success");
    console.log("[진행] 비밀번호 인증 완료");
  } catch (err) {
    console.error("[오류] 로그인 실패:", err);
    showBanner(err.message, "error");
  } finally {
    setBusy(false);
  }
}

btnLogin.addEventListener("click", handleLogin);
passwordInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter") handleLogin();
});

btnLogout.addEventListener("click", async () => {
  await fetch("/logout", { method: "POST" });
  sectionAuth.classList.remove("hidden");
  appWorkspace.classList.add("hidden");
  btnLogout.classList.add("hidden");
  showBanner("앱이 잠겼습니다. 다시 사용하려면 비밀번호를 입력해 주세요.", "info");
});

btnInspect.addEventListener("click", async () => {
  const file = fileInput.files[0];
  if (!file) {
    showBanner("먼저 업로드할 엑셀(.xls) 파일을 선택해 주세요.", "error");
    return;
  }
  if (!file.name.toLowerCase().endsWith(".xls")) {
    showBanner("구형 엑셀(.xls) 파일만 업로드할 수 있습니다.", "error");
    return;
  }

  console.log("[진행] 파일 진단 시작:", file.name);
  setBusy(true, "처리 중... 파일을 읽고 진단하고 있습니다.");

  try {
    const formData = new FormData();
    formData.append("file", file);
    const res = await fetch("/inspect", { method: "POST", body: formData });
    const data = await res.json();
    if (!res.ok) {
      throw new Error(data.error || "파일 진단에 실패했습니다.");
    }

    console.log("[진행] 파일 진단 완료:", data);

    const cards = [
      ["전체 데이터 행", `${data.data_rows}행 (원본 ${data.raw_rows}행)`],
      ["고유 분반 수", `${data.unique_sections}개`],
      ["교과목번호 / 명", `${data.unique_course_codes}개 / ${data.unique_course_names}개`],
      ["단과대 수", `${data.college_count}개`],
      ["개설유지 / 폐강", `${data.open_rows}행 / ${data.closed_rows}행`],
    ];
    inspectMetrics.innerHTML = cards
      .map(
        ([k, v]) =>
          `<div class="metric-box"><div class="metric-label">${k}</div><div class="metric-value">${v}</div></div>`
      )
      .join("");

    warningList.innerHTML = "";
    (data.warnings || []).forEach((w) => {
      const li = document.createElement("li");
      li.innerHTML = `<strong>[${w.type}]</strong> ${w.message}`;
      warningList.appendChild(li);
    });

    buildTable(previewTable, data.preview || []);
    renderManualTools();
    suggestList.innerHTML = "";
    resultsContainer.innerHTML = "";
    reportPreview.classList.add("hidden");
    reportEditor.classList.add("hidden");
    reportEditor.value = "";
    originalReportText = "";
    [btnViewPreview, btnViewEdit, btnResetReport, btnCopy, btnDownload].forEach((b) => {
      b.disabled = true;
    });

    sectionInspect.classList.remove("hidden");
    sectionSuggest.classList.remove("hidden");
    sectionResults.classList.add("hidden");
    sectionReport.classList.add("hidden");
    showBanner("파일 진단이 완료되었습니다. 질문을 입력하거나 AI 분석 제안을 받아 보세요.", "success");
  } catch (err) {
    console.error("[오류] /inspect 실패:", err);
    showBanner(err.message, "error");
  } finally {
    setBusy(false);
  }
});

async function handleAskQuestion() {
  const q = questionInput.value.trim();
  if (!q) {
    showBanner("분석할 질문을 입력해 주세요.", "error");
    return;
  }
  console.log("[진행] 질문 분석 요청:", q);
  setBusy(true, "처리 중... 질문을 분석 도구로 번역해 pandas로 계산하고 있습니다.");

  try {
    const res = await fetch("/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question: q }),
    });
    const data = await res.json();
    if (!res.ok) {
      throw new Error(data.cannot_reason || data.error || "질문 분석에 실패했습니다.");
    }

    console.log("[진행] 질문 분석 완료:", data);
    renderResultCards(
      data.results || [],
      `질문 해석: "${data.question}" → [${data.tool_name}] ${data.explanation || ""}`
    );
    showBanner("질문에 대한 pandas 계산이 완료되었습니다! 아래에서 보고서를 생성해 보세요.", "success");
  } catch (err) {
    console.error("[오류] /ask 실패:", err);
    showBanner(err.message, "error");
  } finally {
    setBusy(false);
  }
}

btnAsk.addEventListener("click", handleAskQuestion);
questionInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter") handleAskQuestion();
});

btnSuggest.addEventListener("click", async () => {
  console.log("[진행] AI 분석 제안 요청 시작");
  setBusy(true, "처리 중... AI가 진단 요약을 보고 적합한 분석을 고르고 있습니다.");

  try {
    const res = await fetch("/suggest", { method: "POST" });
    const data = await res.json();
    if (!res.ok) {
      throw new Error(data.error || "AI 제안을 불러오지 못했습니다.");
    }

    console.log("[진행] AI 분석 제안 완료:", data);
    suggestList.innerHTML = "";

    (data.suggestions || []).forEach((s) => {
      const label = document.createElement("label");
      label.className = "suggest-item";

      const cb = document.createElement("input");
      cb.type = "checkbox";
      cb.className = "tool-checkbox";
      cb.checked = true;
      cb.dataset.toolName = s.tool_name;
      cb.dataset.params = JSON.stringify(s.params || {});

      const box = document.createElement("div");
      const title = document.createElement("div");
      title.className = "suggest-title";
      title.textContent = s.tool_name;

      const reason = document.createElement("div");
      reason.textContent = s.reason || "";

      const cols = document.createElement("div");
      cols.className = "suggest-meta";
      cols.textContent = `사용 예정 열: ${(s.columns || []).join(", ")}`;

      box.appendChild(title);
      box.appendChild(reason);
      box.appendChild(cols);
      label.appendChild(cb);
      label.appendChild(box);
      suggestList.appendChild(label);
    });

    showBanner("AI 분석 제안이 도착했습니다. 원하는 항목을 체크한 뒤 계산을 실행하세요.", "success");
  } catch (err) {
    console.error("[오류] /suggest 실패:", err);
    showBanner(err.message, "error");
  } finally {
    setBusy(false);
  }
});

btnRun.addEventListener("click", async () => {
  const checked = Array.from(document.querySelectorAll(".tool-checkbox:checked"));
  const seen = new Set();
  const selections = [];

  checked.forEach((cb) => {
    const name = cb.dataset.toolName;
    if (!seen.has(name)) {
      seen.add(name);
      selections.push({
        tool_name: name,
        params: JSON.parse(cb.dataset.params || "{}"),
      });
    }
  });

  if (selections.length === 0) {
    showBanner("계산할 분석 도구를 하나 이상 체크해 주세요.", "error");
    return;
  }

  console.log("[진행] 도구 계산 실행:", selections);
  setBusy(true, "처리 중... pandas가 선택한 분석을 정확히 계산하고 있습니다.");

  try {
    const res = await fetch("/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ selections }),
    });
    const data = await res.json();
    if (!res.ok) {
      throw new Error(data.error || "계산 실행에 실패했습니다.");
    }

    console.log("[진행] 도구 계산 완료:", data);
    renderResultCards(data.results || []);
    showBanner("계산이 완료되었습니다! 아래에서 모드 A/B를 골라 AI 보고서를 받아 보세요.", "success");
  } catch (err) {
    console.error("[오류] /run 실패:", err);
    showBanner(err.message, "error");
  } finally {
    setBusy(false);
  }
});

btnExplain.addEventListener("click", async () => {
  const modeInput = document.querySelector('input[name="report-mode"]:checked');
  const mode = modeInput ? modeInput.value : "B";

  console.log("[진행] 보고서 생성 요청: 모드", mode);
  setBusy(true, `처리 중... 모드 ${mode} 보고서를 작성하고 있습니다.`);

  try {
    const res = await fetch("/explain", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode }),
    });
    const data = await res.json();
    if (!res.ok) {
      throw new Error(data.error || "보고서 생성에 실패했습니다.");
    }

    console.log("[진행] 보고서 생성 완료: 모드", mode);
    originalReportText = data.report || "";
    lastReportMode = mode;
    reportEditor.value = originalReportText;

    [btnViewPreview, btnViewEdit, btnResetReport, btnCopy, btnDownload].forEach((b) => {
      b.disabled = false;
    });
    showReportView("preview");
    showBanner(`모드 ${mode} 보고서가 생성되었습니다. 미리보기 또는 직접 수정하기를 사용해 보세요.`, "success");
  } catch (err) {
    console.error("[오류] /explain 실패:", err);
    showBanner(err.message, "error");
  } finally {
    setBusy(false);
  }
});

btnViewPreview.addEventListener("click", () => showReportView("preview"));
btnViewEdit.addEventListener("click", () => showReportView("edit"));

btnResetReport.addEventListener("click", () => {
  if (!originalReportText) return;
  reportEditor.value = originalReportText;
  if (currentReportView === "preview") {
    renderMarkdownToPreview(originalReportText);
  }
  showBanner("보고서를 AI가 처음 만든 원본으로 되돌렸습니다.", "info");
});

btnCopy.addEventListener("click", async () => {
  const mdText = reportEditor.value;
  if (!mdText) return;

  try {
    if (currentReportView === "preview" && typeof ClipboardItem !== "undefined" && navigator.clipboard.write) {
      renderMarkdownToPreview(mdText);
      const htmlString = buildStyledHtmlForPaste(reportPreview);
      const plainString = markdownToHumanPlainText(mdText);
      const item = new ClipboardItem({
        "text/html": new Blob([htmlString], { type: "text/html" }),
        "text/plain": new Blob([plainString], { type: "text/plain" }),
      });
      await navigator.clipboard.write([item]);
      console.log("[진행] 미리보기 서식(HTML+텍스트) 복사 완료");
      showBanner("보기 좋은 서식으로 복사되었습니다! 카톡·이메일·워드에 바로 붙여넣어 보세요.", "success");
    } else {
      const textToCopy = currentReportView === "preview" ? markdownToHumanPlainText(mdText) : mdText;
      await navigator.clipboard.writeText(textToCopy);
      console.log("[진행] 텍스트 복사 완료");
      showBanner("보고서 내용이 클립보드에 복사되었습니다.", "success");
    }
  } catch (err) {
    console.error("[오류] 복사 실패:", err);
    showBanner("클립보드 복사에 실패했습니다. 직접 드래그해서 복사해 주세요.", "error");
  }
});

btnDownload.addEventListener("click", () => {
  const mdText = reportEditor.value;
  if (!mdText) return;
  const blob = new Blob([mdText], { type: "text/markdown;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `개설강좌_보고서_모드${lastReportMode}.md`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
  console.log("[진행] 보고서 .md 다운로드 완료");
});

checkAuthStatus();
