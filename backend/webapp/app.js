/* The Web App core: talking to the backend, the router, formatting, and the small UI pieces every screen
   uses. Screens live in progress.js, payments.js, users.js and settings.js and register themselves with
   App.page(). Plain JavaScript, no build step. */
(function () {
  "use strict";

  const tg = window.Telegram && window.Telegram.WebApp;
  const inTelegram = !!(tg && tg.initData);
  /** Back button and header colours need Telegram 6.1 or newer */
  const tgHas = (version) => !!(inTelegram && tg.isVersionAtLeast && tg.isVersionAtLeast(version));

  const App = (window.App = {
    tg, inTelegram,
    me: null,
    lang: "uz",
    tz: "Asia/Tashkent",
    today: null,
    pages: [],
    actions: {},
    forms: {},
    cache: {},
  });

  // ---- text and formatting (NFR-16: DD.MM.YYYY, Tashkent time, 1 500 000 so'm, hours and minutes)

  App.t = function (key, params) {
    const table = (window.I18N || {})[App.lang] || {};
    let text = App.lang === "en" ? key : (table[key] || key);
    if (params) text = text.replace(/\{(\w+)\}/g, (m, k) => (params[k] !== undefined && params[k] !== null ? params[k] : m));
    // English plurals: "1 workers" → "1 worker" (Uzbek and Russian texts are written to need no plural)
    if (App.lang === "en") text = text.replace(/(^|[^\d\s ])(\s*)1 (worker|phone|payment|recording|line|staff)s\b/g, "$1$21 $3");
    return text;
  };
  const t = App.t;

  App.esc = function (value) {
    return String(value === null || value === undefined ? "" : value)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  };
  const esc = App.esc;

  const NBSP = " ";
  App.num = (n) => String(Math.round(n || 0)).replace(/\B(?=(\d{3})+(?!\d))/g, NBSP);
  App.currency = () => (App.lang === "ru" ? "сум" : "so'm");
  /** 1 500 000 so'm; "—" while no rate applies (SET-10) */
  App.money = (n, unit = true) => (n === null || n === undefined ? "—" : App.num(n) + (unit ? NBSP + App.currency() : ""));
  App.signed = (n) => (n < 0 ? "−" : "+") + App.num(Math.abs(n));
  App.dur = function (minutes) {
    const total = Math.round(minutes || 0), h = Math.floor(total / 60), m = String(total % 60).padStart(2, "0");
    if (App.lang === "uz") return `${h}${NBSP}soat ${m}${NBSP}daq`;
    if (App.lang === "ru") return `${h}${NBSP}ч ${m}${NBSP}мин`;
    return `${h}h ${m}m`;
  };
  /** a calendar day "2026-11-16" → 16.11.2026 */
  App.day = (iso) => (iso ? iso.slice(0, 10).split("-").reverse().join(".") : "");
  function parts(iso) {
    const f = new Intl.DateTimeFormat("en-GB", { timeZone: App.tz, year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
    const o = {};
    for (const p of f.formatToParts(new Date(iso))) o[p.type] = p.value;
    return o;
  }
  /** a moment → 16.11.2026 09:42 in Tashkent time */
  App.moment = function (iso) {
    if (!iso) return "";
    const p = parts(iso);
    return `${p.day}.${p.month}.${p.year} ${p.hour}:${p.minute}`;
  };
  /** When UseMinute data was last updated (APP-7): "today 09:42" or a full date */
  App.synced = function (iso) {
    if (!iso) return t("UseMinute data has not been synced yet");
    const p = parts(iso);
    const day = `${p.year}-${p.month}-${p.day}`;
    const when = day === App.today ? t("today {time}", { time: `${p.hour}:${p.minute}` }) : App.moment(iso);
    return t("UseMinute data updated {when}", { when });
  };
  /** +998 90 123 45 67 */
  App.phone = function (phone) {
    const d = String(phone || "").replace(/^\+/, "");
    return d.length === 12 && d.startsWith("998") ? `+998 ${d.slice(3, 5)} ${d.slice(5, 8)} ${d.slice(8, 10)} ${d.slice(10)}` : phone || "";
  };
  App.initials =(name) => (name || "?").split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0]).join("").toUpperCase();
  App.addDays = function (iso, n) {
    const d = new Date(iso + "T00:00:00Z");
    d.setUTCDate(d.getUTCDate() + n);
    return d.toISOString().slice(0, 10);
  };
  App.uuid = () => (window.crypto && crypto.randomUUID ? crypto.randomUUID() : String(Date.now()) + Math.random().toString(16).slice(2));

  // ---- icons (stroke icons drawn inline)

  const ICONS = {
    progress: '<path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/>',
    payments: '<rect x="2.5" y="5.5" width="19" height="13" rx="2.5"/><path d="M2.5 10h19M6.5 14.5h4"/>',
    users: '<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20c.6-3.4 3.2-5.5 6.5-5.5s5.9 2.1 6.5 5.5M16 4.8a3.5 3.5 0 0 1 0 6.4M18 14.8c1.9.7 3.2 2.5 3.5 5.2"/>',
    back: '<path d="M15 6l-6 6 6 6"/>',
    next: '<path d="M9 6l6 6-6 6"/>',
    phone: '<rect x="6" y="2.5" width="12" height="19" rx="2.5"/><path d="M11 18.5h2"/>',
    copy: '<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M15 9V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v7a2 2 0 0 0 2 2h3"/>',
    call: '<path d="M5 4h4l2 5-2.5 1.5a11 11 0 0 0 5 5L15 13l5 2v4a2 2 0 0 1-2 2A16 16 0 0 1 3 6a2 2 0 0 1 2-2"/>',
    send: '<path d="M21 3L10 14M21 3l-7 18-4-7-7-4 18-7z"/>',
    info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8h.01"/>',
    search: '<circle cx="11" cy="11" r="7"/><path d="M20 20l-3.5-3.5"/>',
    gear: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/>',
    file: '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5M9 13h6M9 17h6"/>',
    useminute: '<path d="M20 12a8 8 0 0 1-14.3 4.9M4 12a8 8 0 0 1 14.3-4.9"/><path d="M19 3v4.5h-4.5M5 21v-4.5h4.5"/>',
    chat: '<path d="M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.6A8 8 0 1 1 21 12z"/>',
  };
  App.icon = (name, cls = "") => `<svg class="icon ${cls}" viewBox="0 0 24 24" aria-hidden="true">${ICONS[name] || ""}</svg>`;

  // ---- the backend

  class ApiError extends Error {
    constructor(status, detail) {
      super(detail.message || "Something went wrong");
      this.status = status;
      this.code = detail.code || "error";
      this.params = detail.params || {};
    }
  }
  App.ApiError = ApiError;

  function debugUser() {
    // Developer machine only: the backend accepts this header only with DEBUG_AUTH on a localhost address
    try {
      const fromUrl = new URLSearchParams(location.search).get("debug_user");
      if (fromUrl) sessionStorage.setItem("debug_user", fromUrl);
      return sessionStorage.getItem("debug_user");
    } catch (e) {
      return null;
    }
  }

  App.api = async function (method, path, body) {
    const headers = { Accept: "application/json" };
    if (inTelegram) headers.Authorization = "tma " + tg.initData;
    else if (debugUser()) headers["X-Debug-User-Id"] = debugUser();
    if (body !== undefined) headers["Content-Type"] = "application/json";
    let r;
    try {
      r = await fetch("/api" + path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
    } catch (e) {
      throw new ApiError(0, { code: "network", message: "Could not reach the server. Check the connection and try again." });
    }
    let data = null;
    try { data = await r.json(); } catch (e) { data = null; }
    if (!r.ok) {
      const detail = data && typeof data.detail === "object" && data.detail ? data.detail : { message: "Something went wrong. Please try again." };
      throw new ApiError(r.status, detail);
    }
    return data;
  };
  App.get = (path, query) => {
    const q = new URLSearchParams();
    for (const [k, v] of Object.entries(query || {})) if (v !== null && v !== undefined && v !== "") q.set(k, v);
    const s = q.toString();
    return App.api("GET", s ? `${path}?${s}` : path);
  };
  App.post = (path, body) => App.api("POST", path, body === undefined ? {} : body);

  /** The text of a refusal, in the user's language */
  App.errorText = (e) => (e instanceof ApiError ? t(e.message, e.params) : t("Something went wrong. Please try again."));

  // ---- small UI pieces

  App.toast = function (text) {
    const el = document.getElementById("toast");
    el.textContent = text;
    el.classList.add("on");
    clearTimeout(App._toast);
    App._toast = setTimeout(() => el.classList.remove("on"), 2800);
  };
  App.haptic = function (kind) {
    try {
      if (!tg || !tg.HapticFeedback) return;
      if (kind === "error" || kind === "success" || kind === "warning") tg.HapticFeedback.notificationOccurred(kind);
      else tg.HapticFeedback.impactOccurred("light");
    } catch (e) { /* not in Telegram */ }
  };

  App.head = (eyebrow, title, extra = "") =>
    `<div class="head-row"><div class="head"><span class="eyebrow">${esc(eyebrow)}</span><h1>${esc(title)}</h1></div>${extra}</div>`;
  App.labelRow = (left, right = "") =>
    `<div class="label-row"><span class="eyebrow">${esc(left)}</span><span class="eyebrow" style="letter-spacing:0;text-transform:none">${esc(right)}</span></div>`;
  App.empty = (text) => `<div class="empty">${esc(text)}</div>`;
  App.note = (text) => `<p class="note">${esc(text)}</p>`;
  App.info = (text, warn = false) => `<div class="info${warn ? " warn" : ""}">${App.icon("info", "sm")}<p>${esc(text)}</p></div>`;
  App.chip = (text, kind = "n") => `<span class="chip ${kind}">${esc(text)}</span>`;
  App.cell = (k, v, dot = "") => `<div><span class="k">${dot ? `<span class="dot ${dot}"></span>` : ""}${esc(k)}</span><b>${esc(v)}</b></div>`;
  App.tabs = (items, current) =>
    `<div class="tabs" role="tablist">${items.map((i) => `<a role="tab" href="${esc(i.href)}" aria-selected="${i.key === current}">${esc(i.label)}</a>`).join("")}</div>`;
  App.roleName = (role) => t({ worker: "Worker", staff: "Staff", admin: "Admin", super_admin: "Super Admin" }[role] || role);
  App.roleBadge = (role) => (role && role !== "worker" ? App.chip(App.roleName(role), "n") : "");

  /** The recording split bar: green accepted, yellow pending, red rejected (PRG-2) */
  App.bar = function (split) {
    if (!split || !split.minutes) return `<div class="bar"></div>`;
    return `<div class="bar" role="img" aria-label="${esc(t("Accepted {a}%, pending {p}%, rejected {r}%", { a: split.accepted.percent, p: split.pending.percent, r: split.rejected.percent }))}">`
      + ["accepted", "pending", "rejected"].map((s, i) => split[s].minutes ? `<i class="${["acc", "pen", "rej"][i]}" style="width:${split[s].percent}%"></i>` : "").join("")
      + `</div>`;
  };

  // ---- transfers (section 9): one row, with what this viewer may do

  const STATE = {
    awaiting: ["Awaiting", "wait"], accepted: ["Accepted", "ok"], declined: ["Declined", "bad"],
    cancelled: ["Cancelled", "n"], voided: ["Voided", "bad"],
  };
  App.stateChip = (state) => App.chip(t((STATE[state] || [state])[0]), (STATE[state] || [0, "n"])[1]);

  App.transferRow = function (tr, opts = {}) {
    const other = opts.other ? opts.other(tr) : (tr.sender && tr.sender.name);
    const amount = opts.signed ? App.signed(opts.incoming ? tr.amount : -tr.amount) : App.num(tr.amount);
    const lines = [esc(other || ""), esc(App.day(tr.paid_on))].filter(Boolean).join(" · ");
    const acts = [];
    if (tr.can_answer) {
      acts.push(`<button class="btn sm ghost" data-act="decline" data-id="${tr.id}" data-kind="${tr.kind}">${esc(t(tr.kind === "payment" ? "Decline" : "Not received"))}</button>`);
      acts.push(`<button class="btn sm" data-act="accept" data-id="${tr.id}" data-kind="${tr.kind}">${esc(t(tr.kind === "payment" ? "Accept" : "Received"))}</button>`);
    }
    if (tr.can_cancel) acts.push(`<button class="btn sm ghost" data-act="cancel" data-id="${tr.id}">${esc(t("Cancel"))}</button>`);
    if (tr.can_void) acts.push(`<button class="btn sm danger" data-act="void" data-id="${tr.id}">${esc(t("Void"))}</button>`);
    const extra = [];
    if (tr.note) extra.push(`<span class="reason">“${esc(tr.note)}”</span>`);
    if (tr.reason) extra.push(`<span class="reason">${esc(t("Reason: {reason}", { reason: tr.reason }))}</span>`);
    const balance = opts.balance && tr.balance_after !== null && tr.balance_after !== undefined
      ? `<span class="mono muted" style="font-size:11px">${esc(t("Cash after: {amount}", { amount: App.num(tr.balance_after) }))}</span>` : "";
    return `<div class="item col"><div style="display:flex;justify-content:space-between;align-items:center;gap:12px">
        <div class="main"><b>${esc(amount)}</b><span>${lines}</span>${extra.join("")}</div>
        <div class="end">${App.stateChip(tr.state)}${balance}</div></div>
        ${acts.length ? `<div class="actions">${acts.join("")}</div>` : ""}</div>`;
  };

  /** A transfer waiting for this viewer's answer, shown first (PAYW-2, PAYS-12) */
  App.awaitingCard = function (tr) {
    const payment = tr.kind === "payment";
    return `<section class="card strong" aria-label="${esc(t("Awaiting your answer"))}">
      <div class="label-row" style="margin:0"><span class="eyebrow c-pen"><span class="dot pen" style="border-radius:999px"></span>${esc(t("Awaiting your answer"))}</span>
        <span class="mono muted" style="font-size:12px">${esc(App.day(tr.paid_on))}</span></div>
      <div class="head" style="gap:4px"><span class="big" style="font-size:32px">${esc(App.num(tr.amount))} <small>${esc(App.currency())}</small></span>
        <span style="font-size:14px;color:var(--muted2)">${esc(t("From {name}", { name: tr.sender ? tr.sender.name : "" }))}${tr.note ? " · “" + esc(tr.note) + "”" : ""}</span></div>
      <p class="lead" style="font-size:14px">${esc(t("Did you receive this money?"))}</p>
      <div class="btns"><button class="btn ghost" data-act="decline" data-id="${tr.id}" data-kind="${tr.kind}">${esc(t(payment ? "Decline" : "Not received"))}</button>
        <button class="btn" data-act="accept" data-id="${tr.id}" data-kind="${tr.kind}">${esc(t(payment ? "Accept" : "Received"))}</button></div>
    </section>`;
  };

  // ---- bottom sheets (forms and confirmations)

  /** Show a sheet. `body` is HTML; `onSubmit(form)` returns true (or a value) to close it, or throws to show the error. */
  App.sheet = function ({ title, body, submit, onSubmit, cancel = t("Cancel"), danger = false, mount }) {
    return new Promise((resolve) => {
      const root = document.getElementById("sheet-root");
      root.innerHTML = `<div class="sheet-bg"><form class="sheet" role="dialog" aria-modal="true" aria-label="${esc(title)}" novalidate>
        <div class="grab"></div><h2>${esc(title)}</h2>${body || ""}<p class="error hidden" data-error></p>
        <div class="btns"><button type="button" class="btn ghost" data-close>${esc(submit === false ? t("Close") : cancel)}</button>
        ${submit === false ? "" : `<button type="submit" class="btn${danger ? " danger" : ""}">${esc(submit || t("Save"))}</button>`}</div></form></div>`;
      const bg = root.firstElementChild, form = bg.firstElementChild, err = form.querySelector("[data-error]");
      const close = (value) => { root.innerHTML = ""; document.removeEventListener("keydown", onKey); resolve(value); };
      const onKey = (e) => { if (e.key === "Escape") close(null); };
      document.addEventListener("keydown", onKey);
      bg.addEventListener("click", (e) => { if (e.target === bg) close(null); });
      form.querySelector("[data-close]").addEventListener("click", () => close(null));
      form.addEventListener("submit", async (e) => {
        e.preventDefault();
        const button = form.querySelector('[type="submit"]');
        if (!button) return;
        button.disabled = true;
        err.classList.add("hidden");
        try {
          const result = onSubmit ? await onSubmit(form) : true;
          if (result !== false) close(result === undefined ? true : result);
        } catch (ex) {
          err.textContent = App.errorText(ex);
          err.classList.remove("hidden");
          App.haptic("error");
        } finally {
          button.disabled = false;
        }
      });
      if (mount) mount(form);
      const first = form.querySelector("input:not([type=hidden]):not([type=radio]):not([type=checkbox]), textarea, select");
      if (first && !("ontouchstart" in window)) first.focus();
    });
  };
  App.confirm = (title, text, submit, danger = false) =>
    App.sheet({ title, body: text ? `<p class="lead">${esc(text)}</p>` : "", submit: submit || t("Confirm"), danger });

  // ---- transfer actions, shared by every screen that lists transfers

  async function afterChange(text) {
    if (text) App.toast(text);
    App.haptic("success");
    await App.refresh();
  }
  App.actions.accept = async (el) => {
    el.disabled = true;
    try {
      await App.post(`/transfers/${el.dataset.id}/accept`);
      await afterChange(t("Recorded: received"));
    } catch (e) { App.toast(App.errorText(e)); App.haptic("error"); await App.refresh(); }
  };
  App.actions.decline = async (el) => {
    const ok = await App.sheet({
      title: t("Answer: not received?"),
      body: `<p class="lead">${esc(t("Tell the sender why, if you want. This answer is final."))}</p>
        <label class="field"><span>${esc(t("Reason (optional)"))}</span><textarea class="input" name="reason" maxlength="500"></textarea></label>`,
      submit: t(el.dataset.kind === "payment" ? "Decline" : "Not received"), danger: true,
      onSubmit: (f) => App.post(`/transfers/${el.dataset.id}/decline`, { reason: f.reason.value }),
    });
    if (ok) await afterChange(t("Recorded: not received. The sender has been told."));
  };
  App.actions.cancel = async (el) => {
    const ok = await App.sheet({
      title: t("Cancel this transfer?"), body: `<p class="lead">${esc(t("The receiver is told. To fix an amount, cancel and send it again."))}</p>`,
      submit: t("Cancel transfer"), cancel: t("Keep"), danger: true,
      onSubmit: () => App.post(`/transfers/${el.dataset.id}/cancel`),
    });
    if (ok) await afterChange(t("Cancelled"));
  };
  App.actions.void = async (el) => {
    const ok = await App.sheet({
      title: t("Void this transfer?"),
      body: `<p class="lead">${esc(t("It stops counting in every balance and stays in history with your reason. Both people are told."))}</p>
        <label class="field"><span>${esc(t("Reason"))}</span><textarea class="input" name="reason" maxlength="500" required></textarea></label>`,
      submit: t("Void"), danger: true,
      onSubmit: (f) => {
        if (!f.reason.value.trim()) throw new ApiError(400, { message: "Write the reason for the void" });
        return App.post(`/transfers/${el.dataset.id}/void`, { reason: f.reason.value });
      },
    });
    if (ok) await afterChange(t("Voided"));
  };
  App.actions.copy = async (el) => {
    try { await navigator.clipboard.writeText(el.dataset.value); App.toast(t("Copied")); } catch (e) { App.toast(el.dataset.value); }
  };
  App.actions.go = (el) => { location.hash = el.dataset.href; };

  // ---- language (APP-8)

  App.setLang = function (lang) {
    App.lang = ["uz", "ru", "en"].includes(lang) ? lang : "uz";
    document.documentElement.lang = App.lang;
  };
  App.actions.language = async () => {
    const names = { uz: "O'zbekcha", ru: "Русский", en: "English" };
    const choice = await App.sheet({
      title: t("Language"),
      body: `<div class="list">${Object.entries(names).map(([code, name]) =>
        `<label class="item"><span class="main"><b>${esc(name)}</b></span><input type="radio" name="lang" value="${code}" ${code === App.lang ? "checked" : ""} style="width:20px;height:20px;accent-color:var(--ink)"></label>`).join("")}</div>`,
      submit: t("Save"),
      onSubmit: async (f) => {
        const lang = f.lang.value;
        if (App.me) await App.post("/me/language", { language: lang });
        return lang;
      },
    });
    if (choice) { App.setLang(choice); if (App.me) App.me.user.language = choice; await App.refresh(); }
  };

  // ---- router

  /** Register a screen. `pattern` like "payments/worker/:id"; `back(params, query)` gives the list it returns to (APP-9). */
  App.page = function (pattern, def) {
    const keys = [];
    const re = new RegExp("^" + pattern.split("/").map((p) => (p.startsWith(":") ? (keys.push(p.slice(1)), "([^/]+)") : p)).join("/") + "$");
    App.pages.push({ re, keys, ...def });
  };

  function parseHash() {
    const raw = location.hash.replace(/^#\/?/, "");
    const [path, qs] = raw.split("?");
    return { path: path.replace(/\/$/, ""), query: Object.fromEntries(new URLSearchParams(qs || "")) };
  }
  App.query = () => parseHash().query;

  App.home = () => "#/" + (App.me ? App.me.start : "progress");

  function header(page, params, query) {
    const back = page && page.back ? page.back(params, query) : null;
    const left = back
      ? `<a class="linkbtn" href="${esc(back)}">${App.icon("back", "sm")}${esc(t("Back"))}</a>`
      : (inTelegram ? `<button class="linkbtn" data-act="close">${esc(t("Close"))}</button>` : "<span></span>");
    return `<header class="top">${left}<div class="brand"><b>UseMinute</b><small>mini app</small></div>
      <button class="lang" data-act="language" aria-label="${esc(t("Language"))}">${esc(App.lang)}</button></header>`;
  }
  App.actions.close = () => tg && tg.close();

  /** Telegram links open in Telegram, others in the browser; inside Telegram a page can't open them itself */
  App.openLink = (url) => {
    if (inTelegram && /^https:\/\/t\.me\//.test(url) && tg.openTelegramLink) return tg.openTelegramLink(url);
    if (inTelegram && tg.openLink) return tg.openLink(url);
    window.open(url, "_blank", "noopener");
  };
  App.actions["open-link"] = (el) => App.openLink(el.dataset.url);

  function nav(section) {
    if (!App.me) return "";
    const names = { progress: "My Progress", payments: "Payments", users: "Users", useminute: "UseMinute" };
    return `<nav class="nav" aria-label="${esc(t("Sections"))}"><div>${App.me.sections.map((s) =>
      `<a href="#/${s}" ${s === section ? 'aria-current="page"' : ""}>${App.icon(s)}${esc(t(names[s]))}</a>`).join("")}</div></nav>`;
  }

  let renderSeq = 0;
  App.render = async function (keepScroll = false) {
    const seq = ++renderSeq;
    const { path, query } = parseHash();
    if (!path) { location.replace(App.home()); return; }
    let page = null, params = {};
    for (const p of App.pages) {
      const m = p.re.exec(path);
      if (m) { page = p; p.keys.forEach((k, i) => (params[k] = decodeURIComponent(m[i + 1]))); break; }
    }
    if (!page || (page.section && !App.me.sections.includes(page.section))) { location.replace(App.home()); return; }
    const app = document.getElementById("app");
    const scroll = keepScroll ? window.scrollY : 0;
    if (!keepScroll) app.innerHTML = header(page, params, query) + `<main id="main"><div class="loading" aria-busy="true"></div></main>` + nav(page.section);
    let html;
    try {
      html = await page.render(params, query);
    } catch (e) {
      html = `<div class="wall"><p>${esc(App.errorText(e))}</p><button class="btn ghost sm" data-act="reload">${esc(t("Try again"))}</button></div>`;
    }
    if (seq !== renderSeq) return; // the user moved on while this loaded
    app.innerHTML = header(page, params, query) + `<main id="main">${html}</main>` + nav(page.section);
    if (page.mount) page.mount(document.getElementById("main"), params, query);
    window.scrollTo(0, scroll);
    // Telegram's own Back button returns from a detail screen to its list (APP-9)
    if (tgHas("6.1")) {
      const back = page.back ? page.back(params, query) : null;
      App._back = back;
      if (back) tg.BackButton.show(); else tg.BackButton.hide();
    }
  };
  /** Load the current screen again, keeping the scroll position */
  App.refresh = () => App.render(true);
  App.actions.reload = () => App.render();

  // ---- events: one listener for every data-act button

  document.addEventListener("click", (e) => {
    const el = e.target.closest("[data-act]");
    if (!el || el.closest("#sheet-root") && !el.closest("[data-sheet-act]")) return;
    const act = App.actions[el.dataset.act];
    if (!act) return;
    e.preventDefault();
    act(el, e);
  });
  document.addEventListener("submit", (e) => {
    const form = e.target.closest("[data-form]");
    if (!form) return;
    e.preventDefault();
    const handler = App.forms[form.dataset.form];
    if (handler) handler(form, e);
  });

  // ---- start

  function applyTheme() {
    // Inside Telegram its theme decides; in a plain browser (development) the system setting does
    const dark = inTelegram && tg.colorScheme ? tg.colorScheme === "dark" : !!(window.matchMedia && matchMedia("(prefers-color-scheme: dark)").matches);
    document.documentElement.dataset.theme = dark ? "dark" : "light";
    if (tgHas("6.1")) {
      tg.setHeaderColor(dark ? "#19191B" : "#FFFFFF");
      tg.setBackgroundColor(dark ? "#0E0E0F" : "#F4F4F1");
    }
  }

  function wall(text) {
    document.getElementById("app").innerHTML = header(null) + `<main><div class="wall">${App.icon("info")}<p>${esc(text)}</p></div></main>`;
  }

  App.start = async function () {
    const tgLang = tg && tg.initDataUnsafe && tg.initDataUnsafe.user && tg.initDataUnsafe.user.language_code;
    App.setLang(tgLang && tgLang.toLowerCase().startsWith("ru") ? "ru" : "uz"); // NFR-15, until the stored choice is known
    applyTheme();
    if (tg) {
      try {
        tg.ready();
        tg.expand();
        tg.onEvent("themeChanged", applyTheme);
        if (tgHas("6.1")) tg.BackButton.onClick(() => { if (App._back) location.hash = App._back; });
      } catch (e) { /* not in Telegram */ }
    }
    if (!inTelegram && !debugUser()) {
      wall(t("Open this app from the Telegram bot")); // APP-1
      return;
    }
    try {
      App.me = await App.get("/me");
    } catch (e) {
      wall(App.errorText(e)); // ONB-11, ROLE-6
      return;
    }
    App.setLang(App.me.user.language);
    App.tz = App.me.timezone || App.tz;
    App.today = App.me.today;
    window.addEventListener("hashchange", () => App.render());
    await App.render();
  };
})();
