/* Users (section 6, Admins): the Workers and Staff lists, a person's profile and what can be changed
   there. The server checks every action again (ROLE-5); buttons only appear when it says they are allowed. */
(function () {
  "use strict";
  const { t, esc } = App;

  const FILTERS = [["not_linked", "Not linked"], ["no_staff", "No staff"], ["owed", "Owed money"], ["blocked", "Blocked"]];

  function listQuery() {
    const q = App.query();
    return { tab: q.tab === "staff" ? "staff" : "workers", filter: q.filter || "" };
  }

  function workerRow(r) {
    const phone = r.phones ? t("1 phone") : t("No phone");
    const flags = [r.not_linked ? App.chip(t("Not linked"), "wait") : "", r.no_staff ? App.chip(t("No staff"), "wait") : "", r.blocked ? App.chip(t("Blocked"), "bad") : ""].join("");
    const money = r.overpaid ? t("Overpaid {amount}", { amount: App.money(r.overpaid) }) : r.remaining ? t("Owed {amount}", { amount: App.money(r.remaining) }) : "";
    const tail = [money, r.staff ? t("Staff: {name}", { name: r.staff.name }) : ""].filter(Boolean).join(" · ");
    return `<a class="item" href="#/users/${r.id}"><div style="display:flex;gap:12px;align-items:center;min-width:0;flex:1">
        <div class="avatar" aria-hidden="true">${esc(App.initials(r.name))}</div>
        <div class="main"><b>${esc(r.name)} ${App.roleBadge(r.role)}</b><span class="mono">${esc(r.phone_masked)} · ${esc(phone)}</span>
          ${tail ? `<span>${esc(tail)}</span>` : ""}</div></div>
      <div class="end">${flags}</div></a>`;
  }
  function staffRow(r) {
    return `<a class="item" href="#/users/${r.id}"><div style="display:flex;gap:12px;align-items:center;min-width:0;flex:1">
        <div class="avatar" aria-hidden="true">${esc(App.initials(r.name))}</div>
        <div class="main"><b>${esc(r.name)} ${App.roleBadge(r.role)}</b><span class="mono">${esc(r.phone_masked)}</span>
          <span>${esc(t("{workers} workers · {phones} phones", { workers: r.workers, phones: r.phones }))}</span></div></div>
      <div class="end"><span class="mono" style="font-size:12px">${esc(App.money(r.remaining))}</span>${r.blocked ? App.chip(t("Blocked"), "bad") : ""}</div></a>`;
  }
  function rows(d) {
    if (!d.rows.length) return App.empty(t("Nobody matches."));
    let html = `<div class="list">${d.rows.map(d.tab === "staff" ? staffRow : workerRow).join("")}</div>`;
    if (d.total > d.rows.length) html += `<button class="btn ghost" data-act="more-users">${esc(t("Show more ({count} left)", { count: d.total - d.rows.length }))}</button>`;
    return html;
  }
  async function load(q, offset = 0) {
    const { tab, filter } = listQuery();
    return App.get("/users", { tab, filter, q, offset, limit: 100 });
  }

  App.page("users", {
    section: "users",
    async render() {
      const { tab, filter } = listQuery();
      const q = App.cache.usersQ || "";
      const d = await load(q);
      App.cache.users = d;
      // APP-3: Settings opens from the gear at the top of Users
      let html = App.head(t("03 — Users"), t("People"), `<a class="iconbtn" href="#/settings" aria-label="${esc(t("Settings"))}">${App.icon("gear", "sm")}</a>`);
      html += App.tabs([
        { key: "workers", href: "#/users", label: t("Workers · {count}", { count: d.counts.workers }) },
        { key: "staff", href: "#/users?tab=staff", label: t("Staff · {count}", { count: d.counts.staff }) },
      ], tab);
      html += `<label class="search">${App.icon("search", "sm")}<input type="search" data-search="users" value="${esc(q)}"
        placeholder="${esc(t(tab === "staff" ? "Name or phone" : "Name, phone or email"))}" aria-label="${esc(t("Search people"))}"></label>`;
      if (tab === "workers") {
        html += `<div class="scroll-x">${FILTERS.map(([k, label]) => {
          const on = filter === k, href = on ? "#/users" : `#/users?filter=${k}`;
          return `<a class="pill" style="display:inline-flex;align-items:center;text-decoration:none" href="${href}" aria-pressed="${on}">${esc(t(label))} · ${d.filters[k]}</a>`;
        }).join("")}</div>`;
      }
      html += `<div id="results" style="display:flex;flex-direction:column;gap:10px">${rows(d)}</div>`;
      html += App.note(App.synced(d.synced_at));
      return html;
    },
    mount(main) {
      App.mountSearch(main, "users", async (q) => { const d = await load(q); App.cache.users = d; return rows(d); });
    },
  });
  App.actions["more-users"] = async (el) => {
    el.disabled = true;
    try {
      const d = App.cache.users;
      const next = await load(App.cache.usersQ || "", d.rows.length);
      d.rows = d.rows.concat(next.rows);
      document.getElementById("results").innerHTML = rows(d);
    } catch (e) { App.toast(App.errorText(e)); el.disabled = false; }
  };

  // ---- a person's profile (USR-7, USR-8)

  App.page("users/:id", {
    section: "users",
    back: () => "#/users",
    async render(p) {
      const d = await App.get(`/users/${p.id}`);
      App.cache.profile = d;
      const u = d.user, a = d.actions, s = d.summary;
      let html = App.head(App.roleName(u.role), u.name, `<div class="chips">${u.blocked ? App.chip(t("Blocked"), "bad") : ""}</div>`);
      html += `<div class="card"><dl class="kv">
        <dt>${esc(t("Phone"))}</dt><dd><a href="tel:${esc(u.phone)}" class="mono">${esc(App.phone(u.phone))}</a><br><small class="muted">${esc(t(u.phone_verified ? "Shared through Telegram" : "Typed by hand, not verified"))}</small></dd>
        <dt>${esc(t("Telegram"))}</dt><dd>${u.username ? `<a href="https://t.me/${esc(u.username)}" target="_blank" rel="noopener">@${esc(u.username)}</a>` : `<span class="muted">${esc(t("no username"))}</span>`}</dd>
        <dt>${esc(t("Registered"))}</dt><dd>${esc(App.moment(u.registered_at))}</dd>
        <dt>${esc(t("Role"))}</dt><dd>${esc(App.roleName(u.role))}</dd>
        <dt>${esc(t("UseMinute email"))}</dt><dd>${d.email ? `<span class="mono">${esc(d.email)}</span><br><small class="muted">${esc(t("linked {date}", { date: App.moment(d.linked_at) }))}</small>` : `<span class="muted">${esc(t("Not linked"))}</span>`}</dd>
        <dt>${esc(t("Staff member"))}</dt><dd>${d.staff ? esc(d.staff.name) : `<span class="c-pen">${esc(t("No staff assigned"))}</span>`}</dd>
        ${d.workers ? `<dt>${esc(t("Workers"))}</dt><dd>${d.workers}</dd>` : ""}
      </dl></div>`;
      // the money summary from section 8
      html += `<section class="hero"><div class="row"><span class="k">${esc(t(s.overpaid ? "Overpaid" : "Remaining"))}</span></div>
        <span class="big">${esc(App.num(s.overpaid || s.remaining))} <small>${esc(App.currency())}</small></span>
        <div class="cells">${App.cell(t("Earned"), App.money(s.earned, false))}${App.cell(t("Paid"), App.num(s.paid))}
          ${App.timeCell(t("Pending review"), s.minutes.pending, "pen")}${App.timeCell(t("Rejected"), s.minutes.rejected, "rej")}</div></section>`;
      if (d.email || s.paid || s.awaiting) html += `<a class="btn ghost" href="#/payments/worker/${u.id}?from=user-${u.id}">${esc(t("Phone and payment history"))}</a>`;

      const buttons = [];
      const b = (act, label, kind = "ghost") => buttons.push(`<button class="btn ${kind}" data-act="${act}">${esc(t(label))}</button>`);
      if (a.assign_staff) b("assign-staff", d.staff ? "Change staff" : "Assign to staff");
      if (a.make_staff) b("role-staff", "Make Staff");
      if (a.remove_staff) b("role-worker", "Remove Staff");
      if (a.make_admin) b("role-admin", "Make Admin");
      if (a.remove_admin) b("role-staff-from-admin", "Remove Admin");
      if (a.edit) b("edit-user", "Edit name or phone");
      if (a.unlink) b("unlink", "Unlink email", "danger");
      if (a.block) b("block", u.blocked ? "Unblock" : "Block", u.blocked ? "ghost" : "danger");
      if (buttons.length) html += App.labelRow(t("Actions")) + `<div style="display:flex;flex-direction:column;gap:8px">${buttons.join("")}</div>`;
      return html;
    },
  });

  const profile = () => App.cache.profile;
  async function done(text) { App.toast(text); App.haptic("success"); await App.refresh(); }

  // USR-9: every role change asks for confirmation; the person is told by the bot
  async function changeRole(role, title, text) {
    const u = profile().user;
    const ok = await App.sheet({
      title: t(title, { name: u.name }), body: `<p class="lead">${esc(t(text))}</p>`, submit: t("Confirm"),
      onSubmit: async () => {
        try {
          return await App.post(`/users/${u.id}/role`, { role });
        } catch (e) {
          if (e.code !== "has_workers") throw e;
          return moveThenDemote(u, e.params.count);
        }
      },
    });
    if (ok) await done(t("Role changed. {name} has been told.", { name: u.name }));
  }
  /** USR-14: refused while they have workers, unless all move to another staff member in the same step */
  async function moveThenDemote(u, count) {
    const options = (await App.get("/staff-options")).staff.filter((x) => x.id !== u.id);
    if (!options.length) throw new App.ApiError(409, { message: "There is no other staff member to move the workers to." });
    document.getElementById("sheet-root").innerHTML = "";
    return App.sheet({
      title: t("Move {count} workers first", { count }),
      body: `<p class="lead">${esc(t("{name} still has workers. Choose who takes all of them, then the Staff role is removed.", { name: u.name }))}</p>
        <label class="field"><span>${esc(t("Move all workers to"))}</span><select class="input" name="to">${options.map((o) => `<option value="${o.id}">${esc(o.name)}</option>`).join("")}</select></label>`,
      submit: t("Move and remove Staff"),
      onSubmit: (f) => App.post(`/users/${u.id}/role`, { role: "worker", move_workers_to: Number(f.to.value) }),
    });
  }
  App.actions["role-staff"] = () => changeRole("staff", "Make {name} Staff?", "They can pay the workers assigned to them and receive funding.");
  App.actions["role-worker"] = () => changeRole("worker", "Remove Staff from {name}?", "They become a Worker again. Payments they made stay in history.");
  App.actions["role-admin"] = () => changeRole("admin", "Make {name} Admin?", "They can fund staff and manage people and settings.");
  App.actions["role-staff-from-admin"] = () => changeRole("staff", "Remove Admin from {name}?", "They become Staff.");

  App.actions["assign-staff"] = async () => {
    const d = profile(), u = d.user;
    const options = (await App.get("/staff-options")).staff.filter((x) => x.id !== u.id); // USR-15
    const ok = await App.sheet({
      title: t("Responsible staff member"),
      body: `<p class="lead">${esc(t("They pay {name}. Payments already made stay with whoever made them.", { name: u.name }))}</p>
        <label class="field"><span>${esc(t("Staff member"))}</span><select class="input" name="staff">
          <option value="">${esc(t("No staff member"))}</option>
          ${options.map((o) => `<option value="${o.id}" ${d.staff && d.staff.id === o.id ? "selected" : ""}>${esc(o.name)}</option>`).join("")}</select></label>`,
      onSubmit: (f) => App.post(`/users/${u.id}/staff`, { staff_id: f.staff.value ? Number(f.staff.value) : null }),
    });
    if (ok) await done(t("Saved. Both have been told."));
  };

  App.actions.block = async () => {
    const u = profile().user, blocking = !u.blocked;
    const ok = await App.sheet({
      title: t(blocking ? "Block {name}?" : "Unblock {name}?", { name: u.name }),
      body: `<p class="lead">${esc(t(blocking ? "They can no longer open the app or answer payments. Their data and history are kept." : "They can open the app again."))}</p>`,
      submit: t(blocking ? "Block" : "Unblock"), danger: blocking,
      onSubmit: () => App.post(`/users/${u.id}/block`, { blocked: blocking }),
    });
    if (ok) await done(t(blocking ? "Blocked" : "Access restored"));
  };

  App.actions["edit-user"] = async () => {
    const u = profile().user;
    const ok = await App.sheet({
      title: t("Edit name or phone"),
      body: `<label class="field"><span>${esc(t("Full name"))}</span><input class="input" name="name" value="${esc(u.name)}" maxlength="128" required></label>
        <label class="field"><span>${esc(t("Phone"))}</span><input class="input" name="phone" type="tel" value="${esc(u.phone)}" required>
        <small>${esc(t("A changed number is marked as typed by hand."))}</small></label>`,
      onSubmit: (f) => {
        const body = {};
        if (f.name.value.trim() !== u.name) body.full_name = f.name.value;
        if (f.phone.value.trim() !== u.phone) body.phone = f.phone.value;
        return Object.keys(body).length ? App.api("PATCH", `/users/${u.id}`, body) : true;
      },
    });
    if (ok) await done(t("Saved"));
  };

  App.actions.unlink = async () => {
    const d = profile();
    const ok = await App.sheet({
      title: t("Unlink {email}?", { email: d.email }),
      body: `<p class="lead">${esc(t("The worker can then link another email. What happens to the recordings already counted?"))}</p>
        <div class="list"><label class="item check"><input type="radio" name="keep" value="1" checked><span><b>${esc(t("Keep them in their earnings"))}</b><br>
          <span class="muted">${esc(t("The UseMinute account was blocked or replaced."))}</span></span></label>
        <label class="item check"><input type="radio" name="keep" value="0"><span><b>${esc(t("Remove them from their earnings"))}</b><br>
          <span class="muted">${esc(t("The email was linked to the wrong person."))}</span></span></label></div>`,
      submit: t("Unlink"), danger: true,
      onSubmit: (f) => App.post(`/users/${d.user.id}/unlink`, { keep_recordings: f.keep.value === "1" }),
    });
    if (ok) await done(t("Unlinked"));
  };
})();
