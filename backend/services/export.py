"""Excel files for Admins (PAYA-9): the staff table and a staff member's statement. The bot sends the
file to the Admin's chat."""

import io

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font
from openpyxl.utils import get_column_letter
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import Settings
from backend.models import ACCEPTED, AWAITING, CANCELLED, DECLINED, VOIDED, User
from backend.services import formats, overview, transfers
from backend.texts import t

STATE_NAMES = {AWAITING: "awaiting confirmation", ACCEPTED: "accepted", DECLINED: "declined", CANCELLED: "cancelled", VOIDED: "voided"}


def _finish(wb: Workbook, widths: list[int]) -> bytes:
    ws = wb.active
    for cell in ws[1]:
        cell.font = Font(bold=True)
        cell.alignment = Alignment(wrap_text=True, vertical="top")
    for i, width in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = width
    ws.freeze_panes = "A2"
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


async def staff_table(session: AsyncSession, settings: Settings, lang: str) -> bytes:
    """Every staff member with their totals, and the company totals in the last row (PAYA-1..3)."""
    data = await overview.admin_staff(session, settings)
    wb = Workbook()
    ws = wb.active
    ws.title = t(lang, "Staff")[:31]
    columns = ["Staff member", "Workers", "Phones", "To be paid", "Pending review", "Cash on hand", "Funding awaiting", "Needs funding"]
    ws.append([t(lang, c) for c in columns])
    for r in data["staff"]:
        ws.append([r["name"], r["workers"], r["phones"], r["to_be_paid"], r["pending_review"], r["cash_on_hand"],
                   r["funding_awaiting"], r["needs_funding"]])
    totals = data["totals"]
    ws.append([t(lang, "Total"), totals["workers"], totals["phones"], totals["to_be_paid"], totals["pending_review"],
               totals["staff_cash"], sum(r["funding_awaiting"] for r in data["staff"]), totals["needs_funding"]])
    for cell in ws[ws.max_row]:
        cell.font = Font(bold=True)
    for row in ws.iter_rows(min_row=2, min_col=4, max_col=8):
        for cell in row:
            cell.number_format = "#,##0"
    return _finish(wb, [32, 10, 10, 16, 16, 16, 18, 16])


async def statement(session: AsyncSession, settings: Settings, staff: User, lang: str) -> bytes:
    """Every funding the staff member received and every worker payment they sent, newest first, with
    the cash on hand after each line that counts (PAYS-13)."""
    lines = await transfers.statement(session, staff.id)
    users = await overview.users_by_id(session, {x.transfer.sender_id for x in lines} | {x.transfer.receiver_id for x in lines})
    wb = Workbook()
    ws = wb.active
    ws.title = t(lang, "Statement")[:31]
    columns = ["Date paid", "Recorded", "Kind", "With", "Received, so'm", "Paid out, so'm", "Status", "Cash after, so'm", "Note", "Reason"]
    ws.append([t(lang, c) for c in columns])
    for x in lines:
        tr = x.transfer
        other = users.get(tr.sender_id if x.incoming else tr.receiver_id)
        ws.append([
            formats.day(tr.paid_on),
            formats.moment(tr.created_at, settings.tz),
            t(lang, "Funding received" if x.incoming else "Payment to worker"),
            other.full_name if other else "",
            tr.amount if x.incoming else None,
            None if x.incoming else tr.amount,
            t(lang, STATE_NAMES[tr.state]),
            x.balance_after,
            tr.note,
            tr.reason or "",
        ])
    for row in ws.iter_rows(min_row=2):
        for cell in (row[4], row[5], row[7]):
            cell.number_format = "#,##0"
    return _finish(wb, [14, 18, 22, 30, 16, 16, 22, 16, 30, 30])
