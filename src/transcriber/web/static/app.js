"use strict";

// ---------------------------------------------------------------- утилиты

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const state = {
  info: null,
  cases: [],
  caseId: null,
  review: null,
  selectedEntities: [],
  pollTimer: null,
};

async function api(method, url, body, raw = false) {
  const opts = { method, headers: { "X-Requested-With": "transcriber" }, credentials: "same-origin" };
  if (body instanceof Blob) {
    opts.body = body;
  } else if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const r = await fetch(url, opts);
  if (r.status === 401 && !url.startsWith("/api/unlock") && !url.startsWith("/api/setup")) {
    showAuth();
    throw new Error("Требуется пароль");
  }
  const data = raw ? r : await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.detail || `Ошибка ${r.status}`);
  return data;
}

function toast(msg, ms = 2500) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.remove("hidden");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => t.classList.add("hidden"), ms);
}

async function guarded(fn) {
  try {
    return await fn();
  } catch (e) {
    toast(e.message || String(e), 4000);
  }
}

const fmtTime = (s) => {
  if (s == null) return "";
  s = Math.floor(s);
  return [Math.floor(s / 3600), Math.floor((s % 3600) / 60), s % 60].map((x) => String(x).padStart(2, "0")).join(":");
};
const fmtDate = (ts) => new Date(ts * 1000).toLocaleString("ru-RU", { dateStyle: "short", timeStyle: "short" });
const typeTitle = (code) => (state.info?.types.find((t) => t.code === code) || {}).title || code;

// ---------------------------------------------------------------- вход

function showAuth() {
  const init = state.info?.initialized;
  $("#auth").classList.remove("hidden");
  $("#auth-hint").textContent = init ? "Введите пароль приложения." : "Задайте пароль приложения. Им шифруются словари соответствий и транскрипты.";
  $("#auth-password2").classList.toggle("hidden", !!init);
  $("#auth-warning").classList.toggle("hidden", !!init);
  $("#auth-submit").textContent = init ? "Войти" : "Задать пароль";
  $("#auth-password").value = "";
  $("#auth-password2").value = "";
  $("#auth-password").focus();
}

$("#auth-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const pw = $("#auth-password").value;
  $("#auth-error").textContent = "";
  try {
    if (!state.info.initialized) {
      if (pw !== $("#auth-password2").value) throw new Error("Пароли не совпадают");
      await api("POST", "/api/setup", { password: pw });
    } else {
      await api("POST", "/api/unlock", { password: pw });
    }
    $("#auth").classList.add("hidden");
    await boot();
  } catch (err) {
    $("#auth-error").textContent = err.message;
  }
});

$("#btn-lock").addEventListener("click", () => guarded(async () => {
  await api("POST", "/api/lock");
  state.info.unlocked = false;
  showAuth();
}));

$("#btn-shutdown").addEventListener("click", () => guarded(async () => {
  if (!confirm("Остановить приложение? Незавершённые задачи будут прерваны.")) return;
  await api("POST", "/api/shutdown");
  document.body.innerHTML = '<p class="center">Приложение остановлено. Вкладку можно закрыть.</p>';
}));

// ---------------------------------------------------------------- вкладки

function showTab(tab) {
  $$(".tab").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
  ["cases", "review", "unmask", "settings"].forEach((v) => $(`#view-${v}`).classList.toggle("hidden", v !== tab));
  if (tab === "unmask") renderUnmaskCases();
  if (tab === "settings") renderSettings();
}
$$(".tab").forEach((b) => b.addEventListener("click", () => {
  showTab(b.dataset.tab);
  if (b.dataset.tab === "cases") loadCases();
}));

// ---------------------------------------------------------------- дела

async function loadCases() {
  state.cases = await api("GET", "/api/cases");
  const ul = $("#case-list");
  ul.innerHTML = state.cases.map((c) => `
    <li data-id="${c.id}" class="${c.id === state.caseId ? "active" : ""}">
      <div>${esc(c.name)}</div>
      <div class="code">${esc(c.code)} · транскриптов: ${c.transcripts ?? 0}</div>
    </li>`).join("") || '<li class="muted">Дел пока нет</li>';
  $$("#case-list li[data-id]").forEach((li) => li.addEventListener("click", () => openCase(li.dataset.id)));
}

$("#new-case").addEventListener("submit", (e) => {
  e.preventDefault();
  guarded(async () => {
    const c = await api("POST", "/api/cases", { name: $("#new-case-name").value });
    $("#new-case-name").value = "";
    await loadCases();
    openCase(c.id);
  });
});

async function openCase(id) {
  state.caseId = id;
  showTab("cases");
  $$("#case-list li").forEach((li) => li.classList.toggle("active", li.dataset.id === id));
  const c = await api("GET", `/api/cases/${id}`);
  renderCase(c);
  schedulePoll(c);
}

function renderCase(c) {
  const el = $("#case-detail");
  const models = state.info.models;
  const missing = Object.entries(models).filter(([n, ok]) => !ok && n !== "t-one").map(([n]) => n);
  el.innerHTML = `
    <div class="review-head">
      <h2 id="case-name">${esc(c.name)}</h2>
      <span class="badge">${esc(c.code)}</span>
      <button class="link" id="case-rename">Переименовать</button>
      <button class="link" id="case-delete" style="color:var(--danger)">Удалить дело</button>
    </div>
    <p class="muted">Название дела видно только здесь и в экспорт не попадает. Код дела ставится в шапку и имя файла.</p>
    ${missing.length ? `<div class="notice danger">Не установлены модели: ${esc(missing.join(", "))}. Распознавание аудио недоступно, импорт транскриптов работает. Запустите установщик повторно.</div>` : ""}
    <div class="card">
      <h3>Новая запись</h3>
      <div class="dropzone" id="dropzone">
        Перетащите аудиофайл (mp3, m4a, wav, ogg, opus, flac, webm) или готовый транскрипт (txt, md, docx, srt, vtt)<br>
        <span class="muted">или нажмите, чтобы выбрать файл</span>
        <input type="file" id="file-input" class="hidden" accept=".mp3,.m4a,.wav,.ogg,.opus,.flac,.webm,.mp4,.aac,.txt,.md,.docx,.srt,.vtt">
      </div>
      <div class="row">
        <label>Собеседников:
          <select id="speakers">
            <option value="auto">авто</option>${[1, 2, 3, 4, 5, 6].map((n) => `<option value="${n}">${n}</option>`).join("")}
          </select>
        </label>
        <span class="muted">Вкладку можно закрыть — обработка продолжится.</span>
      </div>
      <div id="jobs"></div>
    </div>
    <div class="card">
      <h3>Транскрипты</h3>
      ${c.transcripts_list.length ? `
      <table class="list"><thead><tr><th>Запись</th><th>Создан</th><th>Длительность</th><th>Масок</th><th>Статус</th><th></th></tr></thead><tbody>
      ${c.transcripts_list.map((t) => `
        <tr>
          <td>${esc(t.title)}</td>
          <td>${fmtDate(t.created)}</td>
          <td>${t.duration ? fmtTime(t.duration) : (t.source_kind === "text" ? "импорт" : "")}</td>
          <td>${t.spans}</td>
          <td>${t.reviewed ? '<span class="badge ok">проверен</span>' : '<span class="badge warn">ждёт проверки</span>'}</td>
          <td><button data-review="${t.id}" class="primary">Проверить</button> <button data-del="${t.id}" class="link" style="color:var(--danger)">Удалить</button></td>
        </tr>`).join("")}</tbody></table>` : '<p class="muted">Пока нет.</p>'}
    </div>
    <div class="card">
      <h3>Словарь организаций</h3>
      <p class="muted">Названия, которые всегда маскируются в этом деле, по одному в строке.</p>
      <textarea id="org-dict" rows="3">${esc(c.org_dictionary.join("\n"))}</textarea>
      <div class="row"><button id="org-save">Сохранить словарь</button></div>
    </div>`;
  renderJobs(c.jobs);

  $("#case-rename").onclick = () => guarded(async () => {
    const name = prompt("Новое название дела", c.name);
    if (name == null) return;
    await api("PATCH", `/api/cases/${c.id}`, { name });
    await loadCases();
    openCase(c.id);
  });
  $("#case-delete").onclick = () => guarded(async () => {
    if (!confirm(`Удалить дело «${c.name}» вместе со словарём соответствий? Вернуть данные в ответы по этому делу станет невозможно.`)) return;
    await api("DELETE", `/api/cases/${c.id}`);
    state.caseId = null;
    $("#case-detail").innerHTML = '<p class="muted center">Дело удалено.</p>';
    loadCases();
  });
  $("#org-save").onclick = () => guarded(async () => {
    await api("PATCH", `/api/cases/${c.id}`, { org_dictionary: $("#org-dict").value.split("\n") });
    toast("Словарь сохранён. Он применяется к новым записям.");
  });
  $$("[data-review]").forEach((b) => (b.onclick = () => openReview(b.dataset.review)));
  $$("[data-del]").forEach((b) => (b.onclick = () => guarded(async () => {
    if (!confirm("Удалить транскрипт?")) return;
    await api("DELETE", `/api/transcripts/${b.dataset.del}`);
    openCase(c.id);
  })));

  const dz = $("#dropzone");
  const fi = $("#file-input");
  dz.onclick = () => fi.click();
  fi.onchange = () => fi.files[0] && uploadFile(c.id, fi.files[0]);
  dz.ondragover = (e) => { e.preventDefault(); dz.classList.add("over"); };
  dz.ondragleave = () => dz.classList.remove("over");
  dz.ondrop = (e) => {
    e.preventDefault();
    dz.classList.remove("over");
    if (e.dataTransfer.files[0]) uploadFile(c.id, e.dataTransfer.files[0]);
  };
}

function renderJobs(jobs) {
  const el = $("#jobs");
  if (!el) return;
  el.innerHTML = jobs.filter((j) => j.status !== "done" || Date.now() / 1000 - j.created < 600).map((j) => `
    <div class="row" style="flex-direction:column;align-items:stretch">
      <div><b>${esc(j.title)}</b> — ${esc(j.status === "error" ? "ошибка: " + j.error : j.stage)}
        ${j.status === "done" && j.transcript_id ? `<button class="link" data-review="${j.transcript_id}">Перейти к проверке</button>` : ""}</div>
      ${j.status === "running" || j.status === "queued" ? `<div class="progress"><div style="width:${Math.round(j.progress * 100)}%"></div></div>` : ""}
    </div>`).join("");
  $$("[data-review]", el).forEach((b) => (b.onclick = () => openReview(b.dataset.review)));
}

async function uploadFile(caseId, file) {
  await guarded(async () => {
    const speakers = $("#speakers").value;
    const q = new URLSearchParams({ filename: file.name, speakers });
    toast("Загрузка файла…");
    await api("POST", `/api/cases/${caseId}/upload?${q}`, file);
    openCase(caseId);
  });
}

function schedulePoll(c) {
  clearTimeout(state.pollTimer);
  const active = c.jobs.some((j) => j.status === "running" || j.status === "queued");
  if (!active) return;
  state.pollTimer = setTimeout(async () => {
    if (state.caseId !== c.id || $("#view-cases").classList.contains("hidden")) return;
    const fresh = await api("GET", `/api/cases/${c.id}`).catch(() => null);
    if (!fresh) return;
    const wasActive = c.jobs.filter((j) => j.status === "running" || j.status === "queued").length;
    const nowActive = fresh.jobs.filter((j) => j.status === "running" || j.status === "queued").length;
    if (nowActive < wasActive) {
      renderCase(fresh);
      loadCases();
    } else {
      renderJobs(fresh.jobs);
    }
    schedulePoll(fresh);
  }, 1000);
}

// ---------------------------------------------------------------- проверка масок

async function openReview(trId) {
  await guarded(async () => {
    state.review = await api("GET", `/api/transcripts/${trId}`);
    state.selectedEntities = [];
    showTab("review");
    renderReview();
  });
}

async function reviewAction(method, url, body) {
  await guarded(async () => {
    state.review = await api(method, url, body);
    hidePopover();
    renderReview();
  });
}

function utteranceHtml(text, marks) {
  // marks: [{start, end, html attrs}] без пересечений
  marks.sort((a, b) => a.start - b.start);
  let out = "";
  let pos = 0;
  for (const m of marks) {
    if (m.start < pos) continue;
    out += esc(text.slice(pos, m.start));
    out += `<mark ${m.attrs}>${esc(text.slice(m.start, m.end))}</mark>`;
    pos = m.end;
  }
  return out + esc(text.slice(pos));
}

function renderReview() {
  const r = state.review;
  const el = $("#view-review");
  const byUtt = {};
  for (const s of r.spans) {
    for (const p of s.parts) {
      if (p.field !== "text") continue;
      const low = s.confidence < 0.8 || s.case_flag ? " low" : "";
      (byUtt[p.utt] ||= []).push({
        start: p.start, end: p.end,
        attrs: `class="pii${low}" data-span="${s.id}" data-type="${esc(s.type)}" data-label="${esc(s.label)}"`,
      });
    }
  }
  for (const lk of r.leaks) {
    for (const p of lk.parts) {
      (byUtt[p.utt] ||= []).push({ start: p.start, end: p.end, attrs: `class="leak" data-leak="${lk.entity_id}" title="Значение из словаря дела без маски"` });
    }
  }
  const lowSpans = r.spans.filter((s) => s.confidence < 0.8 || s.case_flag);
  const groups = {};
  for (const e of r.entities) (groups[e.type] ||= []).push(e);

  el.innerHTML = `
  <div class="review">
    <div class="review-main" id="review-main">
      <div class="review-head">
        <button id="back-case">← К делу</button>
        <h2>${esc(r.title)}</h2>
        <span class="badge">${esc(r.case_code)}</span>
      </div>
      <div class="card speakers">
        <h3>Спикеры</h3>
        ${r.speakers.length ? r.speakers.map((s) => `
          <div class="row"><span class="muted" style="width:120px">${esc(s.raw)}</span>
          <input data-raw="${esc(s.raw)}" value="${esc(s.display)}" placeholder="Оператор, Клиент…">
          <button data-rename="${esc(s.raw)}">Сохранить</button></div>`).join("") : '<p class="muted">Спикеры не определены.</p>'}
        <p class="muted">Подписывайте спикеров ролями («Оператор», «Клиент»). Имена в подписях маскируются автоматически.</p>
      </div>
      <p class="muted">Нажмите на подсветку, чтобы снять маску или поменять падеж. Выделите текст мышью, чтобы добавить маску.</p>
      <div id="utterances">
      ${r.utterances.map((u, i) => `
        <div class="utt">
          <div class="utt-meta"><div class="who">${esc(u.display || "")}</div><div>${fmtTime(u.start)}</div></div>
          <div class="utt-text" data-utt="${i}">${utteranceHtml(u.text, byUtt[i] || [])}</div>
        </div>`).join("")}
      </div>
    </div>
    <aside class="review-side">
      ${r.leaks.length ? `<div class="side-block"><h3>Без маски</h3>
        ${r.leaks.map((lk) => `<div class="notice danger">«${esc(lk.value)}» встречается без маски: ${lk.parts.length}
          <button class="link" data-mask-leak="${lk.entity_id}">Замаскировать</button></div>`).join("")}</div>` : ""}
      ${r.suggestions.length ? `<div class="side-block"><h3>Возможно, одна сущность</h3>
        ${r.suggestions.map((s) => {
          const a = r.entities.find((e) => e.id === s.a), b = r.entities.find((e) => e.id === s.b);
          return `<div class="notice">${esc(a.value)} и ${esc(b.value)} <span class="muted">(${esc(s.reason)})</span>
            <button class="link" data-merge="${s.a}|${s.b}">Объединить</button></div>`;
        }).join("")}</div>` : ""}
      ${lowSpans.length ? `<div class="side-block"><h3>Проверьте внимательно <span class="badge warn">${lowSpans.length}</span></h3>
        ${lowSpans.map((s) => `<div class="entity"><span class="chip" data-type="${esc(s.type)}">${esc(typeTitle(s.type))}</span>
          <span class="value">${esc(s.value)} ${s.case_flag ? '<span class="muted">— падеж не определён</span>' : '<span class="muted">— низкая уверенность</span>'}</span>
          <button class="link" data-jump="${s.id}">Показать</button></div>`).join("")}</div>` : ""}
      <div class="side-block">
        <h3>Сущности <button id="merge-btn" disabled>Объединить выбранные</button></h3>
        ${Object.entries(groups).map(([type, ents]) => `
          <div class="muted" style="margin-top:8px">${esc(typeTitle(type))}</div>
          ${ents.map((e) => `<div class="entity">
            <input type="checkbox" data-select="${e.id}" data-etype="${esc(e.type)}" ${state.selectedEntities.includes(e.id) ? "checked" : ""}>
            <span class="value">${esc(e.value)}<br><span class="label">${esc(e.label)}</span>
            ${e.variants.length > 1 ? `<br><span class="muted">варианты: ${esc(e.variants.join(", "))}</span>` : ""}</span>
            <span class="badge" title="упоминаний">${e.mentions}</span></div>`).join("")}`).join("") || '<p class="muted">Персональные данные не найдены. Проверьте текст вручную.</p>'}
      </div>
    </aside>
  </div>
  <div class="review-foot">
    <label><input type="checkbox" id="reviewed" ${r.reviewed ? "checked" : ""}> Я проверил маски</label>
    <button id="export-docx" class="primary" ${r.reviewed ? "" : "disabled"}>Скачать DOCX</button>
    <button id="export-md" ${r.reviewed ? "" : "disabled"}>Скачать MD</button>
    <button id="preview">Предпросмотр</button>
    <span class="muted" id="export-status"></span>
  </div>`;

  $("#back-case").onclick = () => openCase(r.case_id);
  $$("[data-rename]").forEach((b) => (b.onclick = () => {
    const input = $(`input[data-raw="${CSS.escape(b.dataset.rename)}"]`);
    reviewAction("POST", `/api/transcripts/${r.id}/speakers`, { raw: b.dataset.rename, display: input.value });
  }));
  $$("mark.pii").forEach((m) => (m.onclick = (e) => { e.stopPropagation(); spanPopover(m); }));
  $$("[data-mask-leak]").forEach((b) => (b.onclick = () => reviewAction("POST", `/api/transcripts/${r.id}/leaks/${b.dataset.maskLeak}`)));
  $$("[data-merge]").forEach((b) => (b.onclick = () => {
    const [keep, drop] = b.dataset.merge.split("|");
    reviewAction("POST", `/api/transcripts/${r.id}/merge`, { keep, drop });
  }));
  $$("[data-jump]").forEach((b) => (b.onclick = () => {
    const m = $(`mark[data-span="${b.dataset.jump}"]`);
    if (!m) return;
    m.scrollIntoView({ behavior: "smooth", block: "center" });
    m.classList.add("flash");
    setTimeout(() => m.classList.remove("flash"), 1500);
  }));
  $$("[data-select]").forEach((cb) => (cb.onchange = () => {
    state.selectedEntities = $$("[data-select]:checked").map((x) => x.dataset.select);
    updateMergeBtn();
  }));
  updateMergeBtn();
  $("#merge-btn").onclick = () => {
    const [keep, drop] = state.selectedEntities;
    state.selectedEntities = [];
    reviewAction("POST", `/api/transcripts/${r.id}/merge`, { keep, drop });
  };
  $("#reviewed").onchange = (e) => reviewAction("POST", `/api/transcripts/${r.id}/reviewed`, { reviewed: e.target.checked });
  $("#export-docx").onclick = () => exportTranscript("docx");
  $("#export-md").onclick = () => exportTranscript("md");
  $("#preview").onclick = () => guarded(async () => {
    const p = await api("GET", `/api/transcripts/${r.id}/preview`);
    showPopover(`<b>Так текст увидит Claude</b><div class="pre">${esc(p.lines.join("\n"))}</div>
      <div class="row"><button id="pv-close">Закрыть</button></div>`, { left: 40, top: window.scrollY + 70, wide: true });
    $("#pv-close").onclick = hidePopover;
  });
  $("#utterances").onmouseup = onSelect;
}

function updateMergeBtn() {
  const sel = state.selectedEntities;
  const types = sel.map((id) => state.review.entities.find((e) => e.id === id)?.type);
  $("#merge-btn").disabled = !(sel.length === 2 && types[0] === types[1]);
}

async function exportTranscript(fmt) {
  await guarded(async () => {
    const res = await api("POST", `/api/transcripts/${state.review.id}/export`, { fmt });
    $("#export-status").textContent = `Сохранено в «Загрузки»: ${res.filename}`;
  });
}

function showPopover(html, pos) {
  const p = $("#popover");
  p.innerHTML = html;
  p.style.left = `${pos.left}px`;
  p.style.top = `${pos.top}px`;
  p.style.maxWidth = pos.wide ? "min(900px, 90vw)" : "360px";
  p.classList.remove("hidden");
}
function hidePopover() { $("#popover").classList.add("hidden"); }
document.addEventListener("mousedown", (e) => {
  if (!$("#popover").contains(e.target) && !e.target.closest("mark.pii")) hidePopover();
});

function spanPopover(mark) {
  const r = state.review;
  const s = r.spans.find((x) => x.id === mark.dataset.span);
  const ent = r.entities.find((e) => e.id === s.entity_id);
  const rect = mark.getBoundingClientRect();
  const caseSel = s.declinable && s.type === "ФИО" || s.declinable && s.type === "ОРГ"
    ? `<div class="row">Падеж: <select id="pp-case">${state.info.cases.map((c) => `<option ${c === s.gcase ? "selected" : ""}>${c}</option>`).join("")}</select></div>` : "";
  showPopover(`
    <div><span class="chip" data-type="${esc(s.type)}">${esc(typeTitle(s.type))}</span> <code>${esc(s.label)}</code></div>
    <div style="margin-top:6px">${esc(s.value)}</div>
    <div class="muted">Упоминаний в записи: ${ent?.mentions ?? 1}${s.source === "dict" ? " · найдено по словарю имён" : ""}</div>
    ${caseSel}
    <div class="row">
      <button id="pp-remove">Снять маску</button>
      ${ent && ent.mentions > 1 ? '<button id="pp-remove-all">Снять все упоминания</button>' : ""}
    </div>`, { left: rect.left + window.scrollX, top: rect.bottom + window.scrollY + 6 });
  $("#pp-remove").onclick = () => reviewAction("DELETE", `/api/transcripts/${r.id}/spans/${s.id}`);
  const all = $("#pp-remove-all");
  if (all) all.onclick = () => reviewAction("DELETE", `/api/transcripts/${r.id}/spans/${s.id}?all=1`);
  const cs = $("#pp-case");
  if (cs) cs.onchange = () => reviewAction("POST", `/api/transcripts/${r.id}/spans/${s.id}/case`, { gcase: cs.value });
}

function offsetIn(container, node, offset) {
  const range = document.createRange();
  range.setStart(container, 0);
  range.setEnd(node, offset);
  return range.toString().length;
}

function onSelect() {
  const sel = window.getSelection();
  if (!sel || sel.isCollapsed || !sel.rangeCount) return;
  const range = sel.getRangeAt(0);
  const a = range.startContainer.parentElement?.closest(".utt-text");
  const b = range.endContainer.parentElement?.closest(".utt-text") || range.endContainer.closest?.(".utt-text");
  if (!a || a !== b) {
    toast("Выделяйте текст в пределах одной реплики");
    return;
  }
  const start = offsetIn(a, range.startContainer, range.startOffset);
  const end = offsetIn(a, range.endContainer, range.endOffset);
  if (end <= start) return;
  const utt = Number(a.dataset.utt);
  const text = state.review.utterances[utt].text.slice(start, end);
  if (!text.trim()) return;
  const rect = range.getBoundingClientRect();
  showPopover(`
    <div>Замаскировать «${esc(text.length > 60 ? text.slice(0, 60) + "…" : text)}»</div>
    <div class="row"><select id="sel-type">${state.info.types.map((t) => `<option value="${esc(t.code)}">${esc(t.title)}</option>`).join("")}</select></div>
    <div class="row"><label><input type="checkbox" id="sel-all" checked> все вхождения в записи</label></div>
    <div class="row"><button class="primary" id="sel-add">Замаскировать</button></div>`,
  { left: rect.left + window.scrollX, top: rect.bottom + window.scrollY + 6 });
  $("#sel-add").onclick = () => reviewAction("POST", `/api/transcripts/${state.review.id}/spans`, {
    utt, start, end, type: $("#sel-type").value, all: $("#sel-all").checked,
  });
}

// ---------------------------------------------------------------- демаскирование

async function renderUnmaskCases() {
  await guarded(async () => {
    state.cases = await api("GET", "/api/cases");
    $("#unmask-case").innerHTML = '<option value="">Определить по коду в тексте</option>' +
      state.cases.map((c) => `<option value="${c.id}">${esc(c.code)} — ${esc(c.name)}</option>`).join("");
  });
}

let lastUnmask = null;
$("#unmask-run").onclick = () => guarded(async () => {
  const text = $("#unmask-input").value;
  if (!text.trim()) return;
  const res = await api("POST", "/api/unmask", { text, case_id: $("#unmask-case").value || null });
  lastUnmask = res;
  $("#unmask-case").value = res.case_id;
  $("#unmask-status").textContent = `Дело ${res.code}: восстановлено ${res.restored}` +
    (res.uncertain ? `, падеж не определён: ${res.uncertain}` : "") + (res.unknown ? `, неизвестных меток: ${res.unknown}` : "");
  let html = "";
  let pos = 0;
  for (const h of res.highlights) {
    html += esc(res.text.slice(pos, h.start));
    html += `<span class="hl ${h.kind}">${esc(res.text.slice(h.start, h.end))}</span>`;
    pos = h.end;
  }
  html += esc(res.text.slice(pos));
  $("#unmask-output").innerHTML = html;
  $("#unmask-result").classList.remove("hidden");
  $("#unmask-saved").textContent = "";
});
$("#unmask-copy").onclick = () => guarded(async () => {
  await navigator.clipboard.writeText(lastUnmask.text);
  toast("Скопировано");
});
for (const fmt of ["docx", "md"]) {
  $(`#unmask-${fmt}`).onclick = () => guarded(async () => {
    const r = await api("POST", "/api/unmask/export", { text: lastUnmask.text, code: lastUnmask.code, fmt });
    $("#unmask-saved").textContent = `Сохранено в «Загрузки»: ${r.filename}`;
  });
}

// ---------------------------------------------------------------- настройки

async function renderSettings() {
  await guarded(async () => {
    const s = await api("GET", "/api/settings");
    const enabled = new Set(s.types || state.info.types.map((t) => t.code));
    const models = state.info.models;
    $("#settings-body").innerHTML = `
      <h2>Настройки</h2>
      <div class="card">
        <h3>Хранение</h3>
        <div class="row"><label>Удалять дела через <input id="st-days" type="number" min="0" max="3650" value="${s.retention_days}" style="width:90px"> дней без изменений</label></div>
        <p class="muted">0 — не удалять автоматически. Удаление перезаписывает данные и сжимает базу.</p>
      </div>
      <div class="card">
        <h3>Распознавание</h3>
        <label><input type="radio" name="engine" value="gigaam" ${s.asr_engine === "gigaam" ? "checked" : ""}> GigaAM v3 — по умолчанию, пунктуация и числа</label><br>
        <label><input type="radio" name="engine" value="tone" ${s.asr_engine === "tone" ? "checked" : ""} ${models["t-one"] ? "" : "disabled"}> T-one — для телефонных записей 8 кГц ${models["t-one"] ? "" : "(модель не установлена)"}</label>
      </div>
      <div class="card">
        <h3>Что маскировать</h3>
        <div class="types-grid">${state.info.types.map((t) => `<label><input type="checkbox" data-type-opt="${esc(t.code)}" ${enabled.has(t.code) ? "checked" : ""}> ${esc(t.title)}</label>`).join("")}</div>
        <p class="warning">Должность, город, даты и суммы не маскируются, но вместе могут указать на человека. Проверяйте их на экране проверки.</p>
      </div>
      <div class="row"><button class="primary" id="st-save">Сохранить</button></div>
      <div class="card">
        <h3>О программе</h3>
        <p>Версия ${esc(state.info.version)}. Распознавание, маскирование и возврат данных выполняются только на этом компьютере.</p>
        <p class="muted">Метки при наличии словаря соответствий — псевдонимизация, а не обезличивание по 152-ФЗ. Передача замаскированного текста зарубежному сервису остаётся трансграничной передачей.</p>
        <p class="muted">Модели: ${Object.entries(models).map(([n, ok]) => `${esc(n)} ${ok ? "✓" : "✗"}`).join(", ")}.
          GigaAM — MIT, T-one — Apache 2.0, Natasha — MIT, sherpa-onnx — Apache 2.0, pyannote segmentation 3.0 — MIT, 3D-Speaker CAM++ — Apache 2.0.</p>
        <div class="row"><button id="upd-check">Проверить обновления</button><span id="upd-status" class="muted"></span></div>
      </div>`;
    $("#st-save").onclick = () => guarded(async () => {
      await api("PUT", "/api/settings", {
        retention_days: Number($("#st-days").value),
        asr_engine: $('input[name="engine"]:checked')?.value,
        types: $$("[data-type-opt]:checked").map((x) => x.dataset.typeOpt),
      });
      toast("Сохранено");
    });
    $("#upd-check").onclick = () => guarded(async () => {
      $("#upd-status").textContent = "Проверка…";
      const r = await api("POST", "/api/update/check");
      if (!r.configured || r.error) {
        $("#upd-status").textContent = r.message || r.error;
      } else if (r.available) {
        $("#upd-status").innerHTML = `Доступна версия ${esc(r.latest)} <button id="upd-apply">Обновить</button>`;
        $("#upd-apply").onclick = () => guarded(async () => {
          $("#upd-status").textContent = "Обновление…";
          const a = await api("POST", "/api/update/apply");
          $("#upd-status").textContent = a.message;
        });
      } else {
        $("#upd-status").textContent = "Установлена последняя версия";
      }
    });
  });
}

// ---------------------------------------------------------------- старт

async function boot() {
  state.info = await api("GET", "/api/state");
  if (!state.info.unlocked) {
    showAuth();
    return;
  }
  await loadCases();
  showTab("cases");
}

boot().catch((e) => toast(e.message));
