/* Payments (section 8): My money for everyone, My workers and My funds for Staff, Staff for Admins. */
(function () {
  "use strict";
  const { t, esc } = App;

  const TAB_NAMES = { money: "My money", workers: "My workers", funds: "My funds", staff: "Staff members" };
  // "Staff" is also the role name; the section is plural in Uzbek and Russian (Xodimlar, Сотрудники)
  const staffWord = () => (App.lang === "en" ? "Staff" : t("Staff members"));
  function payTabs(current) {
    const tabs = App.me.payments;
    if (tabs.length < 2) return "";
    return App.tabs(tabs.map((k) => ({ key: k, href: `#/payments/${k}`, label: k === "staff" ? staffWord() : t(TAB_NAMES[k]) })), current);
  }
  const eyebrow = () => t("02 — Payments") + (App.me.payments.includes("staff") ? " · " + t("Admin") : App.me.payments.includes("workers") ? " · " + t("Staff") : "");

  /** Someone to reach: name, phone (tap to call) and Telegram username (tap to message) */
  App.contactCard = function (person, label) {
    if (!person) return "";
    const tel = person.phone ? `<a class="iconbtn" href="tel:${esc(person.phone)}" aria-label="${esc(t("Call {name}", { name: person.name }))}">${App.icon("call", "sm")}</a>` : "";
    const tgLink = person.username ? `<a class="iconbtn" href="https://t.me/${esc(person.username)}" target="_blank" rel="noopener" aria-label="${esc(t("Message {name}", { name: person.name }))}">${App.icon("chat", "sm")}</a>` : "";
    return `<div class="fact" style="border-radius:14px"><div style="flex-direction:row;align-items:center;gap:12px">
        <div class="avatar" aria-hidden="true">${esc(App.initials(person.name))}</div>
        <div style="display:flex;flex-direction:column;gap:2px;min-width:0"><span class="k">${esc(label)}</span>
          <span style="font-size:15px;font-weight:600">${esc(person.name)}</span>
          ${person.phone ? `<span class="mono muted" style="font-size:12px">${esc(App.phone(person.phone))}</span>` : ""}</div></div>
      <div style="flex-direction:row;gap:8px">${tel}${tgLink}</div></div>`;
  };

  /** The five figures (PAYW-1), Remaining first; Overpaid instead of a negative debt (CALC-4) */
  function moneyHero(s) {
    const over = s.overpaid > 0;
    let html = `<section class="hero"><div class="row"><span class="k">${esc(t(over ? "Overpaid" : "Remaining"))}</span>
      ${s.rate !== null && s.rate !== undefined ? `<span class="sub">${esc(t("Rate: {amount} per hour", { amount: App.money(s.rate) }))}</span>` : ""}</div>
      <span class="big" style="font-size:44px">${esc(App.num(over ? s.overpaid : s.remaining))} <small>${esc(App.currency())}</small></span>
      <div class="cells">${App.cell(t("Earned"), App.money(s.earned, false))}${App.cell(t("Paid"), App.num(s.paid))}
        ${App.cell(t("Pending review"), App.money(s.pending_review, false), "pen")}${App.cell(t("Rejected"), App.money(s.rejected, false), "rej")}</div>`;
    if (s.awaiting) html += `<span class="sub" style="white-space:normal">${esc(t("Awaiting confirmation: {amount}. It counts once accepted.", { amount: App.money(s.awaiting) }))}</span>`;
    html += `</section>`;
    if (!s.available) html += App.info(t("No pay rate is set yet, so amounts are not available. Recording time is still shown."), true);
    else if (s.rate_missing) html += App.info(t("Some recording time falls on days without a pay rate, so it is in no amount yet. An administrator sets the rate."), true);
    return html;
  }

  function historyList(rows, label, opts = {}) {
    let html = App.labelRow(label, rows.length === 1 ? t("1 payment") : t("{count} payments", { count: rows.length }));
    html += rows.length ? `<div class="list">${rows.map((r) => App.transferRow(r, opts)).join("")}</div>` : App.empty(t("No payments yet."));
    return html;
  }

  // ---- My money (8.2)

  App.page("payments", { section: "payments", render() { location.replace("#/payments/" + App.me.payments[0]); return ""; } });

  async function money() {
    const d = await App.get("/payments/me");
    let html = App.head(eyebrow(), t("My money")) + payTabs("money");
    html += d.awaiting.map(App.awaitingCard).join(""); // PAYW-2: first, above the summary
    if (!d.linked && !d.phones.length) {
      // PAYW-7: a prompt to link instead of empty figures
      html += `<div class="card"><h2>${esc(t("Link your UseMinute account"))}</h2>
        <p class="lead" style="font-size:14px">${esc(t("Your earnings appear here once your phone's UseMinute account is linked in My Progress."))}</p>
        <a class="btn" href="#/progress">${esc(t("Go to My Progress"))}</a></div>`;
    } else {
      html += moneyHero(d.summary);
    }
    html += d.staff ? App.contactCard(d.staff, t("Your staff")) // PAYW-6
      : App.info(t("No staff member is assigned to you yet. An administrator will assign one."));
    if (d.phones.length) {
      html += App.labelRow(t("Your phone"));
      for (const p of d.phones) {
        html += App.phoneCard(p, { href: `#/progress/phone?email=${encodeURIComponent(p.email)}&from=money` });
        if (p.rejected.minutes) // PAYW-4
          html += `<a class="btn ghost" href="#/progress/phone?email=${encodeURIComponent(p.email)}&status=rejected&from=money">${esc(t("See rejected recordings and reasons"))}</a>`;
      }
    }
    html += historyList(d.history, t("History"));
    html += App.note(App.synced(d.synced_at));
    return html;
  }

  // ---- My workers (8.3)

  function workerChip(w) {
    if (w.blocked) return App.chip(t("Blocked"), "bad");
    if (w.awaiting) return App.chip(t("{amount} awaiting", { amount: App.num(w.awaiting) }), "wait");
    if (w.overpaid) return App.chip(t("Overpaid"), "n");
    if (w.earned && !w.remaining) return App.chip(t("Settled"), "ok");
    return "";
  }
  function workerCard(w, opts = {}) {
    const href = `#/payments/worker/${w.id}${opts.from ? "?from=" + opts.from : ""}`;
    const phones = w.phones === 1 ? t("1 phone") : w.phones ? t("{count} phones", { count: w.phones }) : t("No phone");
    return `<article class="card" style="gap:12px">
      <a href="${href}" style="display:flex;justify-content:space-between;align-items:center;gap:8px;text-decoration:none">
        <div style="display:flex;flex-direction:column;gap:2px;min-width:0"><span style="font-size:15px;font-weight:600">${esc(w.name)}</span>
          <span class="mono muted" style="font-size:12px">${esc(phones)} · ${esc(w.phone_masked)}</span></div>${workerChip(w)}</a>
      <div class="parts"><div><span class="k">${esc(t("Earned"))}</span><b>${esc(App.money(w.earned, false))}</b></div>
        <div><span class="k">${esc(t("Paid"))}</span><b>${esc(App.num(w.paid))}</b></div>
        <div><span class="k">${esc(t(w.overpaid ? "Overpaid" : "Remaining"))}</span><b>${esc(App.num(w.overpaid || w.remaining))}</b></div></div>
      ${w.pending_review ? `<span class="mono muted" style="font-size:11px">${esc(t("Pending review: {amount}", { amount: App.money(w.pending_review) }))}</span>` : ""}
      ${opts.pay && !w.blocked ? `<button class="btn" style="height:44px;font-size:14px" data-act="pay" data-id="${w.id}" data-name="${esc(w.name)}" data-payable="${w.payable}">${esc(t("Send payment"))}</button>` : ""}
    </article>`;
  }
  function workersHero(totals, title) {
    return `<section class="hero"><div class="row"><span class="k">${esc(title)}</span>
        <span class="sub">${esc(t("{workers} workers · {phones} phones", { workers: totals.workers, phones: totals.phones }))}</span></div>
      <span class="big">${esc(App.num(totals.remaining))} <small>${esc(App.currency())}</small></span>
      <div class="cells three">${App.cell(t("Cash on hand"), App.num(totals.cash_on_hand))}${App.cell(t("Pending rev."), App.num(totals.pending_review))}
        ${App.cell(t("Awaiting"), App.num(totals.awaiting))}</div></section>`;
  }

  async function workers() {
    const q = App.cache.workersQ || "";
    const d = await App.get("/staff/workers", { q });
    App.cache.available = d.totals.available;
    let html = App.head(eyebrow(), t("My workers")) + payTabs("workers") + workersHero(d.totals, t("To be paid")); // PAYS-3
    html += `<label class="search">${App.icon("search", "sm")}<input type="search" data-search="workers" value="${esc(q)}" placeholder="${esc(t("Search name or phone"))}" aria-label="${esc(t("Search workers"))}"></label>`;
    html += `<div id="results" style="display:flex;flex-direction:column;gap:10px">${workerList(d)}</div>`;
    html += App.note(App.synced(d.synced_at));
    return html;
  }
  function workerList(d) {
    if (!d.workers.length) return App.empty(d.totals.workers ? t("Nobody matches the search.") : t("No workers are assigned to you yet. An administrator assigns them."));
    return d.workers.map((w) => workerCard(w, { pay: true })).join("");
  }
  /** Search as you type, without rebuilding the screen (the keyboard stays open) */
  function mountSearch(main, kind, load) {
    const input = main.querySelector(`[data-search="${kind}"]`);
    if (!input) return;
    let timer;
    input.addEventListener("input", () => {
      clearTimeout(timer);
      timer = setTimeout(async () => {
        App.cache[kind + "Q"] = input.value;
        try { document.getElementById("results").innerHTML = await load(input.value); } catch (e) { App.toast(App.errorText(e)); }
      }, 250);
    });
  }
  App.mountSearch = mountSearch;

  // ---- sending a payment (PAYS-5..10)

  function amountField(value, hint) {
    return `<label class="field"><span>${esc(t("Amount, so'm"))}</span>
      <input class="input" name="amount" inputmode="numeric" autocomplete="off" value="${value ? esc(App.num(value)) : ""}" required>
      ${hint ? `<small>${esc(hint)}</small>` : ""}</label>`;
  }
  function dateNoteFields() {
    return App.dateField({ name: "paid_on", label: t("Date paid"), value: App.today, max: App.today,
        quick: [["Today", App.today], ["Yesterday", App.addDays(App.today, -1)]] })
      + `<label class="field"><span>${esc(t("Note (optional)"))}</span><input class="input" name="note" maxlength="500" placeholder="${esc(t("For example: cash, week 46"))}"></label>`;
  }
  function readAmount(f) {
    const raw = f.amount.value.replace(/[\s ]/g, "");
    const n = Number(raw);
    if (!/^\d+$/.test(raw) || n < 1 || n > 1e9) throw new App.ApiError(400, { message: "The amount must be a whole number between 1 and 1 000 000 000 so'm" });
    App.readDate(f, "paid_on", t("Date paid"));
    return n;
  }
  function groupDigits(form) {
    form.amount.addEventListener("input", () => {
      const digits = form.amount.value.replace(/\D/g, "").replace(/^0+/, "");
      form.amount.value = digits ? App.num(Number(digits)) : "";
    });
  }

  App.actions.pay = async (el) => {
    const payable = Number(el.dataset.payable) || 0;
    const key = App.uuid(); // the same key for every retry: a double tap makes one payment (PAYS-10)
    const available = App.cache.available;
    const ok = await App.sheet({
      title: t("Pay {name}", { name: el.dataset.name }),
      body: amountField(payable, t("Owed and not yet sent: {amount}", { amount: App.money(payable) }))
        + dateNoteFields()
        + (available !== undefined ? `<small class="muted">${esc(t("You can pay out up to {amount}.", { amount: App.money(Math.max(available, 0)) }))}</small>` : "")
        + `<div data-advance class="hidden"></div>`,
      submit: t("Send payment"),
      mount: groupDigits,
      onSubmit: async (f) => {
        const body = { worker_id: Number(el.dataset.id), amount: readAmount(f), paid_on: f.paid_on.value, note: f.note.value,
          client_key: key, confirm_advance: f.dataset.advance === "1" };
        try {
          return await App.post("/payments", body);
        } catch (e) {
          if (e.code !== "advance_confirmation") throw e;
          // PAYS-8: more than owed is allowed as an advance, after a second confirmation
          f.dataset.advance = "1";
          const box = f.querySelector("[data-advance]");
          box.className = "info warn";
          box.innerHTML = `${App.icon("info", "sm")}<p>${esc(t("This is more than the worker is owed (owed and not yet sent: {amount}). Send it as an advance?", { amount: App.money(e.params.payable) }))}</p>`;
          f.querySelector('[type="submit"]').textContent = t("Send as advance");
          App.haptic("warning");
          return false;
        }
      },
    });
    if (ok) { App.toast(t("Sent. It counts once the worker accepts it.")); App.haptic("success"); await App.refresh(); }
  };

  // ---- one worker (PAYS-2)

  App.page("payments/worker/:id", {
    section: "payments",
    back: (p, q) => (q.from && q.from.startsWith("staff-") ? `#/payments/staff/${q.from.slice(6)}` : q.from && q.from.startsWith("user-") ? `#/users/${q.from.slice(5)}` : "#/payments/workers"),
    async render(p) {
      const d = await App.get(`/staff/workers/${p.id}`);
      App.cache.available = d.available;
      let html = App.head(t("Worker"), d.user.name, d.blocked ? App.chip(t("Blocked"), "bad") : "");
      html += App.contactCard(d.user, t("Contact"));
      if (d.can_pay) html += `<button class="btn lg" data-act="pay" data-id="${d.user.id}" data-name="${esc(d.user.name)}" data-payable="${d.summary.payable}">${esc(t("Send payment"))}</button>`;
      else if (!d.staff) html += App.info(t("No staff member is assigned, so nobody can pay this worker yet."), true);
      else html += `<p class="lead" style="font-size:14px">${esc(t("Responsible staff member: {name}", { name: d.staff.name }))}</p>`;
      html += d.linked || d.phones.length ? moneyHero(d.summary) : App.info(t("No UseMinute account is linked yet."));
      for (const ph of d.phones) html += App.phoneCard(ph, { user: d.user.id });
      html += historyList(d.history, t("Payment history"));
      html += App.note(App.synced(d.synced_at));
      return html;
    },
  });

  // ---- My funds (PAYS-11..13)

  function fundsHero(pos) {
    return `<section class="hero"><div class="row"><span class="k">${esc(t("Cash on hand"))}</span>
        <span class="sub">${esc(t("Available to pay: {amount}", { amount: App.num(pos.available) }))}</span></div>
      <span class="big">${esc(App.num(pos.cash_on_hand))} <small>${esc(App.currency())}</small></span>
      <div class="cells">${App.cell(t("Funding received"), App.num(pos.received))}${App.cell(t("Paid to workers"), App.num(pos.paid_out))}
        ${App.cell(t("Funding awaiting you"), App.num(pos.funding_awaiting), "pen")}${App.cell(t("Payments awaiting workers"), App.num(pos.payments_awaiting), "pen")}</div></section>`;
  }
  function statement(funds) {
    const opts = { signed: true, balance: true, other: (x) => (x.incoming ? x.sender : x.receiver) && (x.incoming ? x.sender : x.receiver).name };
    let html = App.labelRow(t("Statement"), t("{count} lines", { count: funds.statement_total }));
    html += funds.statement.length
      ? `<div class="list">${funds.statement.map((x) => App.transferRow(x, { ...opts, incoming: x.incoming })).join("")}</div>`
      : App.empty(t("No funding or payments yet."));
    return html;
  }

  async function funds() {
    const d = await App.get("/staff/funds");
    let html = App.head(eyebrow(), t("My funds")) + payTabs("funds");
    html += d.awaiting.map(App.awaitingCard).join(""); // PAYS-12: first
    html += fundsHero(d.position) + statement(d);
    return html;
  }

  // ---- Staff (8.4, Admins)

  function staffCard(s, me) {
    const phones = t("{workers} workers · {phones} phones", { workers: s.workers, phones: s.phones });
    return `<article class="card" style="gap:12px">
      <a href="#/payments/staff/${s.id}" style="display:flex;justify-content:space-between;align-items:center;gap:8px;text-decoration:none">
        <div style="display:flex;gap:12px;align-items:center;min-width:0"><div class="avatar" aria-hidden="true">${esc(App.initials(s.name))}</div>
          <div style="display:flex;flex-direction:column;gap:2px;min-width:0"><span style="font-size:15px;font-weight:600">${esc(s.name)}</span>
            <span class="mono muted" style="font-size:12px">${esc(phones)}</span></div></div>
        <div class="chips" style="justify-content:flex-end">${s.blocked ? App.chip(t("Blocked"), "bad") : ""}${s.funding_awaiting ? App.chip(t("{amount} awaiting", { amount: App.num(s.funding_awaiting) }), "wait") : ""}</div></a>
      <div class="parts"><div><span class="k">${esc(t("To be paid"))}</span><b>${esc(App.num(s.to_be_paid))}</b></div>
        <div><span class="k">${esc(t("Cash"))}</span><b>${esc(App.num(s.cash_on_hand))}</b></div>
        <div><span class="k">${esc(t("Needs"))}</span><b class="${s.needs_funding ? "c-rej" : ""}">${esc(App.num(s.needs_funding))}</b></div></div>
      ${s.pending_review ? `<span class="mono muted" style="font-size:11px">${esc(t("Pending review: {amount}", { amount: App.money(s.pending_review) }))}</span>` : ""}
      ${s.id !== me && !s.blocked ? `<button class="btn ghost" style="height:44px;font-size:14px" data-act="fund" data-id="${s.id}" data-name="${esc(s.name)}" data-needs="${s.needs_funding}">${esc(t("Give funding"))}</button>` : ""}
    </article>`;
  }

  async function staff() {
    const [d, given] = await Promise.all([App.get("/admin/staff"), App.get("/admin/given")]);
    const tot = d.totals;
    let html = App.head(eyebrow(), t("Staff funding"),
      `<button class="iconbtn" data-act="export-staff" aria-label="${esc(t("Send the staff table to my chat as Excel"))}">${App.icon("file", "sm")}</button>`) + payTabs("staff");
    html += `<section class="hero"><div class="row"><span class="k">${esc(t("Company · {count} staff", { count: tot.staff }))}</span>
        <span class="sub">${esc(t("{workers} workers · {phones} phones", { workers: tot.workers, phones: tot.phones }))}</span></div>
      <div class="head" style="gap:6px"><span class="k">${esc(t("Needs funding"))}</span><span class="big">${esc(App.num(tot.needs_funding))} <small>${esc(App.currency())}</small></span></div>
      <div class="cells three">${App.cell(t("To be paid"), App.num(tot.to_be_paid))}${App.cell(t("Staff cash"), App.num(tot.staff_cash))}${App.cell(t("Pending rev."), App.num(tot.pending_review))}</div></section>`;
    if (d.unassigned.length) { // PAYA-7
      html += `<section class="card warn" style="gap:10px"><div class="head" style="gap:4px"><span class="eyebrow c-pen">${esc(t("Nobody can pay them"))}</span>
          <h2 style="font-size:17px">${esc(t("{count} workers with a phone but no staff member", { count: d.unassigned.length }))}</h2></div>
        <div class="list" style="border:0">${d.unassigned.map((u) => `<a class="item" href="#/users/${u.id}" style="padding:10px 0">
          <span class="main"><b>${esc(u.name)}</b><span class="mono">${esc(u.phone_masked)}</span></span>
          <span class="end">${u.remaining ? `<span class="mono" style="font-size:12px">${esc(t("Owed {amount}", { amount: App.money(u.remaining) }))}</span>` : ""}</span></a>`).join("")}</div></section>`;
    }
    html += App.labelRow(staffWord(), t("Sorted by need"));
    html += d.staff.length ? `<div style="display:flex;flex-direction:column;gap:10px">${d.staff.map((s) => staffCard(s, App.me.user.id)).join("")}</div>`
      : App.empty(t("Nobody is Staff yet. Make someone Staff in Users."));
    html += App.labelRow(t("Funding you gave")); // PAYA-6
    html += given.given.length ? `<div class="list">${given.given.map((x) => App.transferRow(x, { other: (r) => r.receiver && r.receiver.name })).join("")}</div>`
      : App.empty(t("You haven't given any funding yet."));
    html += App.note(App.synced(d.synced_at));
    return html;
  }

  App.actions.fund = async (el) => {
    const key = App.uuid();
    const needs = Number(el.dataset.needs) || 0;
    const ok = await App.sheet({
      title: t("Fund {name}", { name: el.dataset.name }),
      body: amountField(needs, t("Needs funding: {amount}", { amount: App.money(needs) })) + dateNoteFields(),
      submit: t("Give funding"),
      mount: groupDigits,
      onSubmit: (f) => App.post("/fundings", { staff_id: Number(el.dataset.id), amount: readAmount(f), paid_on: f.paid_on.value, note: f.note.value, client_key: key }),
    });
    if (ok) { App.toast(t("Sent. It counts once they confirm receiving it.")); App.haptic("success"); await App.refresh(); }
  };
  App.actions["export-staff"] = async (el) => {
    el.disabled = true;
    try { await App.post("/admin/export/staff"); App.toast(t("The Excel file is on its way to your chat with the bot.")); }
    catch (e) { App.toast(App.errorText(e)); }
    el.disabled = false;
  };
  App.actions["export-statement"] = async (el) => {
    el.disabled = true;
    try { await App.post(`/admin/export/statement/${el.dataset.id}`); App.toast(t("The Excel file is on its way to your chat with the bot.")); }
    catch (e) { App.toast(App.errorText(e)); }
    el.disabled = false;
  };

  // ---- one staff member, as they see it, read-only (PAYA-4)

  App.page("payments/staff/:id", {
    section: "payments",
    back: () => "#/payments/staff",
    async render(p) {
      const d = await App.get(`/admin/staff/${p.id}`);
      const w = d.workers, self = d.staff.id === App.me.user.id;
      let html = App.head(t("Staff member"), d.staff.name);
      html += App.contactCard(d.staff, t("Contact"));
      html += `<div class="btns">${self ? "<span></span>" : `<button class="btn" data-act="fund" data-id="${d.staff.id}" data-name="${esc(d.staff.name)}" data-needs="0">${esc(t("Give funding"))}</button>`}
        <button class="btn ghost" data-act="export-statement" data-id="${d.staff.id}">${App.icon("file", "sm")}${esc(t("Statement to chat"))}</button></div>`;
      html += workersHero(w.totals, t("To be paid"));
      html += App.labelRow(t("Their workers"), String(w.workers.length));
      html += w.workers.length ? `<div style="display:flex;flex-direction:column;gap:10px">${w.workers.map((x) => workerCard(x, { from: "staff-" + d.staff.id })).join("")}</div>`
        : App.empty(t("No workers are assigned."));
      html += App.labelRow(t("Their funds")) + fundsHero(d.funds.position) + statement(d.funds);
      return html;
    },
  });

  // ---- the tab screen

  const TABS = { money, workers, funds, staff };
  App.page("payments/:tab", {
    section: "payments",
    async render(p) {
      if (!App.me.payments.includes(p.tab)) { location.replace("#/payments/" + App.me.payments[0]); return ""; }
      return TABS[p.tab]();
    },
    mount(main, p) {
      if (p.tab === "workers") mountSearch(main, "workers", async (q) => workerList(await App.get("/staff/workers", { q })));
    },
  });
})();
