/* My Progress (section 7): linking a UseMinute account, then the phone with its accepted, pending and
   rejected time and money, and the phone's recordings. */
(function () {
  "use strict";
  const { t, esc } = App;

  // ---- the period filter (PRG-5): applies to the whole screen, All time by default

  App.period = { key: "all", start: null, end: null };

  function monthStart(iso) { return iso.slice(0, 8) + "01"; }
  function setPeriod(key) {
    const today = App.today;
    if (key === "all") App.period = { key, start: null, end: null };
    if (key === "month") App.period = { key, start: monthStart(today), end: today };
    if (key === "last") {
      const end = App.addDays(monthStart(today), -1);
      App.period = { key, start: monthStart(end), end };
    }
  }
  function periodLabel() {
    const p = App.period;
    if (p.key === "custom") return `${App.day(p.start)} – ${App.day(p.end)}`;
    return t({ all: "All time", month: "This month", last: "Last month" }[p.key]);
  }
  App.periodChips = function () {
    const items = [["all", "All time"], ["month", "This month"], ["last", "Last month"], ["custom", "Custom"]];
    return `<div class="scroll-x" role="group" aria-label="${esc(t("Period"))}">${items.map(([k, label]) =>
      `<button class="pill" data-act="period" data-key="${k}" aria-pressed="${App.period.key === k}">${esc(k === "custom" && App.period.key === "custom" ? periodLabel() : t(label))}</button>`).join("")}</div>`;
  };
  App.actions.period = async (el) => {
    const key = el.dataset.key;
    if (key !== "custom") { setPeriod(key); return App.refresh(); }
    const range = await App.sheet({
      title: t("Custom period"),
      body: App.dateField({ name: "start", label: t("From"), value: App.period.start || monthStart(App.today), max: App.today,
          quick: [["1st of this month", monthStart(App.today)], ["1st of last month", monthStart(App.addDays(monthStart(App.today), -1))]] })
        + App.dateField({ name: "end", label: t("To"), value: App.period.end || App.today, max: App.today,
          quick: [["Today", App.today], ["Yesterday", App.addDays(App.today, -1)]] }),
      submit: t("Show"),
      onSubmit: (f) => {
        const start = App.readDate(f, "start", t("From")), end = App.readDate(f, "end", t("To"));
        if (start > end) throw new App.ApiError(400, { message: "Choose a start date before the end date" });
        return { start, end };
      },
    });
    if (range) { App.period = { key: "custom", ...range }; App.refresh(); }
  };

  // ---- pieces shared with Payments

  /** The phone's ID is the UseMinute email it records under (PRG-1) */
  App.phoneCard = function (p, opts = {}) {
    const money = (s) => (p[s].amount === null ? "—" : App.num(p[s].amount));
    const href = opts.href || `#/progress/phone?email=${encodeURIComponent(p.email)}${opts.user ? "&user=" + opts.user : ""}`;
    return `<a class="card tap" href="${esc(href)}" style="text-decoration:none">
      <div class="phone"><div class="who"><div class="tile">${App.icon("phone", "sm")}</div>
        <div class="main" style="display:flex;flex-direction:column;gap:2px;min-width:0">
          <span class="mono ellipsis" style="font-size:13px;font-weight:500">${esc(p.email)}</span>
          <span class="muted" style="font-size:12px">${esc([p.model || t("Phone model unknown"), App.dur(p.minutes)].join(" · "))}${p.current ? "" : " · " + esc(t("earlier email"))}</span>
        </div></div>${App.icon("next", "sm")}</div>
      ${App.bar(p)}
      <div class="parts">
        <div><span class="p c-acc">${Math.round(p.accepted.percent)}% · ${esc(App.dur(p.accepted.minutes))}</span><b>${esc(money("accepted"))}</b><span class="k">${esc(t("Earned"))}</span></div>
        <div><span class="p c-pen">${Math.round(p.pending.percent)}% · ${esc(App.recCount(p.pending.recordings))}</span><b>${esc(App.dur(p.pending.minutes))}</b><span class="k">${esc(t("Pending review"))}</span></div>
        <div><span class="p c-rej">${Math.round(p.rejected.percent)}% · ${esc(App.recCount(p.rejected.recordings))}</span><b>${esc(App.dur(p.rejected.minutes))}</b><span class="k">${esc(t("Rejected"))}</span></div>
      </div></a>`;
  };

  /** Total recording time and its three parts (PRG-4) */
  function totalHero(data) {
    const s = data.total, phones = data.phones.length;
    const part = (key, label, dot) => `<div><span class="k"><span class="dot ${dot}"></span>${esc(t(label))}</span>
      <span class="t">${esc(App.dur(s[key].minutes))}</span><span class="m">${esc(key === "accepted" ? App.money(s[key].amount) : App.recCount(s[key].recordings))}</span></div>`;
    return `<section class="hero">
      <div class="row"><span class="k">${esc(t(phones === 1 ? "Total · 1 phone" : "Total · {count} phones", { count: phones }))}</span>
        <span class="sub">${esc(periodLabel())}</span></div>
      <div style="display:flex;align-items:baseline;gap:8px;flex-wrap:wrap"><span class="big" style="font-size:44px">${esc(App.dur(s.minutes))}</span>
        <span style="font-size:14px;color:var(--hero-muted)">${esc(t("recorded"))}</span></div>
      ${App.bar(s)}
      <div class="split">${part("accepted", "Accepted", "acc")}${part("pending", "Pending", "pen")}${part("rejected", "Rejected", "rej")}</div>
    </section>`;
  }

  // ---- linking (section 7.1)

  function steps(now) {
    const names = ["Email", "Code", "Linked"];
    return `<ol class="steps" aria-label="${esc(t("Steps"))}">${names.map((n, i) =>
      `<li class="${i < now ? "done" : i === now ? "now" : ""}" ${i === now ? 'aria-current="step"' : ""}>${String(i + 1).padStart(2, "0")} ${esc(t(n))}</li>`).join("")}</ol>`;
  }
  function inviteFact(code) {
    return `<div class="fact"><div><span class="k">${esc(t("Invite code"))}</span><span class="v">${esc(code)}</span></div>
      <button class="iconbtn" data-act="copy" data-value="${esc(code)}" aria-label="${esc(t("Copy invite code"))}">${App.icon("copy", "sm")}</button></div>`;
  }
  /** No invite code yet: it comes from us, so the worker gets a direct way to ask for one */
  function contactCard() {
    const link = App.me.support;
    return `<div class="card" style="gap:10px"><b style="font-size:15px">${esc(t("Don't have an invite code?"))}</b>
      <p class="lead" style="font-size:14px;margin:0">${esc(t("Contact us and we will send you one. Then come back here and enter it."))}</p>
      ${link ? `<button class="btn ghost" data-act="open-link" data-url="${esc(link)}">${App.icon("chat", "sm")}${esc(t("Contact us"))}</button>` : ""}</div>`;
  }
  const linkInfo = () => App.info(t("One worker links one UseMinute email, which is one phone. If your UseMinute account changes, ask an administrator to update it."));
  const linkHead = (text) => App.head(t("01 — My Progress"), t("Link your UseMinute account")) + `<p class="lead">${esc(text)}</p>`;

  function linkForm(s) {
    if (s.step === "invite") {
      const locked = s.locked_until && new Date(s.locked_until) > new Date();
      return linkHead(t("To see your recordings and earnings, link the UseMinute account your phone records under. It takes three short steps."))
        + `<form class="card" data-form="invite">
          <label class="field"><span>${esc(t("Invite code"))}</span>
            <input class="input code" name="code" maxlength="12" autocomplete="off" autocapitalize="characters" spellcheck="false" ${locked ? "disabled" : ""} required></label>
          ${locked ? `<p class="error">${esc(t("Too many wrong codes. Try again in {minutes} min.", { minutes: Math.max(1, Math.ceil((new Date(s.locked_until) - new Date()) / 60000)) }))}</p>`
            : s.attempts_left < 5 ? `<small class="muted">${esc(t("Attempts left: {count}", { count: s.attempts_left }))}</small>` : ""}
          <button class="btn lg" ${locked ? "disabled" : ""}>${esc(t("Continue"))}</button></form>`
        + contactCard() + linkInfo();
    }
    if (s.step === "email") {
      return linkHead(t("Connect the email your phone records under. Your recordings and earnings will appear automatically."))
        + steps(0) + inviteFact(s.invite_code)
        + `<form class="card" data-form="email">
          <label class="field"><span>${esc(t("Email of your UseMinute account"))}</span>
            <input class="input" type="email" name="email" inputmode="email" autocomplete="email" autocapitalize="off" spellcheck="false" placeholder="name@example.com" required></label>
          <small class="muted">${esc(t("We send a 6-digit code to this email to check it is yours."))}</small>
          <button class="btn lg">${esc(t("Send code"))}</button></form>` + linkInfo();
    }
    // the code step
    const wait = Math.max(0, Math.ceil((new Date(s.resend_at) - new Date()) / 1000));
    return linkHead(t("Connect the email your phone records under. Your recordings and earnings will appear automatically."))
      + steps(1)
      + `<div style="display:flex;flex-direction:column;gap:8px">${inviteFact(s.invite_code)}
        <div class="fact"><div><span class="k">${esc(t("UseMinute email"))}</span><span class="v">${esc(s.email)}</span></div>
          <button class="linkbtn" style="font-size:13px;text-decoration:underline" data-act="change-email">${esc(t("Change"))}</button></div></div>
      <form data-form="code" style="display:flex;flex-direction:column;gap:12px">
        <label for="d0" style="font-size:15px;font-weight:600">${esc(t("Enter the 6-digit code we emailed you"))}</label>
        <div class="digits">${[0, 1, 2, 3, 4, 5].map((i) =>
          `<input id="d${i}" inputmode="numeric" autocomplete="${i === 0 ? "one-time-code" : "off"}" maxlength="1" aria-label="${esc(t("Digit {n}", { n: i + 1 }))}" ${s.code_active ? "" : "disabled"}>`).join("")}</div>
        <div style="display:flex;justify-content:space-between;align-items:center;gap:8px">
          <span style="font-size:13px" class="${s.code_active ? "muted" : "c-rej"}">${esc(s.code_active ? t("Code valid for 10 minutes") : t("This code no longer works. Ask for a new one."))}</span>
          <button type="button" class="linkbtn mono" style="font-size:13px" data-act="resend" data-at="${esc(s.resend_at)}" ${wait ? "disabled" : ""}>${esc(wait ? t("Resend in {time}", { time: clock(wait) }) : t("Resend code"))}</button>
        </div>
        <button class="btn lg" ${s.code_active ? "" : "disabled"}>${esc(t("Verify and link"))} ${App.icon("next", "sm")}</button>
      </form>` + linkInfo();
  }
  function clock(seconds) { return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`; }

  function linkedView(email) {
    return App.head(t("01 — My Progress"), t("Your UseMinute account is linked")) + steps(3)
      + `<div class="card"><div class="fact" style="border:0;padding:0"><div><span class="k">${esc(t("UseMinute email"))}</span><span class="v">${esc(email)}</span></div></div>
        <p class="lead" style="font-size:14px">${esc(t("We are fetching your recordings from UseMinute now. The bot will tell you how many were found."))}</p>
        <button class="btn lg" data-act="see-phone">${esc(t("See my phone"))}</button></div>`;
  }

  async function submitLink(path, body, button) {
    if (button) button.disabled = true;
    try {
      const state = await App.post(path, body);
      App.cache.link = state;
      if (state.step === "linked") {
        App.me.linked = true;
        App.cache.justLinked = state.email;
        App.haptic("success");
      }
      await App.render(true);
    } catch (e) {
      App.haptic("error");
      // the reason stays under the field until the next try, so it can be read while correcting
      const form = button && button.closest("form");
      let box = form && form.querySelector("[data-error]");
      if (form && !box) { box = document.createElement("p"); box.className = "error"; box.dataset.error = ""; form.querySelector("button:not([type=button])").before(box); }
      if (box) box.textContent = App.errorText(e); else App.toast(App.errorText(e));
      if (button) button.disabled = false;
      if (["invite_locked", "invite_inactive", "code_cancelled", "code_expired"].includes(e.code)) { App.cache.link = null; await App.render(true); }
    }
  }
  App.forms.invite = (f) => submitLink("/link/invite", { code: f.code.value }, f.querySelector("button"));
  App.forms.email = (f) => submitLink("/link/email", { email: f.email.value }, f.querySelector("button"));
  App.forms.code = (f) => {
    const code = [...f.querySelectorAll(".digits input")].map((i) => i.value).join("");
    if (code.length !== 6) { App.toast(t("Enter all 6 digits")); return; }
    return submitLink("/link/verify", { code }, f.querySelector('button:not([type="button"])'));
  };
  App.actions.resend = (el) => submitLink("/link/resend", {}, el);
  App.actions["change-email"] = (el) => submitLink("/link/change-email", {}, el);
  App.actions["see-phone"] = () => { App.cache.justLinked = null; App.render(); };

  function mountCode(main) {
    const boxes = [...main.querySelectorAll(".digits input")];
    boxes.forEach((box, i) => {
      box.addEventListener("input", () => {
        const digits = box.value.replace(/\D/g, "");
        if (digits.length > 1) { // pasted or autofilled
          digits.slice(0, 6 - i).split("").forEach((d, k) => (boxes[i + k].value = d));
          (boxes[Math.min(i + digits.length, 5)]).focus();
        } else {
          box.value = digits;
          if (digits && boxes[i + 1]) boxes[i + 1].focus();
        }
        if (boxes.every((b) => b.value)) main.querySelector('[data-form="code"]').requestSubmit();
      });
      box.addEventListener("keydown", (e) => { if (e.key === "Backspace" && !box.value && boxes[i - 1]) boxes[i - 1].focus(); });
    });
    if (boxes[0] && !boxes[0].disabled) boxes[0].focus();
    const resend = main.querySelector('[data-act="resend"]');
    if (resend) {
      clearInterval(App._timer);
      App._timer = setInterval(() => {
        if (!resend.isConnected) return clearInterval(App._timer);
        const wait = Math.max(0, Math.ceil((new Date(resend.dataset.at) - new Date()) / 1000));
        resend.disabled = wait > 0;
        resend.textContent = wait ? t("Resend in {time}", { time: clock(wait) }) : t("Resend code");
        if (!wait) clearInterval(App._timer);
      }, 1000);
    }
  }

  // ---- the screen

  App.page("progress", {
    section: "progress",
    async render() {
      if (App.cache.justLinked) return linkedView(App.cache.justLinked);
      const data = await App.get("/progress", { start: App.period.start, end: App.period.end });
      if (!data.linked) {
        const state = App.cache.link || (await App.get("/link"));
        App.cache.link = null;
        if (state.step === "linked") return linkedView(state.email); // linked a moment ago, elsewhere
        let html = linkForm(state);
        if (data.phones.length) html += App.labelRow(t("Earlier phone")) + data.phones.map((p) => App.phoneCard(p)).join("");
        return html;
      }
      App.cache.link = null;
      let html = App.head(t("01 — My Progress"), t("Recording time")) + App.periodChips() + totalHero(data);
      html += App.labelRow(t("Your phone"), t("1 per worker")) + data.phones.map((p) => App.phoneCard(p)).join("");
      if (!data.total.recordings) html += App.info(t("No recordings were found yet. They appear here after UseMinute receives them."));
      if (data.rate_missing) html += App.info(t("Some recording time falls on days without a pay rate, so it is in no amount yet. An administrator sets the rate."), true);
      html += App.note(t("Amounts in so'm") + " · " + App.synced(data.synced_at));
      return html;
    },
    mount(main) { mountCode(main); },
  });

  // ---- one phone's recordings (PRG-6): day by day, newest first. A day opens to show its recordings,
  // with the reviewer's reasons and the videos of rejected ones right there. Every day starts closed.

  const STATUS_CHIP = { accepted: ["Accepted", "ok"], pending: ["Pending", "wait"], rejected: ["Rejected", "bad"] };
  const LOCALE = { en: "en-GB", uz: "uz-Latn-UZ", ru: "ru-RU" };

  function dayName(iso) {
    try {
      const s = new Date(iso + "T12:00:00Z").toLocaleDateString(LOCALE[App.lang] || "en-GB", { weekday: "short", day: "numeric", month: "long", timeZone: "UTC" });
      return s.charAt(0).toUpperCase() + s.slice(1);
    } catch (e) { return App.day(iso); }
  }
  function hhmm(iso) { return App.moment(iso).slice(11); }

  function dayCard(d) {
    const open = App.cache.openDays.has(d.day);
    const part = (key, cls) => d[key].recordings
      ? `<span class="${cls}">${esc(App.dur(d[key].minutes))}</span>` : "";
    const earned = d.accepted.amount === null ? "" : App.money(d.accepted.amount);
    return `<section class="card day${open ? " open" : ""}" data-day="${esc(d.day)}">
      <button class="day-head" data-act="toggle-day" data-day="${esc(d.day)}" aria-expanded="${open}">
        <span class="main"><b>${esc(dayName(d.day))}</b>
          <span>${esc(App.dur(d.minutes))} · ${esc(App.recCount(d.recordings))}${earned ? " · " + esc(earned) : ""}</span></span>
        ${d.rejected.recordings ? App.chip(t("{count} rejected", { count: d.rejected.recordings }), "bad") : ""}
        ${App.icon("next", "sm chev")}
      </button>
      ${App.bar(d)}
      <div class="day-parts">${part("accepted", "c-acc")}${part("pending", "c-pen")}${part("rejected", "c-rej")}</div>
      <div class="day-body">${open ? dayBody(d.day) : ""}</div>
    </section>`;
  }

  function dayBody(day) {
    const rows = App.cache.dayRows[day];
    if (!rows) return `<div class="loading" aria-busy="true"></div>`;
    if (!rows.length) return App.empty(t("No recordings in this period."));
    return `<div class="list">${rows.map(recordingRow).join("")}</div>`;
  }

  function feedback(r) {
    const head = [r.verdict === "fraud" ? `<b class="c-rej">${esc(t("Fraud"))}</b>` : `<b>${esc(t("Feedback"))}</b>`];
    if (r.flagged_videos != null) head.push(esc(r.videos ? t("{flagged} of {videos} videos flagged", { flagged: r.flagged_videos, videos: r.videos }) : t("Videos flagged: {count}", { count: r.flagged_videos })));
    const reasons = r.reason ? r.reason.split("\n").filter(Boolean) : [];
    return `<div class="feedback"><span>${head.join(" · ")}</span>
      ${reasons.length ? reasons.map((x) => `<span class="why">${esc(x)}</span>`).join("") : `<span class="muted">${esc(t("UseMinute gave no reason"))}</span>`}
      <div class="player" data-player="${esc(r.id)}"><button class="btn sm ghost" data-act="video" data-id="${esc(r.id)}">▶ ${esc(t("Watch video"))}</button></div></div>`;
  }

  function recordingRow(r) {
    const [label, kind] = STATUS_CHIP[r.status] || ["Pending", "wait"];
    return `<div class="item col"><div style="display:flex;justify-content:space-between;align-items:center;gap:12px">
      <div class="main"><b>${esc(hhmm(r.recorded_at))} · ${esc(App.dur(r.minutes))}</b>${r.task ? `<span>${esc(r.task)}</span>` : ""}</div>
      <div class="end">${App.chip(t(label), kind)}${r.status !== "accepted" || r.amount === null ? "" : `<span class="mono muted" style="font-size:12px">${esc(App.money(r.amount))}</span>`}</div></div>
      ${r.status === "rejected" ? feedback(r) : ""}</div>`;
  }

  function video(f, i) {
    return `<figure class="clip"><figcaption>${esc(t("Video {n}", { n: i + 1 }))} ${f.flagged ? App.chip(t("Flagged"), "bad") : ""}${f.reason ? `<span class="why">${esc(f.reason)}</span>` : ""}</figcaption>
      <video controls playsinline preload="metadata" src="${esc(f.url)}"></video>
      <button class="linkbtn muted" data-act="open-link" data-url="${esc(f.url)}">${esc(t("Doesn't play? Open it in the browser"))}</button></figure>`;
  }

  /** UseMinute's video links expire in about an hour, so a fresh one is asked for on every tap. The
      flagged videos play right here; the others of the same recording open on request. */
  App.actions.video = async (el) => {
    const box = el.closest("[data-player]");
    el.disabled = true;
    el.textContent = t("Opening…");
    try {
      const { files } = await App.get(`/recordings/${encodeURIComponent(el.dataset.id)}/video`, { user_id: App.query().user });
      const flagged = files.filter((f) => f.flagged), shown = flagged.length ? flagged : files.slice(0, 1);
      const rest = files.filter((f) => !shown.includes(f));
      box.innerHTML = shown.map(video).join("")
        + (rest.length ? `<button class="btn sm ghost" data-act="more-videos">${esc(t("Show the other {count} videos", { count: rest.length }))}</button><div class="hidden" data-rest>${rest.map((f, i) => video(f, shown.length + i)).join("")}</div>` : "");
    } catch (e) {
      App.toast(App.errorText(e));
      el.disabled = false;
      el.textContent = "▶ " + t("Watch video");
    }
  };
  App.actions["more-videos"] = (el) => { el.nextElementSibling.classList.remove("hidden"); el.remove(); };

  App.actions["toggle-day"] = async (el) => {
    const day = el.dataset.day, card = el.closest(".day"), body = card.querySelector(".day-body");
    const open = !App.cache.openDays.has(day);
    open ? App.cache.openDays.add(day) : App.cache.openDays.delete(day);
    card.classList.toggle("open", open);
    el.setAttribute("aria-expanded", open);
    if (!open) { body.innerHTML = ""; return; }
    body.innerHTML = dayBody(day);
    if (App.cache.dayRows[day]) return;
    try {
      const q = App.query();
      const data = await App.get("/recordings", { email: q.email, user_id: q.user, start: day, end: day, limit: 200 });
      App.cache.dayRows[day] = data.recordings;
      if (App.cache.openDays.has(day)) body.innerHTML = dayBody(day);
    } catch (e) { App.toast(App.errorText(e)); body.innerHTML = ""; }
  };

  App.page("progress/phone", {
    section: "progress",
    back: (p, q) => (q.user ? `#/payments/worker/${q.user}` : q.from === "money" ? "#/payments/money" : "#/progress"),
    async render(p, q) {
      const key = JSON.stringify([q.email, q.user, App.period.start, App.period.end]);
      {  // fresh on every visit, with every day closed
        const data = await App.get("/recordings/days", { email: q.email, user_id: q.user, start: App.period.start, end: App.period.end, limit: 30 });
        App.cache.days = { key, days: data.days, has_more: data.has_more, synced_at: data.synced_at };
        App.cache.openDays = new Set(); // every day starts closed: the worker opens the one they want
        App.cache.dayRows = {};
      }
      const c = App.cache.days;
      let html = App.head(t("Recordings"), t("By day")) + `<p class="mono muted ellipsis" style="font-size:13px;margin:-6px 0 0">${esc(q.email || "")}</p>` + App.periodChips();
      html += c.days.length ? c.days.map(dayCard).join("") : App.empty(t("No recordings in this period."));
      if (c.has_more) html += `<button class="btn ghost" data-act="more-days">${esc(t("Show more"))}</button>`;
      html += App.note(t("Review statuses can change after every sync.") + " " + App.synced(c.synced_at));
      return html;
    },
  });
  App.actions["more-days"] = async (el) => {
    el.disabled = true;
    const q = App.query(), c = App.cache.days;
    try {
      const data = await App.get("/recordings/days", { email: q.email, user_id: q.user, start: App.period.start, end: App.period.end, limit: 30, offset: c.days.length });
      c.days = c.days.concat(data.days);
      c.has_more = data.has_more;
      el.insertAdjacentHTML("beforebegin", data.days.map(dayCard).join(""));
      if (!c.has_more) el.remove(); else el.disabled = false;
    } catch (e) { App.toast(App.errorText(e)); el.disabled = false; }
  };
})();
