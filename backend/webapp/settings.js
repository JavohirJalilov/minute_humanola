/* Settings (section 10, Admins): invite codes, the pay rate and the change history. The UseMinute sync has its own section.
   It opens from the gear in Users (APP-3). */
(function () {
  "use strict";
  const { t, esc } = App;

  function codeRow(c) {
    return `<div class="item col"><div style="display:flex;justify-content:space-between;align-items:center;gap:12px">
        <div class="main"><b class="mono">${esc(c.code)}</b><span>${esc([c.label, c.staff ? t("Staff: {name}", { name: c.staff.name }) : ""].filter(Boolean).join(" · ") || t("No label"))}</span>
          <span>${esc(t("Used by {count} · added by {name}, {date}", { count: c.uses, name: c.created_by || "—", date: App.moment(c.created_at) }))}</span></div>
        <label class="switch" aria-label="${esc(t("Active"))}"><input type="checkbox" data-code-active="${c.id}" ${c.is_active ? "checked" : ""}><i></i></label></div>
      <div class="actions"><button class="btn sm ghost" data-act="copy" data-value="${esc(c.code)}">${App.icon("copy", "sm")}${esc(t("Copy"))}</button>
        <button class="btn sm ghost" data-act="edit-code" data-id="${c.id}">${esc(t("Edit"))}</button>
        ${c.uses ? "" : `<button class="btn sm danger" data-act="delete-code" data-id="${c.id}" data-code="${esc(c.code)}">${esc(t("Delete"))}</button>`}</div></div>`;
  }

  function rateRows(r) {
    let html = `<section class="card"><div class="label-row" style="margin:0"><h2 style="font-size:17px">${esc(t("Company rate"))}</h2>
      <button class="btn sm" data-act="add-rate">${esc(t("New rate"))}</button></div>`;
    html += r.company
      ? `<div><span class="big" style="font-size:32px">${esc(App.num(r.company.amount))}</span> <span class="muted">${esc(t("{currency} per hour", { currency: App.currency() }))}</span><br>
         <small class="muted">${esc(t("since {date}", { date: App.day(r.company.start_date) }))}</small></div>`
      : App.info(t("No rate is set yet, so money amounts show as not available."), true);
    if (r.upcoming.length) html += `<div class="list">${r.upcoming.map((x) => `<div class="item"><span class="main"><b>${esc(App.money(x.amount))}</b><span>${esc(t("from {date}", { date: App.day(x.start_date) }))}</span></span>${App.chip(t("Planned"), "wait")}</div>`).join("")}</div>`;
    if (r.history.length > 1) {
      html += `<details><summary class="muted" style="cursor:pointer;font-size:14px">${esc(t("Rate history"))}</summary><div class="list" style="margin-top:8px">${r.history.map((x) =>
        `<div class="item"><span class="main"><b>${esc(App.money(x.amount))}</b><span>${esc(t("from {date}", { date: App.day(x.start_date) }))} · ${esc(x.created_by || "")}</span></span></div>`).join("")}</div></details>`;
    }
    html += `</section>`;
    html += App.labelRow(t("Own rates"), t("Worker, then staff member, then company"));
    html += r.overrides.length ? `<div class="list">${r.overrides.map((o) => `<div class="item"><span class="main"><b>${esc(o.user.name)} ${App.chip(t(o.scope === "staff" ? "Staff" : "Worker"))}</b>
        <span>${esc(o.amount !== null ? t("{amount} per hour since {date}", { amount: App.money(o.amount), date: App.day(o.start_date) }) : t("No own rate now"))}${o.upcoming.length ? " · " + esc(o.upcoming.map((x) => t("{amount} from {date}", { amount: x.amount === null ? t("ends") : App.money(x.amount), date: App.day(x.start_date) })).join(", ")) : ""}</span></span>
        <button class="btn sm ghost" data-act="add-rate" data-scope="${o.scope}" data-user="${o.user.id}" data-name="${esc(o.user.name)}">${esc(t("Change"))}</button></div>`).join("")}</div>`
      : App.empty(t("Nobody has their own rate. Everyone gets the company rate."));
    return html;
  }

  const ACTIONS = {
    "settings.invite_code.created": "Invite code added", "settings.invite_code.changed": "Invite code changed",
    "settings.invite_code.activated": "Invite code switched on", "settings.invite_code.deactivated": "Invite code switched off",
    "settings.invite_code.deleted": "Invite code deleted", "settings.rate.added": "Rate set",
  };
  function historyRow(h) {
    const d = h.details || {};
    let what = d.code || "";
    if (h.action === "settings.rate.added") what = [d.amount === null ? t("own rate ended") : App.money(d.amount), t("from {date}", { date: App.day(d.start_date) }), h.about ? h.about.name : t("company")].join(" · ");
    return `<div class="item"><span class="main"><b>${esc(t(ACTIONS[h.action] || h.action))}</b><span>${esc(what)}</span>
      <span>${esc(App.moment(h.at))} · ${esc(h.by ? h.by.name : t("System"))}</span></span></div>`;
  }

  App.page("settings", {
    section: "users",
    back: () => "#/users",
    async render() {
      const d = await App.get("/settings");
      App.cache.settings = d;
      let html = App.head(t("03 — Users"), t("Settings"));
      html += App.labelRow(t("Invite codes"), String(d.invite_codes.length));
      html += `<button class="btn ghost" data-act="add-code">${esc(t("Add invite code"))}</button>`;
      html += d.invite_codes.length ? `<div class="list">${d.invite_codes.map(codeRow).join("")}</div>` : App.empty(t("No invite codes yet. Workers need one to link their phone."));
      html += App.labelRow(t("Pay rate")) + rateRows(d.rates);
      html += App.labelRow(t("Change history")); // SET-16
      html += d.history.length ? `<div class="list">${d.history.map(historyRow).join("")}</div>` : App.empty(t("No changes yet."));
      return html;
    },
    mount(main) {
      main.querySelectorAll("[data-code-active]").forEach((box) => box.addEventListener("change", async () => {
        box.disabled = true;
        try {
          await App.api("PATCH", `/settings/invite-codes/${box.dataset.codeActive}`, { is_active: box.checked });
          App.toast(t(box.checked ? "Switched on" : "Switched off. Workers who linked with it stay linked."));
          await App.refresh();
        } catch (e) { box.checked = !box.checked; box.disabled = false; App.toast(App.errorText(e)); }
      }));
    },
  });

  async function staffOptions(selected) {
    const staff = (await App.get("/staff-options")).staff;
    return `<option value="">${esc(t("Nobody"))}</option>` + staff.map((s) => `<option value="${s.id}" ${s.id === selected ? "selected" : ""}>${esc(s.name)}</option>`).join("");
  }

  App.actions["add-code"] = async () => {
    const options = await staffOptions(null);
    const made = await App.sheet({
      title: t("Add invite code"),
      body: `<label class="field"><span>${esc(t("Code"))}</span><input class="input code" name="code" maxlength="8" autocomplete="off" autocapitalize="characters" spellcheck="false" placeholder="${esc(t("Empty: make one up for me"))}">
          <small>${esc(t("8 Latin letters and digits."))}</small></label>
        <label class="field"><span>${esc(t("Label"))}</span><input class="input" name="label" maxlength="100" placeholder="${esc(t("For example: a team or a place"))}"></label>
        <label class="field"><span>${esc(t("Belongs to staff member"))}</span><select class="input" name="staff">${options}</select>
          <small>${esc(t("Workers who link with it are assigned to this staff member."))}</small></label>`,
      submit: t("Add"),
      onSubmit: (f) => App.post("/settings/invite-codes", { code: f.code.value.trim() || null, label: f.label.value, staff_id: f.staff.value ? Number(f.staff.value) : null }),
    });
    if (made) { App.toast(t("Code {code} added", { code: made.code })); await App.refresh(); }
  };

  App.actions["edit-code"] = async (el) => {
    const c = App.cache.settings.invite_codes.find((x) => x.id === Number(el.dataset.id));
    const options = await staffOptions(c.staff ? c.staff.id : null);
    const ok = await App.sheet({
      title: t("Code {code}", { code: c.code }),
      body: `<label class="field"><span>${esc(t("Label"))}</span><input class="input" name="label" maxlength="100" value="${esc(c.label)}"></label>
        <label class="field"><span>${esc(t("Belongs to staff member"))}</span><select class="input" name="staff">${options}</select></label>`,
      onSubmit: (f) => App.api("PATCH", `/settings/invite-codes/${c.id}`, { label: f.label.value, staff_id: f.staff.value ? Number(f.staff.value) : null }),
    });
    if (ok) { App.toast(t("Saved")); await App.refresh(); }
  };

  App.actions["delete-code"] = async (el) => {
    const ok = await App.sheet({
      title: t("Delete code {code}?", { code: el.dataset.code }), body: `<p class="lead">${esc(t("Nobody has used it, so it can be deleted."))}</p>`,
      submit: t("Delete"), danger: true, onSubmit: () => App.api("DELETE", `/settings/invite-codes/${el.dataset.id}`),
    });
    if (ok) { App.toast(t("Deleted")); await App.refresh(); }
  };

  // ---- a new rate (SET-7..11)

  App.actions["add-rate"] = async (el) => {
    const fixed = el.dataset.scope ? { scope: el.dataset.scope, user: Number(el.dataset.user), name: el.dataset.name } : null;
    const staff = (await App.get("/staff-options")).staff;
    const who = fixed ? `<input type="hidden" name="scope" value="${fixed.scope}"><input type="hidden" name="user" value="${fixed.user}">
        <p class="lead">${esc(t("Own rate of {name}", { name: fixed.name }))}</p>`
      : `<div class="field"><span>${esc(t("For"))}</span>
          <input type="hidden" name="scope" value="company">
          <div class="tabs" role="radiogroup" aria-label="${esc(t("For"))}">
            ${[["company", "Everyone"], ["staff", "Staff member"], ["worker", "Worker"]].map(([k, label]) =>
              `<button type="button" role="radio" data-scope="${k}" aria-selected="${k === "company"}" aria-checked="${k === "company"}">${esc(t(label))}</button>`).join("")}
          </div>
          <small class="muted" data-scope-hint>${esc(t("Everyone (company rate)"))}</small></div>
        <div class="field hidden" data-for="staff"><span>${esc(t("Staff member"))}</span>
          <input type="hidden" name="staff_user">
          ${staff.length ? `<div class="list picklist" role="radiogroup" aria-label="${esc(t("Staff member"))}">${staff.map((p) =>
            `<button type="button" class="item" role="radio" aria-checked="false" data-pick-staff="${p.id}">
              <span class="avatar" aria-hidden="true">${esc(App.initials(p.name))}</span><span class="main"><b>${esc(p.name)}</b></span><span class="tick" aria-hidden="true">✓</span></button>`).join("")}</div>`
            : App.empty(t("No staff members yet. Make someone Staff in Users first."))}</div>
        <div class="field hidden" data-for="worker"><span>${esc(t("Worker"))}</span>
          <input type="hidden" name="worker_user">
          <div class="picked hidden" data-picked></div>
          <label class="search" data-worker-search>${App.icon("search", "sm")}<input type="search" name="worker_q" placeholder="${esc(t("Start typing the worker's name"))}" autocomplete="off" aria-label="${esc(t("Search workers"))}"></label>
          <div class="list picklist hidden" data-picks></div></div>`;
    const result = await App.sheet({
      title: t("New pay rate"),
      body: who + `<label class="field"><span>${esc(t("So'm per hour of accepted recording"))}</span><input class="input" name="amount" inputmode="numeric" autocomplete="off"></label>
        <label class="check hidden" data-end><input type="checkbox" name="end"><span>${esc(t("End their own rate: the wider rate applies again"))}</span></label>
        ${App.dateField({ name: "start", label: t("Starts on"), value: App.today,
          quick: [["Today", App.today], ["Tomorrow", App.addDays(App.today, 1)], ["1st of next month", App.addDays(App.today.slice(0, 8) + "28", 4).slice(0, 8) + "01"]] })}
        <small class="muted" data-rate-when>${esc(t("Earlier days keep the rate that was in force on them."))}</small>`,
      mount: (f) => mountRateForm(f, !!fixed),
      onSubmit: (f) => {
        const scope = f.scope.value;
        const user = fixed ? fixed.user : scope === "staff" ? Number(f.staff_user.value) : scope === "worker" ? Number(f.worker_user.value) : null;
        if (scope !== "company" && !user) throw new App.ApiError(400, { message: "Choose who the rate is for" });
        const ending = scope !== "company" && f.end.checked;
        const raw = f.amount.value.replace(/[\s ]/g, "");
        if (!ending && (!/^\d+$/.test(raw) || Number(raw) < 1 || Number(raw) > 1e7))
          throw new App.ApiError(400, { message: "The rate must be a whole number between 1 and 10 000 000 so'm per hour" });
        return App.post("/settings/rates", { scope, user_id: user, amount: ending ? null : Number(raw), start_date: App.readDate(f, "start", t("Starts on")) });
      },
    });
    if (result) { App.toast(t("Saved. Workers told: {count}.", { count: result.affected })); await App.refresh(); }
  };

  /** The typed letters in bold inside a suggested name */
  function highlight(name, q) {
    const i = name.toLowerCase().indexOf(q.toLowerCase());
    return i < 0 ? esc(name) : esc(name.slice(0, i)) + "<u>" + esc(name.slice(i, i + q.length)) + "</u>" + esc(name.slice(i + q.length));
  }

  function mountRateForm(f, fixed) {
    const endBox = f.querySelector("[data-end]");
    // a start in the past recalculates the money of those days: say so while the date is chosen
    const when = f.querySelector("[data-rate-when]");
    f.start.addEventListener("change", () => {
      const start = f.start.value, past = start && start < App.today;
      when.textContent = past ? t("Days from {date} to today will be recalculated with this rate.", { date: App.dmy(start) })
        : t("Earlier days keep the rate that was in force on them.");
      when.classList.toggle("c-pen", !!past);
    });
    const HINTS = { company: "Everyone (company rate)", staff: "A staff member's workers", worker: "One worker" };
    const sync = () => {
      const scope = f.scope.value;
      endBox.classList.toggle("hidden", scope === "company");
      f.amount.disabled = scope !== "company" && f.end.checked;
      if (fixed) return;
      f.querySelector('[data-for="staff"]').classList.toggle("hidden", scope !== "staff");
      f.querySelector('[data-for="worker"]').classList.toggle("hidden", scope !== "worker");
      f.querySelector("[data-scope-hint]").textContent = t(HINTS[scope]);
    };
    f.end.addEventListener("change", sync);
    f.amount.addEventListener("input", () => { const d = f.amount.value.replace(/\D/g, ""); f.amount.value = d ? App.num(Number(d)) : ""; });
    if (!fixed) {
      // who it is for: one tap, and the next step opens straight away
      f.querySelectorAll("[data-scope]").forEach((b) => b.addEventListener("click", () => {
        f.scope.value = b.dataset.scope;
        f.querySelectorAll("[data-scope]").forEach((x) => { const on = x === b; x.setAttribute("aria-selected", on); x.setAttribute("aria-checked", on); });
        sync();
        if (b.dataset.scope === "worker" && !f.worker_user.value) f.worker_q.focus();
      }));
      // staff members are listed as soon as "Staff member" is chosen: tap one
      f.querySelectorAll("[data-pick-staff]").forEach((b) => b.addEventListener("click", () => {
        f.staff_user.value = b.dataset.pickStaff;
        f.querySelectorAll("[data-pick-staff]").forEach((x) => x.setAttribute("aria-checked", String(x === b)));
      }));
      // workers: suggestions from the first letter typed
      const picks = f.querySelector("[data-picks]"), picked = f.querySelector("[data-picked]"), search = f.querySelector("[data-worker-search]");
      let timer, seq = 0;
      const suggest = async () => {
        const q = f.worker_q.value.trim(), mine = ++seq;
        if (!q) { picks.classList.add("hidden"); return; }
        const d = await App.get("/users", { tab: "workers", q, limit: 8 });
        if (mine !== seq) return; // an older answer arriving late
        picks.innerHTML = d.rows.length ? d.rows.map((r) => `<button type="button" class="item" data-pick="${r.id}" data-name="${esc(r.name)}" data-phone="${esc(r.phone_masked)}">
            <span class="avatar" aria-hidden="true">${esc(App.initials(r.name))}</span>
            <span class="main"><b>${highlight(r.name, q)}</b><span class="mono">${esc(r.phone_masked)}</span></span></button>`).join("")
          : App.empty(t("Nobody matches."));
        picks.classList.remove("hidden");
      };
      f.worker_q.addEventListener("input", () => { f.worker_user.value = ""; clearTimeout(timer); timer = setTimeout(suggest, 150); });
      f.worker_q.addEventListener("keydown", (e) => {
        if (e.key !== "Enter") return;
        e.preventDefault(); // Enter picks the first suggestion instead of submitting the form
        picks.querySelector("[data-pick]")?.click();
      });
      const choose = (b) => {
        f.worker_user.value = b.dataset.pick;
        picked.innerHTML = `<div class="item"><span class="avatar" aria-hidden="true">${esc(App.initials(b.dataset.name))}</span>
          <span class="main"><b>${esc(b.dataset.name)}</b><span class="mono">${esc(b.dataset.phone)}</span></span>
          <button type="button" class="btn sm ghost" data-change-worker>${esc(t("Change"))}</button></div>`;
        picked.classList.remove("hidden");
        search.classList.add("hidden");
        picks.classList.add("hidden");
      };
      picks.addEventListener("click", (e) => { const b = e.target.closest("[data-pick]"); if (b) choose(b); });
      picked.addEventListener("click", (e) => {
        if (!e.target.closest("[data-change-worker]")) return;
        f.worker_user.value = "";
        picked.classList.add("hidden");
        search.classList.remove("hidden");
        f.worker_q.value = "";
        f.worker_q.focus();
      });
    }
    sync();
  }
})();
