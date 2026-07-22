import "./style.css";
import "./mobile.css";
import { api, ApiError, Candidate, EventItem, SimilarExamples } from "./api";
import { readState, shiftMonth, State, writeState } from "./state";

const app = document.querySelector<HTMLDivElement>("#app")!;
let state = readState(),
  authenticated = false,
  generation = 0,
  controller: AbortController | null = null;
const labels: Record<string, string> = {
  high: "高",
  middle_high: "中〜高",
  middle: "中",
  low: "低",
  draft: "下書き",
  needs_review: "要確認",
  approved: "承認済み",
  hidden: "非表示",
};
const escapeHtml = (value: unknown) =>
  String(value ?? "").replace(
    /[&<>'"]/g,
    (char) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" })[
        char
      ]!,
  );
const safeUrl = (value: string) => {
  try {
    const url = new URL(value);
    return ["http:", "https:"].includes(url.protocol) ? url.href : "#";
  } catch {
    return "#";
  }
};
export const formatICT = (value: string) =>
  new Intl.DateTimeFormat("ja-JP", {
    timeZone: "Asia/Ho_Chi_Minh",
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value)) + " ICT";
export function readHiddenJobs() {
  try {
    const value = JSON.parse(localStorage.getItem("hiddenJobs") || "[]");
    return new Set<string>(
      Array.isArray(value)
        ? value.filter((item) => typeof item === "string")
        : [],
    );
  } catch {
    return new Set<string>();
  }
}
export function optionalImportanceScore(value: FormDataEntryValue | null) {
  if (value === null || value === "") return null;
  const score = Number(value);
  if (!Number.isInteger(score) || score < 0 || score > 100)
    throw new Error("重要度スコアは0〜100の整数で入力してください。");
  return score;
}
const badge = (event: EventItem) =>
  `<span class="badge importance-${escapeHtml(event.importance_level)}">重要度 ${escapeHtml(labels[event.importance_level || ""] || "未設定")}</span>${event.must_include ? '<span class="badge must">必須掲載</span>' : ""}<span class="badge">${escapeHtml(labels[event.publication_status] || event.publication_status)}</span>`;

function isCurrent(id: number) {
  return id === generation;
}
function shell(content: string) {
  app.innerHTML = `<header><div><p class="eyebrow">EDITORIAL JOURNAL</p><h1>Vietnam Calendar</h1></div><button id="logout" class="quiet">ログアウト</button></header><nav aria-label="主要メニュー"><button data-view="calendar" ${state.view === "calendar" ? 'aria-current="page"' : ""}>カレンダー</button><button data-view="review" ${state.view === "review" ? 'aria-current="page"' : ""}>レビュー</button><button data-view="operations" ${state.view === "operations" ? 'aria-current="page"' : ""}>運用状況</button></nav><main id="main" tabindex="-1">${content}</main><div id="toast" role="status" aria-live="polite"></div>`;
  document
    .querySelectorAll<HTMLButtonElement>("[data-view]")
    .forEach((button) =>
      button.addEventListener("click", () =>
        go({
          ...state,
          view: button.dataset.view as State["view"],
          eventId: "",
        }),
      ),
    );
  const logout = document.querySelector<HTMLButtonElement>("#logout")!;
  logout.addEventListener("click", () =>
    mutate(
      logout,
      async () => {
        await api.logout();
        authenticated = false;
        await render();
      },
      "ログアウトしました",
    ),
  );
}
export function toast(message: string, error = false) {
  const element = document.querySelector("#toast");
  if (element) {
    element.textContent = message;
    element.className = error ? "error" : "";
    element.setAttribute("role", error ? "alert" : "status");
    element.setAttribute("aria-live", error ? "assertive" : "polite");
  }
}
function errorMessage(error: unknown) {
  return error instanceof Error
    ? error.message
    : "操作に失敗しました。もう一度お試しください。";
}
async function mutate(
  button: HTMLButtonElement,
  operation: () => Promise<void>,
  success?: string,
) {
  if (button.disabled) return;
  button.disabled = true;
  button.setAttribute("aria-busy", "true");
  try {
    await operation();
    if (success) toast(success);
  } catch (error) {
    toast(errorMessage(error), true);
  } finally {
    button.disabled = false;
    button.removeAttribute("aria-busy");
  }
}
function go(next: State, replace = false) {
  state = next;
  writeState(state, replace);
  void render();
}

function login() {
  app.innerHTML = `<main class="login" id="main"><form id="login"><p class="eyebrow">PRIVATE EDITORIAL TOOL</p><h1>Vietnam Calendar</h1><p>管理者アカウントでログインしてください。</p><label>ユーザー名<input name="username" autocomplete="username" required></label><label>パスワード<input name="password" type="password" autocomplete="current-password" required minlength="8"></label><button>ログイン</button><p id="login-error" role="alert"></p></form></main>`;
  const form = document.querySelector<HTMLFormElement>("#login")!;
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = form.querySelector<HTMLButtonElement>("button")!,
      error = document.querySelector<HTMLParagraphElement>("#login-error")!,
      data = new FormData(form);
    button.disabled = true;
    error.textContent = "";
    try {
      await api.login(
        String(data.get("username")),
        String(data.get("password")),
      );
      authenticated = true;
      await render();
    } catch (reason) {
      error.textContent = errorMessage(reason);
    } finally {
      button.disabled = false;
    }
  });
}
function eventCards(events: EventItem[]) {
  return events.length
    ? `<div class="cards">${events.map((event) => `<button class="card" data-event="${event.id}"><span>${escapeHtml(event.category)}</span><h3>${escapeHtml(event.title_ja)}</h3><div>${badge(event)}</div><p>${escapeHtml(event.summary_ja)}</p></button>`).join("")}</div>`
    : '<p class="empty">該当する出来事はありません。</p>';
}
function bindCards() {
  document
    .querySelectorAll<HTMLButtonElement>("[data-event]")
    .forEach((button) =>
      button.addEventListener("click", () =>
        go({ ...state, eventId: button.dataset.event! }),
      ),
    );
}
function bindFilters() {
  const form = document.querySelector<HTMLFormElement>("#filters");
  form?.addEventListener("submit", (event) => {
    event.preventDefault();
    const data = new FormData(form);
    go({
      ...state,
      q: String(data.get("q") || ""),
      importance: String(data.get("importance") || ""),
      category: String(data.get("category") || ""),
      dateFrom: String(data.get("from") || ""),
      dateTo: String(data.get("to") || ""),
      publisher: String(data.get("publisher") || ""),
    });
  });
  document
    .querySelector<HTMLButtonElement>("#clear-filters")
    ?.addEventListener("click", () =>
      go({
        ...state,
        q: "",
        importance: "",
        category: "",
        dateFrom: "",
        dateTo: "",
        publisher: "",
      }),
    );
}
function filterForm() {
  return `<form id="filters" class="filters"><label>検索<input name="q" value="${escapeHtml(state.q)}" placeholder="タイトル・要約（% と _ も文字として検索）"></label><label>開始日<input type="date" name="from" value="${state.dateFrom}"></label><label>終了日<input type="date" name="to" value="${state.dateTo}"></label><label>重要度<select name="importance"><option value="">すべて</option>${["high", "middle_high", "middle", "low"].map((value) => `<option value="${value}" ${state.importance === value ? "selected" : ""}>${labels[value]}</option>`).join("")}</select></label><label>カテゴリ<input name="category" value="${escapeHtml(state.category)}"></label><label>媒体<input name="publisher" value="${escapeHtml(state.publisher)}"></label><button>絞り込む</button><button type="button" id="clear-filters" class="quiet">条件を解除</button></form>`;
}
function addFilters(params: URLSearchParams) {
  if (state.q) params.set("q", state.q);
  if (state.importance) params.set("importance", state.importance);
  if (state.category) params.set("category", state.category);
  if (state.dateFrom) params.set("date_from", state.dateFrom);
  if (state.dateTo) params.set("date_to", state.dateTo);
  if (state.publisher) params.set("publisher", state.publisher);
}

async function calendar(
  id: number,
  signal: AbortSignal,
  offset = 0,
  accumulated: EventItem[] = [],
) {
  const params = new URLSearchParams({
    status: "approved",
    limit: "100",
    offset: String(offset),
  });
  if (!state.dateFrom && !state.dateTo) params.set("event_date", state.date);
  addFilters(params);
  const [calendarData, page] = await Promise.all([
    api.calendar(state.month, false, signal),
    api.events(params, signal),
  ]);
  if (!isCurrent(id)) return;
  const days = new Map(calendarData.days.map((day) => [day.date, day])),
    [year, month] = state.month.split("-").map(Number),
    first = new Date(Date.UTC(year, month - 1, 1)),
    count = new Date(Date.UTC(year, month, 0)).getUTCDate(),
    weeks: string[][] = [];
  let week = Array(first.getUTCDay()).fill(
    '<td class="blank" aria-hidden="true"></td>',
  );
  for (let day = 1; day <= count; day++) {
    const value = `${state.month}-${String(day).padStart(2, "0")}`,
      info = days.get(value),
      weekday = ["日", "月", "火", "水", "木", "金", "土"][
        (first.getUTCDay() + day - 1) % 7
      ];
    week.push(
      `<td><button class="day ${value === state.date ? "selected" : ""}" data-date="${value}" aria-label="${month}月${day}日 ${weekday}曜日${info ? `、出来事${info.count}件、最高重要度${labels[info.highest_importance || ""] || "未設定"}${info.has_must_include ? "、必須掲載あり" : ""}、カテゴリ${info.categories.join("、") || "未設定"}` : "、出来事なし"}" ${value === state.date ? 'aria-current="date"' : ""}><span>${day}</span>${info ? `<strong>${info.count}件</strong><small>最高: ${escapeHtml(labels[info.highest_importance || ""] || "未設定")}${info.has_must_include ? "・必須あり" : ""}<br>分類: ${escapeHtml(info.categories.join("、") || "未設定")}</small>` : ""}</button></td>`,
    );
    if (week.length === 7) {
      weeks.push(week);
      week = [];
    }
  }
  if (week.length) {
    while (week.length < 7)
      week.push('<td class="blank" aria-hidden="true"></td>');
    weeks.push(week);
  }
  const visibleEvents = [...accumulated, ...page.items],
    mobileDays = calendarData.days
      .map(
        (day) =>
          `<button data-date="${day.date}"><strong>${escapeHtml(day.date)}</strong> ${day.count}件・${escapeHtml(day.categories.join("、") || "カテゴリ未設定")}</button>`,
      )
      .join("");
  const rangeMode = Boolean(state.dateFrom || state.dateTo),
    resultLabel = rangeMode
      ? `期間検索 ${state.dateFrom || "開始指定なし"}〜${state.dateTo || "終了指定なし"}`
      : `${state.date} この日の出来事`;
  shell(
    `<section class="toolbar"><button id="prev" aria-label="前月">←</button><h2>${year}年 ${month}月</h2><button id="next" aria-label="次月">→</button><button id="today" class="quiet">今日</button></section>${filterForm()}<div class="calendar-wrap"><table class="calendar" aria-label="${year}年${month}月のカレンダー"><thead><tr>${["日", "月", "火", "水", "木", "金", "土"].map((day) => `<th scope="col">${day}</th>`).join("")}</tr></thead><tbody>${weeks.map((row) => `<tr>${row.join("")}</tr>`).join("")}</tbody></table></div><div class="mobile-days" aria-label="出来事のある日一覧">${mobileDays || "<p>この月に出来事はありません。</p>"}</div><section><p class="eyebrow">${escapeHtml(resultLabel)}</p><h2>検索結果 <span class="count">${page.total}</span></h2>${eventCards(visibleEvents)}${page.has_more ? '<button id="load-more" class="load-more">さらに読み込む</button>' : ""}</section>`,
  );
  document
    .querySelector<HTMLButtonElement>("#prev")!
    .addEventListener("click", () => {
      const month = shiftMonth(state.month, -1);
      go({ ...state, month, date: `${month}-01` });
    });
  document
    .querySelector<HTMLButtonElement>("#next")!
    .addEventListener("click", () => {
      const month = shiftMonth(state.month, 1);
      go({ ...state, month, date: `${month}-01` });
    });
  document
    .querySelector<HTMLButtonElement>("#today")!
    .addEventListener("click", () => {
      const today = new Intl.DateTimeFormat("en-CA", {
        timeZone: "Asia/Ho_Chi_Minh",
      }).format(new Date());
      go({
        ...state,
        month: today.slice(0, 7),
        date: today,
        dateFrom: "",
        dateTo: "",
      });
    });
  const dayButtons = [
    ...document.querySelectorAll<HTMLButtonElement>(".calendar [data-date]"),
  ];
  document
    .querySelectorAll<HTMLButtonElement>("[data-date]")
    .forEach((button) =>
      button.addEventListener("click", () =>
        go({
          ...state,
          date: button.dataset.date!,
          dateFrom: "",
          dateTo: "",
          eventId: "",
        }),
      ),
    );
  dayButtons.forEach((button, index) =>
    button.addEventListener("keydown", (event) => {
      let target = index;
      if (event.key === "ArrowRight") target = index + 1;
      if (event.key === "ArrowLeft") target = index - 1;
      if (event.key === "ArrowDown") target = index + 7;
      if (event.key === "ArrowUp") target = index - 7;
      if (event.key === "Home") target = index - (index % 7);
      if (event.key === "End")
        target = Math.min(dayButtons.length - 1, index + (6 - (index % 7)));
      if (event.key === "PageUp") {
        event.preventDefault();
        document.querySelector<HTMLButtonElement>("#prev")!.click();
        return;
      }
      if (event.key === "PageDown") {
        event.preventDefault();
        document.querySelector<HTMLButtonElement>("#next")!.click();
        return;
      }
      if (target !== index && dayButtons[target]) {
        event.preventDefault();
        dayButtons[target].focus();
      }
    }),
  );
  bindCards();
  bindFilters();
  const more = document.querySelector<HTMLButtonElement>("#load-more");
  more?.addEventListener("click", () =>
    mutate(more, () =>
      calendar(id, signal, page.offset + page.limit, visibleEvents),
    ),
  );
}

async function review(
  id: number,
  signal: AbortSignal,
  offset = 0,
  accumulated: EventItem[] = [],
) {
  const params = new URLSearchParams({
    status: "needs_review",
    limit: "50",
    offset: String(offset),
  });
  addFilters(params);
  const page = await api.events(params, signal);
  if (!isCurrent(id)) return;
  const events = [...accumulated, ...page.items];
  shell(
    `<section class="title"><p class="eyebrow">HUMAN IN THE LOOP</p><h2>レビュー待ち <span class="count">${page.total}</span></h2><p>AI提案は自動公開されません。根拠と出典を確認して判断してください。</p></section>${filterForm()}${eventCards(events)}${page.has_more ? '<button id="load-more" class="load-more">さらに読み込む</button>' : ""}`,
  );
  bindCards();
  bindFilters();
  const more = document.querySelector<HTMLButtonElement>("#load-more");
  more?.addEventListener("click", () =>
    mutate(more, () => review(id, signal, page.offset + page.limit, events)),
  );
}

async function detail(id: number, signal: AbortSignal, eventId: string) {
  const [event, candidates, examples] = await Promise.all([
    api.event(eventId, signal),
    api.candidates(eventId, signal),
    api.similarExamples(eventId, signal),
  ]);
  if (!isCurrent(id)) return;
  shell(
    `<button id="back" class="quiet">← 一覧へ</button><article class="detail"><div>${badge(event)}</div><p class="eyebrow">${escapeHtml(event.event_date)} ・ ${escapeHtml(event.category)}</p><h2>${escapeHtml(event.title_ja)}</h2><p class="lead">${escapeHtml(event.summary_ja)}</p><p>最終更新: ${event.updated_at ? escapeHtml(formatICT(event.updated_at)) : "不明"}</p><dl><dt>関連性</dt><dd>${escapeHtml(event.relevance_reason)}</dd><dt>重要度の根拠</dt><dd>${escapeHtml(event.importance_reason)}</dd><dt>確度</dt><dd>${escapeHtml(event.certainty)} / 日付: ${escapeHtml(event.date_certainty)}</dd>${event.must_include ? `<dt>必須掲載の根拠</dt><dd>${escapeHtml(event.must_include_reason)}</dd>` : ""}</dl>${proposalComparison(event, examples)}<section><h3>出典</h3>${(event.articles || []).map((article) => `<p><strong>${escapeHtml(article.publisher)}</strong>・${article.published_at ? escapeHtml(formatICT(article.published_at)) : "公開日時不明"}<br><a href="${escapeHtml(safeUrl(article.url))}" target="_blank" rel="noopener noreferrer">${escapeHtml(article.title)}（外部サイト・新しいタブ）</a>${article.is_primary_source ? " <strong>主要出典</strong>" : ""}</p>`).join("") || "<p>出典なし</p>"}</section>${editForm(event)}${reviewActions()}${candidateSection(candidates)}${mergeSplit(event)}<section><h3>変更履歴</h3><ol>${(event.revisions || []).map((revision) => `<li>v${revision.version} ${escapeHtml(revision.reason)} <time>${escapeHtml(formatICT(revision.created_at))}</time></li>`).join("")}</ol></section></article>`,
  );
  bindDetail(event, candidates);
}
function proposalComparison(event: EventItem, examples: SimilarExamples) {
  const proposal = event.ai_proposal,
    values = proposal?.values || {};
  const proposalHtml = proposal
    ? `<p>${escapeHtml(proposal.provider)} / ${escapeHtml(proposal.model)}・${proposal.finished_at ? escapeHtml(formatICT(proposal.finished_at)) : ""}</p><table><thead><tr><th>項目</th><th>AI提案</th><th>現在の人間確認値</th></tr></thead><tbody>${[
        ["関連性", "relevance", event.relevance_status],
        ["重要度", "importance_level", event.importance_level],
        ["確度", "certainty", event.certainty],
        ["カテゴリ", "category", event.category],
        ["根拠", "importance_reason", event.importance_reason],
      ]
        .map(
          ([label, key, current]) =>
            `<tr><th>${label}</th><td>${escapeHtml(values[String(key)] ?? "提案なし")}</td><td>${escapeHtml(current)}</td></tr>`,
        )
        .join(
          "",
        )}</tbody></table><details><summary>AI提案の構造化根拠</summary><pre>${escapeHtml(JSON.stringify(values, null, 2))}</pre></details>`
    : "<p>利用できるAI提案はありません。人間の判断を継続できます。</p>";
  return `<section><h3>AI提案と現在値の比較</h3>${proposalHtml}<button id="reanalyze" class="secondary">AI再分析を予約</button><h3>57件の判断基準から近い例</h3><p>dataset: ${escapeHtml(examples.dataset_version)} / ${escapeHtml(examples.dataset_sha256.slice(0, 12))}…</p><ol>${examples.matches.map((match) => `<li><strong>${escapeHtml(match.id)}・類似度 ${Math.round(match.similarity * 100)}%</strong><br>${escapeHtml(match.scenario)}<br>期待値: ${escapeHtml(match.expected_relevance)} / ${escapeHtml(match.expected_importance)} / 必須 ${match.must_include ? "はい" : "いいえ"}<br>理由: ${escapeHtml(match.reason)}</li>`).join("")}</ol></section>`;
}
function editForm(event: EventItem) {
  return `<details><summary>内容を編集</summary><form id="edit" class="stack"><label>タイトル<input name="title_ja" value="${escapeHtml(event.title_ja)}" required></label><label>要約<textarea name="summary_ja" required>${escapeHtml(event.summary_ja)}</textarea></label><label>日付<input type="date" name="event_date" value="${event.event_date}" required></label><label>日付確度<select name="date_certainty">${["confirmed", "estimated", "published_fallback"].map((value) => `<option ${event.date_certainty === value ? "selected" : ""}>${value}</option>`).join("")}</select></label><label>カテゴリ<input name="category" value="${escapeHtml(event.category)}" required></label><label>関連性<select name="relevance_status">${["target", "uncertain", "out_of_scope"].map((value) => `<option ${event.relevance_status === value ? "selected" : ""}>${value}</option>`).join("")}</select></label><label>関連性の根拠<textarea name="relevance_reason">${escapeHtml(event.relevance_reason)}</textarea></label><label>重要度<select name="importance_level"><option value="">対象外・未設定</option>${["high", "middle_high", "middle", "low"].map((value) => `<option value="${value}" ${event.importance_level === value ? "selected" : ""}>${labels[value]}</option>`).join("")}</select></label><label>重要度スコア<input type="number" min="0" max="100" name="importance_score" value="${event.importance_score ?? ""}"></label><label>重要度の根拠<textarea name="importance_reason">${escapeHtml(event.importance_reason)}</textarea></label><label>確度<select name="certainty">${["confirmed", "partially_confirmed", "planned", "speculative"].map((value) => `<option ${event.certainty === value ? "selected" : ""}>${value}</option>`).join("")}</select></label><label><input type="checkbox" name="must_include" ${event.must_include ? "checked" : ""}> 必須掲載</label><label>必須掲載の根拠<textarea name="must_include_reason">${escapeHtml(event.must_include_reason)}</textarea></label><label>変更理由<input name="reason" required></label><button>保存して再レビュー</button></form></details>`;
}
function reviewActions() {
  return `<section class="actions"><h3>レビュー判断</h3><label>判断理由<textarea id="reason" required></textarea></label><button data-decision="approve">承認</button><button data-decision="needs_changes" class="secondary">要修正</button><button data-decision="reject" class="danger">却下</button></section>`;
}
function candidateSection(candidates: Candidate[]) {
  return `<section><h3>統合候補</h3><button id="generate" class="secondary">候補を生成</button>${candidates.map((candidate) => `<div class="candidate"><span>類似度 ${Math.round(candidate.similarity_score * 100)}%・${escapeHtml(candidate.status)}</span>${candidate.status === "pending" ? `<button data-candidate="${candidate.id}" data-status="accepted">候補を確認済みにする</button><button data-candidate="${candidate.id}" data-status="dismissed" class="quiet">却下</button>` : ""}</div>`).join("") || "<p>候補はありません。</p>"}</section>`;
}
function mergeSplit(event: EventItem) {
  return `<details><summary>別の出来事へ統合</summary><form id="merge" class="stack"><label>統合元イベントID<input name="source_event_id" required></label><label>統合元version<input name="source_version" type="number" min="1" required></label><label>理由<input name="reason" required></label><button>統合する</button></form></details><details><summary>選択した出典を分割</summary><form id="split" class="stack">${(event.articles || []).map((article) => `<label><input type="checkbox" name="article_ids" value="${article.id}"> ${escapeHtml(article.title)}</label>`).join("")}<label>新しいタイトル<input name="title" required></label><label>要約<textarea name="summary" required></textarea></label><label>理由<input name="reason" required></label><button>分割する</button></form></details>`;
}
function bindDetail(event: EventItem, _candidates: Candidate[]) {
  document
    .querySelector<HTMLButtonElement>("#back")!
    .addEventListener("click", () => go({ ...state, eventId: "" }));
  const edit = document.querySelector<HTMLFormElement>("#edit")!;
  edit.addEventListener("submit", (submit) => {
    submit.preventDefault();
    const button = edit.querySelector<HTMLButtonElement>("button")!,
      data = new FormData(edit);
    void mutate(
      button,
      async () => {
        await api.patch(event.id, {
          version: event.version,
          reason: data.get("reason"),
          title_ja: data.get("title_ja"),
          summary_ja: data.get("summary_ja"),
          event_date: data.get("event_date"),
          date_certainty: data.get("date_certainty"),
          category: data.get("category"),
          relevance_status: data.get("relevance_status"),
          relevance_reason: data.get("relevance_reason"),
          importance_level: data.get("importance_level") || null,
          importance_score: optionalImportanceScore(
            data.get("importance_score"),
          ),
          importance_reason: data.get("importance_reason"),
          certainty: data.get("certainty"),
          must_include: data.get("must_include") === "on",
          must_include_reason: data.get("must_include_reason") || null,
        });
        await render();
      },
      "保存しました",
    );
  });
  const reanalyze = document.querySelector<HTMLButtonElement>("#reanalyze");
  reanalyze?.addEventListener("click", () =>
    mutate(
      reanalyze,
      async () => {
        await api.reanalyze(event.id);
      },
      "再分析を予約しました",
    ),
  );
  document
    .querySelectorAll<HTMLButtonElement>("[data-decision]")
    .forEach((button) =>
      button.addEventListener("click", () =>
        mutate(
          button,
          async () => {
            const reason =
              document.querySelector<HTMLTextAreaElement>("#reason")!.value;
            if (!reason) throw new Error("判断理由を入力してください。");
            await api.review(event.id, {
              version: event.version,
              decision: button.dataset.decision,
              reason,
            });
            go({ ...state, eventId: "" });
          },
          "判断を保存しました",
        ),
      ),
    );
  const generate = document.querySelector<HTMLButtonElement>("#generate")!;
  generate.addEventListener("click", () =>
    mutate(
      generate,
      async () => {
        await api.cluster(event.id);
      },
      "候補生成を予約しました",
    ),
  );
  document
    .querySelectorAll<HTMLButtonElement>("[data-candidate]")
    .forEach((button) =>
      button.addEventListener("click", () =>
        mutate(
          button,
          async () => {
            const reason = prompt("候補判断の理由") || "";
            if (!reason) throw new Error("理由を入力してください。");
            await api.decideCandidate(
              event.id,
              button.dataset.candidate!,
              button.dataset.status!,
              reason,
            );
            await render();
          },
          "候補判断を保存しました",
        ),
      ),
    );
  const merge = document.querySelector<HTMLFormElement>("#merge")!;
  merge.addEventListener("submit", (submit) => {
    submit.preventDefault();
    const button = merge.querySelector<HTMLButtonElement>("button")!,
      data = new FormData(merge);
    void mutate(
      button,
      async () => {
        if (!confirm("出典をこの出来事へ統合しますか？")) return;
        await api.merge(event.id, {
          source_event_id: data.get("source_event_id"),
          target_version: event.version,
          source_version: Number(data.get("source_version")),
          reason: data.get("reason"),
        });
        await render();
      },
      "統合しました",
    );
  });
  const split = document.querySelector<HTMLFormElement>("#split")!;
  split.addEventListener("submit", (submit) => {
    submit.preventDefault();
    const button = split.querySelector<HTMLButtonElement>("button")!,
      data = new FormData(split),
      articleIds = data.getAll("article_ids");
    void mutate(
      button,
      async () => {
        if (!articleIds.length)
          throw new Error("分割する出典を選択してください。");
        await api.split(event.id, {
          version: event.version,
          article_ids: articleIds,
          reason: data.get("reason"),
          event: {
            title_ja: data.get("title"),
            summary_ja: data.get("summary"),
            event_date: event.event_date,
            date_certainty: event.date_certainty,
            category: event.category,
            relevance_status: event.relevance_status,
            relevance_reason: event.relevance_reason,
            importance_level: event.importance_level,
            importance_score: event.importance_score,
            importance_reason: event.importance_reason,
            must_include: false,
            must_include_reason: null,
            certainty: event.certainty,
          },
        });
        await render();
      },
      "分割しました",
    );
  });
}

async function operations(id: number, signal: AbortSignal) {
  const [feeds, jobs, runs, providers, evaluation] = await Promise.all([
    api.feeds(signal),
    api.jobs(signal),
    api.runs(signal),
    api.providers(signal),
    api.evalImportance(signal),
  ]);
  if (!isCurrent(id)) return;
  const hidden = readHiddenJobs(),
    visibleJobs = jobs.filter((job) => !hidden.has(job.id)),
    provider = providers.providers[0];
  shell(
    `<section class="title"><p class="eyebrow">SYSTEM DESK</p><h2>取得・AI・ジョブ状況</h2></section><section><h3>AIプロバイダー</h3><div class="row"><div><strong>${escapeHtml(providers.selected)}</strong><p>${provider ? escapeHtml(provider.detail) : "状態なし"} / model: ${provider ? escapeHtml(provider.model) : "-"}</p></div><button id="provider-test" ${providers.selected === "disabled" ? "disabled" : ""}>能力試験</button></div><h3>重要度評価</h3><pre>${escapeHtml(JSON.stringify(evaluation, null, 2))}</pre><button id="eval-run">評価を再実行</button></section><section><h3>フィードを登録</h3><form id="create-feed" class="stack"><label>名称<input name="name" required></label><label>RSS URL（許可済みHTTPS）<input name="url" type="url" required></label><label>媒体<input name="publisher" required></label><label>言語<input name="language"></label><label>既定カテゴリ<input name="category"></label><label>間隔（分）<input name="interval" type="number" min="5" max="1440" value="30"></label><button type="button" id="pretest-feed" class="secondary">保存前接続試験</button><button>登録</button></form><h3>登録済みフィード</h3>${feeds.map((feed) => `<form class="row feed-form" data-feed="${feed.id}"><div><input type="hidden" name="version" value="${feed.version}"><label>名称<input name="name" value="${escapeHtml(feed.name)}"></label><label>RSS URL<input name="url" type="url" value="${escapeHtml(feed.url)}"></label><label>媒体<input name="publisher" value="${escapeHtml(feed.publisher)}"></label><label>言語<input name="language" value="${escapeHtml(feed.declared_language)}"></label><label>既定カテゴリ<input name="category" value="${escapeHtml(feed.default_category)}"></label><label>間隔（分）<input type="number" min="5" max="1440" name="interval" value="${feed.fetch_interval_minutes}"></label><label><input type="checkbox" name="enabled" ${feed.enabled ? "checked" : ""}> 有効</label><p>${escapeHtml(feed.url)}</p><small>最終成功: ${escapeHtml(feed.last_success_at ? formatICT(feed.last_success_at) : "なし")} / 連続失敗: ${feed.consecutive_failures}</small></div><div><button>設定を保存</button><button type="button" data-test-feed="${feed.id}" class="secondary">接続・形式試験</button><button type="button" data-fetch="${feed.id}">今すぐ取得</button></div></form>`).join("")}</section><section><h3>最近のジョブ</h3><button id="show-hidden" class="quiet">端末内の非表示を解除</button><div class="table-wrap"><table><thead><tr><th>種類</th><th>状態</th><th>試行</th><th>実行予定</th><th>作成日時</th><th>安全なエラー</th><th>操作</th></tr></thead><tbody>${visibleJobs.map((job) => `<tr><td>${escapeHtml(job.job_type)}</td><td>${escapeHtml(job.status)}</td><td>${job.attempts}/${job.max_attempts}</td><td>${escapeHtml(formatICT(job.run_after))}</td><td>${escapeHtml(formatICT(job.created_at))}</td><td>${escapeHtml(job.last_error_code || "なし")}</td><td>${job.status === "dead" ? `<button data-retry="${job.id}">再試行</button>` : ""}<button data-hide-job="${job.id}" class="quiet">この端末で非表示</button></td></tr>`).join("")}</tbody></table></div></section><section><h3>最近のRSS取得</h3><div class="table-wrap"><table><thead><tr><th>状態</th><th>HTTP</th><th>取得</th><th>追加</th><th>開始・終了</th><th>安全なエラー</th></tr></thead><tbody>${runs.map((run) => `<tr><td>${escapeHtml(run.status)}</td><td>${escapeHtml(run.http_status)}</td><td>${run.fetched_count}</td><td>${run.inserted_count}</td><td>${escapeHtml(formatICT(run.started_at))}<br>${run.finished_at ? escapeHtml(formatICT(run.finished_at)) : "未終了"}</td><td>${escapeHtml(run.error_code || "なし")}</td></tr>`).join("")}</tbody></table></div></section>`,
  );
  const createFeed = document.querySelector<HTMLFormElement>("#create-feed")!;
  const createInterval = createFeed.querySelector<HTMLInputElement>('[name="interval"]')!;
  createInterval.closest("label")!.insertAdjacentHTML("beforebegin",'<label>信頼度（0〜100）<input name="trust" type="number" min="0" max="100"></label>');
  document.querySelectorAll<HTMLFormElement>(".feed-form").forEach((form) => {
    const feed = feeds.find((item) => item.id === form.dataset.feed)!;
    const interval = form.querySelector<HTMLInputElement>('[name="interval"]')!;
    interval.closest("label")!.insertAdjacentHTML("beforebegin",'<label>信頼度（0〜100）<input name="trust" type="number" min="0" max="100"></label>');
    form.querySelector<HTMLInputElement>('[name="trust"]')!.value = feed.trust_score === null ? "" : String(feed.trust_score);
  });
  createFeed.addEventListener("submit", (event) => {
    event.preventDefault();
    const button = createFeed.querySelector<HTMLButtonElement>(
        'button:not([type="button"])',
      )!,
      data = new FormData(createFeed);
    void mutate(
      button,
      async () => {
        await api.createFeed({
          name: data.get("name"),
          url: data.get("url"),
          publisher: data.get("publisher"),
          declared_language: data.get("language") || null,
          default_category: data.get("category") || null,
          trust_score: optionalImportanceScore(data.get("trust")),
          fetch_interval_minutes: Number(data.get("interval")),
          enabled: true,
        });
        await render();
      },
      "フィードを登録しました",
    );
  });
  const pretest = document.querySelector<HTMLButtonElement>("#pretest-feed")!;
  pretest.addEventListener("click", () =>
    mutate(
      pretest,
      async () => {
        const url = String(new FormData(createFeed).get("url") || "");
        if (!url) throw new Error("RSS URLを入力してください。");
        await api.testFeedUrl(url);
      },
      "HTTP 200・RSS形式・受理可能記事を確認しました",
    ),
  );
  document.querySelectorAll<HTMLFormElement>(".feed-form").forEach((form) =>
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      const button = form.querySelector<HTMLButtonElement>("button")!,
        data = new FormData(form);
      void mutate(
        button,
        async () => {
          await api.patchFeed(form.dataset.feed!, {
            version: Number(data.get("version")),
            name: data.get("name"),
            url: data.get("url"),
            publisher: data.get("publisher"),
            declared_language: data.get("language") || null,
            default_category: data.get("category") || null,
            trust_score: optionalImportanceScore(data.get("trust")),
            fetch_interval_minutes: Number(data.get("interval")),
            enabled: data.get("enabled") === "on",
          });
          await render();
        },
        "フィード設定を保存しました",
      );
    }),
  );
  document
    .querySelectorAll<HTMLButtonElement>("[data-fetch]")
    .forEach((button) =>
      button.addEventListener("click", () =>
        mutate(
          button,
          async () => {
            await api.fetchFeed(button.dataset.fetch!);
          },
          "取得ジョブを予約しました",
        ),
      ),
    );
  document
    .querySelectorAll<HTMLButtonElement>("[data-test-feed]")
    .forEach((button) =>
      button.addEventListener("click", () =>
        mutate(
          button,
          async () => {
            await api.testFeed(button.dataset.testFeed!);
          },
          "HTTP 200・RSS形式・受理可能記事を確認しました",
        ),
      ),
    );
  document
    .querySelectorAll<HTMLButtonElement>("[data-retry]")
    .forEach((button) =>
      button.addEventListener("click", () =>
        mutate(
          button,
          async () => {
            await api.retryJob(button.dataset.retry!);
            await render();
          },
          "再試行を予約しました",
        ),
      ),
    );
  document
    .querySelectorAll<HTMLButtonElement>("[data-hide-job]")
    .forEach((button) =>
      button.addEventListener("click", () => {
        hidden.add(button.dataset.hideJob!);
        localStorage.setItem("hiddenJobs", JSON.stringify([...hidden]));
        void render();
      }),
    );
  document
    .querySelector<HTMLButtonElement>("#show-hidden")!
    .addEventListener("click", () => {
      localStorage.removeItem("hiddenJobs");
      void render();
    });
  const providerTest =
    document.querySelector<HTMLButtonElement>("#provider-test")!;
  providerTest.addEventListener("click", () =>
    mutate(
      providerTest,
      async () => {
        await api.testProvider(providers.selected);
      },
      "能力試験に成功しました",
    ),
  );
  const evalRun = document.querySelector<HTMLButtonElement>("#eval-run")!;
  evalRun.addEventListener("click", () =>
    mutate(
      evalRun,
      async () => {
        await api.runEval();
      },
      "評価を予約しました",
    ),
  );
}

export async function render() {
  const id = ++generation;
  controller?.abort();
  controller = new AbortController();
  const signal = controller.signal;
  try {
    if (!authenticated) {
      await api.me(signal);
      authenticated = true;
    }
    if (!isCurrent(id)) return;
    state = readState();
    writeState(state, true);
    if (state.eventId) await detail(id, signal, state.eventId);
    else if (state.view === "calendar") await calendar(id, signal);
    else if (state.view === "review") await review(id, signal);
    else await operations(id, signal);
  } catch (error) {
    if (signal.aborted) return;
    if (error instanceof ApiError && error.status === 401) {
      authenticated = false;
      login();
      return;
    }
    app.innerHTML = `<main class="fatal"><h1>読み込めませんでした</h1><p>${escapeHtml(errorMessage(error))}</p><button id="retry">再試行</button></main>`;
    document
      .querySelector<HTMLButtonElement>("#retry")!
      .addEventListener("click", () => void render());
  }
}
addEventListener("popstate", () => {
  state = readState();
  void render();
});
void render();
