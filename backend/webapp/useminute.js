/* UseMinute (Admins): the sync with UseMinute on top, then every account on UseMinute with the worker who
   linked it. Accounts nobody has linked yet come first: those are the people still to connect. */
(function () {
  "use strict";
  const { t, esc } = App;

  const FILTERS = [["", "Active"], ["not_linked", "Not linked"], ["linked", "Linked"], ["off", "Switched off"]];

  function syncCard(s, counts) {
    const state = !s.configured ? App.chip(t("Not set up"), "bad") : s.running ? App.chip(t("Syncing…"), "wait")
      : s.ok ? App.chip(t("Up to date"), "ok") : App.chip(t("Failed"), "bad");
    const cell = (n, label) => `<div><b>${esc(App.num(n))}</b><span class="k">${esc(t(label))}</span></div>`;
    return `<section class="hero">
      <div class="row"><span class="k">${esc(t("UseMinute accounts"))}</span>${state}</div>
      <div class="cells three">${cell(counts.all, "Active")}${cell(counts.linked, "Linked")}${cell(counts.not_linked, "Not linked")}</div>
      <button class="btn lg" data-act="sync-now" ${!s.configured || s.running ? "disabled" : ""}>${App.icon("useminute", "sm")}${esc(t(s.running ? "Syncing…" : "Sync"))}</button>
      <span class="sub" style="white-space:normal">${esc(s.last_success_at ? t("Last synced {time}", { time: App.moment(s.last_success_at) }) : t("Never synced"))}${s.schedule.length ? " · " + esc(t("every day at {times}", { times: s.schedule.join(", ") })) : ""}</span>
    </section>
    ${s.error ? `<div class="info warn">${App.icon("info", "sm")}<p>${esc(t("Last sync failed: {error}", { error: App.errorText({ message: s.error }) }))}</p></div>` : ""}
    ${s.configured ? "" : App.info(t("The UseMinute login lives in the server configuration, not here."), true)}`;
  }

  function accountRow(a) {
    const facts = [a.model, t("{count} recordings", { count: a.uploads }), a.minutes ? App.dur(a.minutes) : "",
      a.last_upload_at ? t("last {date}", { date: App.moment(a.last_upload_at) }) : ""].filter(Boolean).join(" · ");
    const chip = !a.active ? App.chip(t("Switched off"), "n") : a.user ? App.chip(t("Linked"), "ok") : App.chip(t("Not linked"), "wait");
    const body = `<div class="main" style="min-width:0"><b class="mono ellipsis" style="font-size:13px">${esc(a.email)}</b>
        ${a.user ? `<span>${esc(t("Worker: {name}", { name: a.user.name }))}</span>` : ""}
        <span>${esc(facts)}</span></div><div class="end">${chip}</div>`;
    return a.user ? `<a class="item" href="#/users/${a.user.id}">${body}</a>` : `<div class="item">${body}</div>`;
  }

  function rows(d) {
    if (!d.rows.length) return App.empty(d.counts.all + d.counts.off ? t("Nobody matches.") : t("No accounts yet. Tap Sync to bring them from UseMinute."));
    let html = `<div class="list">${d.rows.map(accountRow).join("")}</div>`;
    if (d.total > d.rows.length) html += `<button class="btn ghost" data-act="more-accounts">${esc(t("Show more ({count} left)", { count: d.total - d.rows.length }))}</button>`;
    return html;
  }

  const load = (q, offset = 0) => App.get("/useminute", { q, filter: App.query().filter || "", offset, limit: 100 });

  App.page("useminute", {
    section: "useminute",
    async render() {
      const filter = App.query().filter || "";
      const d = await load(App.cache.accountsQ || "");
      App.cache.accounts = d;
      let html = App.head(t("04 — UseMinute"), t("Accounts")) + syncCard(d.sync, d.counts);
      html += `<label class="search">${App.icon("search", "sm")}<input type="search" data-search="accounts" value="${esc(App.cache.accountsQ || "")}"
        placeholder="${esc(t("Email or worker name"))}" aria-label="${esc(t("Search accounts"))}"></label>`;
      html += `<div class="scroll-x">${FILTERS.map(([k, label]) => {
        const n = k ? d.counts[k] : d.counts.all;
        return `<a class="pill" style="display:inline-flex;align-items:center;text-decoration:none" href="#/useminute${k ? "?filter=" + k : ""}" aria-pressed="${filter === k}">${esc(t(label))} · ${n}</a>`;
      }).join("")}</div>`;
      html += `<div id="results" style="display:flex;flex-direction:column;gap:10px">${rows(d)}</div>`;
      html += App.note(t("A worker is matched to an account when they verify its email in the bot."));
      return html;
    },
    mount(main) {
      App.mountSearch(main, "accounts", async (q) => { const d = await load(q); App.cache.accounts = d; return rows(d); });
    },
  });

  App.actions["more-accounts"] = async (el) => {
    el.disabled = true;
    try {
      const d = App.cache.accounts;
      const next = await load(App.cache.accountsQ || "", d.rows.length);
      d.rows = d.rows.concat(next.rows);
      document.getElementById("results").innerHTML = rows(d);
    } catch (e) { App.toast(App.errorText(e)); el.disabled = false; }
  };

  App.actions["sync-now"] = async (el) => {
    el.disabled = true;
    try {
      await App.post("/settings/sync");
      App.haptic("success");
      await App.refresh();
      // check back until it finishes, then show the result
      for (let i = 0; i < 100; i++) {
        await new Promise((r) => setTimeout(r, 3000));
        if (!location.hash.startsWith("#/useminute")) return;
        const s = (await App.get("/useminute", { limit: 1 })).sync;
        if (!s.running) {
          App.toast(s.ok ? t("Synced: {accounts} active accounts, {recordings} recordings read", { accounts: (s.stats || {}).accounts || 0, recordings: (s.stats || {}).recordings || 0 }) : t("The sync failed"));
          break;
        }
      }
      await App.refresh();
    } catch (e) { App.toast(App.errorText(e)); await App.refresh(); }
  };
})();
