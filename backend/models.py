"""The records of section 12, in SQLite. Money is whole so'm in integer columns (NFR-7); balances are never
stored, they are always calculated from transfers and recordings (NFR-9)."""

from datetime import date, datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy import text as sql
from sqlalchemy.orm import Mapped, mapped_column

from backend.db import Base, UTCDateTime

TZ = UTCDateTime

# Transfer.kind
FUNDING = "funding"  # Admin → Staff
PAYMENT = "payment"  # Staff → Worker
# Transfer.state (section 9)
AWAITING = "awaiting"
ACCEPTED = "accepted"
DECLINED = "declined"
CANCELLED = "cancelled"
VOIDED = "voided"
# Recording.status: UseMinute's review result, as shown to users
REC_ACCEPTED = "accepted"
REC_PENDING = "pending"
REC_REJECTED = "rejected"
# Rate.scope, most specific last
SCOPE_COMPANY = "company"
SCOPE_STAFF = "staff"
SCOPE_WORKER = "worker"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    username: Mapped[str | None] = mapped_column(String(64))
    full_name: Mapped[str] = mapped_column(String(128))
    # International format, +998XXXXXXXXX
    phone: Mapped[str] = mapped_column(String(20), unique=True)
    # Shared through Telegram (True) or typed by hand (False)
    phone_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    # worker, staff or admin. Super Admin is not stored: see backend/roles.py
    role: Mapped[str] = mapped_column(String(16), default="worker")
    is_blocked: Mapped[bool] = mapped_column(Boolean, default=False)
    language: Mapped[str] = mapped_column(String(2), default="uz")
    registered_at: Mapped[datetime] = mapped_column(TZ)
    # The day the last "recordings rejected" summary went out: at most one per day
    rejection_notice_on: Mapped[date | None] = mapped_column(Date)

    __table_args__ = (Index("ix_users_role", "role"),)


class StaffAssignment(Base):
    """Which staff member is responsible for a worker. One current row per worker; earlier ones stay as history."""

    __tablename__ = "staff_assignments"

    id: Mapped[int] = mapped_column(primary_key=True)
    worker_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    staff_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    started_at: Mapped[datetime] = mapped_column(TZ)
    ended_at: Mapped[datetime | None] = mapped_column(TZ)
    # Who set it; empty when it came from the worker's invite code
    set_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    source: Mapped[str] = mapped_column(String(16), default="admin")  # admin | invite_code

    __table_args__ = (
        Index("uq_staff_assignments_current", "worker_id", unique=True, sqlite_where=sql("ended_at IS NULL")),
        Index("ix_staff_assignments_staff_current", "staff_id", sqlite_where=sql("ended_at IS NULL")),
        Index("ix_staff_assignments_worker", "worker_id", "started_at"),
    )


class InviteCode(Base):
    __tablename__ = "invite_codes"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(8), unique=True)
    label: Mapped[str] = mapped_column(String(100), default="")
    # Workers who link with this code are assigned to this staff member (SET-6)
    staff_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(TZ)


class LinkProgress(Base):
    """Where a user is in the linking form before a UseMinute account is linked."""

    __tablename__ = "link_progress"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    invite_code_id: Mapped[int | None] = mapped_column(ForeignKey("invite_codes.id"))
    invite_code_at: Mapped[datetime | None] = mapped_column(TZ)
    wrong_codes: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(TZ)
    # The email a verification code was sent to; empty on the email step
    pending_email: Mapped[str | None] = mapped_column(String(254))


class DoppiLink(Base):
    """A user's UseMinute account (one email = one phone). Unlinked rows stay as history."""

    __tablename__ = "doppi_links"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    email: Mapped[str] = mapped_column(String(254))  # lower case
    invite_code_id: Mapped[int | None] = mapped_column(ForeignKey("invite_codes.id"))
    verified_at: Mapped[datetime] = mapped_column(TZ)
    unlinked_at: Mapped[datetime | None] = mapped_column(TZ)
    unlinked_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    # After unlinking: the recordings stay in this user's earnings (LNK-14) unless the email was linked to
    # the wrong person. Another user linking the email later takes its recordings either way
    keep_recordings: Mapped[bool] = mapped_column(Boolean, default=True)

    __table_args__ = (
        Index("uq_doppi_links_email_current", "email", unique=True, sqlite_where=sql("unlinked_at IS NULL")),
        Index("uq_doppi_links_user_current", "user_id", unique=True, sqlite_where=sql("unlinked_at IS NULL")),
        Index("ix_doppi_links_email", "email"),
        Index("ix_doppi_links_user", "user_id"),
    )


class VerificationAttempt(Base):
    """A one-time code emailed to prove ownership of a UseMinute email. Only its hash is stored (NFR-5)."""

    __tablename__ = "verification_attempts"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    email: Mapped[str] = mapped_column(String(254))
    code_hash: Mapped[str] = mapped_column(String(64))
    sent_at: Mapped[datetime] = mapped_column(TZ)
    expires_at: Mapped[datetime] = mapped_column(TZ)
    wrong_count: Mapped[int] = mapped_column(Integer, default=0)
    used_at: Mapped[datetime | None] = mapped_column(TZ)
    cancelled_at: Mapped[datetime | None] = mapped_column(TZ)

    __table_args__ = (Index("ix_verification_attempts_user", "user_id", "sent_at"),)


class DoppiAccount(Base):
    """An account (email) in the organisation on UseMinute, from its users list. Every sync refreshes the
    whole list; an account that is no longer listed is kept and marked inactive. A worker links one of
    these by verifying the email."""

    __tablename__ = "doppi_accounts"

    email: Mapped[str] = mapped_column(String(254), primary_key=True)  # lower case
    user_key: Mapped[str | None] = mapped_column(String(64))  # UseMinute's user id
    # Enabled on UseMinute and still listed: only active accounts can be linked
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    joined_at: Mapped[datetime | None] = mapped_column(TZ)
    last_upload_at: Mapped[datetime | None] = mapped_column(TZ)
    uploads: Mapped[int] = mapped_column(Integer, default=0)
    duration_ms: Mapped[int] = mapped_column(BigInteger, default=0)
    synced_at: Mapped[datetime] = mapped_column(TZ)


class Phone(Base):
    """A phone is the UseMinute email it records under. Filled from UseMinute on every sync, for linked and
    not yet linked emails alike (EXT-12)."""

    __tablename__ = "phones"

    email: Mapped[str] = mapped_column(String(254), primary_key=True)
    model: Mapped[str | None] = mapped_column(String(100))
    device_type: Mapped[str | None] = mapped_column(String(50))
    first_recorded_at: Mapped[datetime | None] = mapped_column(TZ)
    last_recorded_at: Mapped[datetime | None] = mapped_column(TZ)
    recordings: Mapped[int] = mapped_column(Integer, default=0)


class Recording(Base):
    """One recording session in UseMinute. Read-only copy: the status is updated on every sync."""

    __tablename__ = "recordings"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # UseMinute's session id
    user_key: Mapped[str | None] = mapped_column(String(64))  # UseMinute's user id, needed to fetch the reason
    email: Mapped[str] = mapped_column(String(254))  # lower case
    phone_model: Mapped[str | None] = mapped_column(String(100))
    device_type: Mapped[str | None] = mapped_column(String(50))
    recorded_at: Mapped[datetime] = mapped_column(TZ)
    uploaded_at: Mapped[datetime | None] = mapped_column(TZ)
    # Calendar day of the recording in the configured timezone (PRG-9)
    day: Mapped[date] = mapped_column(Date)
    # Length in minutes exactly as UseMinute reported it, and the same length in whole milliseconds. Every sum
    # uses the integer, so amounts are exact and come out the same every time they are re-derived
    minutes: Mapped[float] = mapped_column(Float)
    duration_ms: Mapped[int] = mapped_column(Integer)
    review_status: Mapped[str] = mapped_column(String(32))  # UseMinute's own word
    status: Mapped[str] = mapped_column(String(10))  # accepted | pending | rejected
    verdict: Mapped[str | None] = mapped_column(String(32))
    flagged_videos: Mapped[int | None] = mapped_column(Integer)
    chunks: Mapped[int | None] = mapped_column(Integer)
    task_name: Mapped[str | None] = mapped_column(String(255))
    category: Mapped[str | None] = mapped_column(String(255))
    # Stored but not used in any amount (section 15)
    rate_multiplier: Mapped[float | None] = mapped_column(Float)
    # UseMinute's reason for a rejection; "" when UseMinute gave none, empty until it has been fetched
    rejection_reason: Mapped[str | None] = mapped_column(Text)
    # The phone's clock was 12 hours ahead and the time was moved back (EXT-9)
    clock_fixed: Mapped[bool] = mapped_column(Boolean, default=False)
    first_seen_at: Mapped[datetime] = mapped_column(TZ)
    synced_at: Mapped[datetime] = mapped_column(TZ)
    # When the worker was told about the rejection (or did not need to be)
    rejection_notified_at: Mapped[datetime | None] = mapped_column(TZ)

    __table_args__ = (
        # Every amount is a sum over (email, day range) by status: this index answers it without the table
        Index("ix_recordings_email_day", "email", "day", "status", "duration_ms"),
        Index("ix_recordings_email_recorded", "email", "recorded_at"),
        Index("ix_recordings_reason_todo", "id", sqlite_where=sql("status = 'rejected' AND rejection_reason IS NULL")),
        Index("ix_recordings_notice_todo", "email", sqlite_where=sql("status = 'rejected' AND rejection_notified_at IS NULL")),
    )


class Rate(Base):
    """So'm per hour of accepted recording, from a start date. Rows are only ever added, so earlier days
    keep their value (CALC-6). A staff or worker row with no amount ends that override."""

    __tablename__ = "rates"

    id: Mapped[int] = mapped_column(primary_key=True)
    scope: Mapped[str] = mapped_column(String(10))  # company | staff | worker
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))  # the staff member or worker
    amount: Mapped[int | None] = mapped_column(Integer)
    start_date: Mapped[date] = mapped_column(Date)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(TZ)

    __table_args__ = (
        CheckConstraint("amount IS NULL OR (amount >= 1 AND amount <= 10000000)", name="amount_range"),
        CheckConstraint("(scope = 'company') = (user_id IS NULL)", name="scope_target"),
        CheckConstraint("scope <> 'company' OR amount IS NOT NULL", name="company_amount"),
        Index("ix_rates_scope_user_start", "scope", "user_id", "start_date"),
    )


class Transfer(Base):
    """A handover of money recorded by the sender. It counts only once the receiver accepts it (section 9).
    Never edited or deleted: only its state moves forward."""

    __tablename__ = "transfers"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(10))  # funding | payment
    sender_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    receiver_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    amount: Mapped[int] = mapped_column(BigInteger)
    paid_on: Mapped[date] = mapped_column(Date)
    note: Mapped[str] = mapped_column(String(500), default="")
    state: Mapped[str] = mapped_column(String(10), default=AWAITING)
    created_at: Mapped[datetime] = mapped_column(TZ)
    answered_at: Mapped[datetime | None] = mapped_column(TZ)  # accepted or declined
    cancelled_at: Mapped[datetime | None] = mapped_column(TZ)
    voided_at: Mapped[datetime | None] = mapped_column(TZ)
    voided_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    reason: Mapped[str | None] = mapped_column(String(500))  # decline or void reason
    # Sent by the Web App with the form, so a double tap or a retry makes one transfer (PAYS-10)
    client_key: Mapped[str | None] = mapped_column(String(64))
    reminders_sent: Mapped[int] = mapped_column(Integer, default=0)
    reminded_at: Mapped[datetime | None] = mapped_column(TZ)

    __table_args__ = (
        CheckConstraint("amount >= 1 AND amount <= 1000000000", name="amount_range"),
        CheckConstraint("sender_id <> receiver_id", name="not_self"),
        UniqueConstraint("sender_id", "client_key", name="uq_transfers_sender_client_key"),
        Index("ix_transfers_receiver", "receiver_id", "kind", "state"),
        Index("ix_transfers_sender", "sender_id", "kind", "state"),
        Index("ix_transfers_awaiting", "created_at", sqlite_where=sql("state = 'awaiting'")),
    )


class TransferMessage(Base):
    """A bot message that asks the receiver about a transfer: its buttons are removed once answered (NTF-3)."""

    __tablename__ = "transfer_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    transfer_id: Mapped[int] = mapped_column(ForeignKey("transfers.id"))
    chat_id: Mapped[int] = mapped_column(BigInteger)
    message_id: Mapped[int] = mapped_column(BigInteger)

    __table_args__ = (Index("ix_transfer_messages_transfer", "transfer_id"),)


class Outbox(Base):
    """A Telegram message waiting for the bot to deliver it. The backend writes the row in the same
    transaction as the change it announces; the bot collects, sends and reports back. So a change is
    never lost or undone because Telegram or the bot was unavailable (NTF-2, NFR-13)."""

    __tablename__ = "outbox"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(10))  # send | edit | document
    chat_id: Mapped[int] = mapped_column(BigInteger)  # the receiver's Telegram id
    message_id: Mapped[int | None] = mapped_column(BigInteger)  # edit: the message to change
    text: Mapped[str] = mapped_column(Text, default="")
    # Rows of buttons: {"text", "data"} answers in the chat, {"text", "web_app"} opens the Web App
    buttons: Mapped[list | None] = mapped_column(JSON)
    # send: this message asks about the transfer, so it is remembered and updated once answered
    transfer_id: Mapped[int | None] = mapped_column(ForeignKey("transfers.id"))
    purpose: Mapped[str] = mapped_column(String(64), default="")
    document: Mapped[bytes | None] = mapped_column(LargeBinary)  # document: the file to send
    filename: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(10), default="pending")  # pending | claimed | sent | failed | skipped
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    not_before: Mapped[datetime | None] = mapped_column(TZ)
    claimed_at: Mapped[datetime | None] = mapped_column(TZ)
    done_at: Mapped[datetime | None] = mapped_column(TZ)
    error: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(TZ)

    __table_args__ = (
        Index("ix_outbox_todo", "id", sqlite_where=sql("status IN ('pending', 'claimed')")),
        Index("ix_outbox_purpose", "purpose", "chat_id"),
    )


class AuditLog(Base):
    """Who did what and when. The database refuses to change or delete rows (NFR-10)."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    at: Mapped[datetime] = mapped_column(TZ)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))  # empty: the system
    action: Mapped[str] = mapped_column(String(64))
    target_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    entity: Mapped[str | None] = mapped_column(String(32))
    entity_id: Mapped[str | None] = mapped_column(String(64))
    details: Mapped[dict] = mapped_column(JSON, default=dict)

    __table_args__ = (
        Index("ix_audit_log_at", "at"),
        Index("ix_audit_log_action", "action", "at"),
        Index("ix_audit_log_target", "target_user_id", "at"),
    )


class SyncState(Base):
    """The single row describing the UseMinute sync (SET-12)."""

    __tablename__ = "sync_state"

    id: Mapped[int] = mapped_column(primary_key=True)
    started_at: Mapped[datetime | None] = mapped_column(TZ)
    finished_at: Mapped[datetime | None] = mapped_column(TZ)
    success_at: Mapped[datetime | None] = mapped_column(TZ)
    error: Mapped[str | None] = mapped_column(Text)
    stats: Mapped[dict | None] = mapped_column(JSON)
    # UseMinute refused this login (a fingerprint, never the token): the schedule stops until it changes (EXT-11)
    refused_login: Mapped[str | None] = mapped_column(String(32))
    alerted_error: Mapped[str | None] = mapped_column(Text)
    alerted_at: Mapped[datetime | None] = mapped_column(TZ)
    # "A sync is running": who holds it and when it last showed it is alive. One sync at a time across
    # every server process (SET-13); a lock nobody refreshes for a while is taken over
    lock_token: Mapped[str | None] = mapped_column(String(32))
    lock_at: Mapped[datetime | None] = mapped_column(TZ)


class AppState(Base):
    """Small facts the backend remembers, such as the bot's name for emails and the last backup day."""

    __tablename__ = "app_state"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON, default=dict)
