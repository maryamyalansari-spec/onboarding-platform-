"""
models.py — All database table definitions for the Itifaq Onboarding Platform.

Multi-tenancy: every table that stores client or user data includes firm_id.
All queries MUST filter by firm_id. No firm can access another firm's data.
"""

from datetime import datetime, timezone
from database import db
from sqlalchemy import (
    Column, Integer, String, Text, Boolean, DateTime,
    ForeignKey, Numeric, ARRAY, Enum as PgEnum, Index
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
import uuid
import enum


# ─────────────────────────────────────────────
# Enums
# ─────────────────────────────────────────────

class UserRole(enum.Enum):
    admin = "admin"
    lawyer = "lawyer"


class ClientChannel(enum.Enum):
    whatsapp = "whatsapp"
    web = "web"


class ClientStatus(enum.Enum):
    pending = "pending"
    id_uploaded = "id_uploaded"
    conflict_check = "conflict_check"
    manual_review = "manual_review"
    context_collection = "context_collection"
    review = "review"
    approved = "approved"
    rejected = "rejected"


class WhatsAppState(enum.Enum):
    greeting = "greeting"
    contact_info = "contact_info"
    passport_upload = "passport_upload"
    conflict_pending = "conflict_pending"
    statement_1 = "statement_1"
    statement_1_confirm = "statement_1_confirm"
    statement_2 = "statement_2"
    statement_2_confirm = "statement_2_confirm"
    statement_3 = "statement_3"
    statement_3_confirm = "statement_3_confirm"
    document_upload = "document_upload"
    document_categorize = "document_categorize"
    completed = "completed"


class MatchType(enum.Enum):
    exact = "exact"
    strong = "strong"
    soft = "soft"
    none = "none"


class ConflictDecision(enum.Enum):
    approved = "approved"
    rejected = "rejected"
    pending = "pending"


class DocumentCategory(enum.Enum):
    passport = "Passport"
    emirates_id = "Emirates ID"
    business_license = "Business License"
    contract = "Contract"
    court_document = "Court Document"
    power_of_attorney = "Power of Attorney"
    title_deed = "Title Deed"
    bank_statement = "Bank Statement"
    corporate_documents = "Corporate Documents"
    other = "Other"


class StatementChannel(enum.Enum):
    whatsapp = "whatsapp"
    web = "web"


class DocuSealStatus(enum.Enum):
    sent = "sent"
    signed = "signed"
    declined = "declined"


# ─────────────────────────────────────────────
# Helper
# ─────────────────────────────────────────────

def utcnow():
    return datetime.now(timezone.utc)


def new_uuid():
    return str(uuid.uuid4())


# ─────────────────────────────────────────────
# 1. LawFirms
# ─────────────────────────────────────────────

class LawFirm(db.Model):
    __tablename__ = "law_firms"

    firm_id = Column(String(36), primary_key=True, default=new_uuid)
    firm_name = Column(String(255), nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    # Relationships
    users = relationship("User", back_populates="firm", cascade="all, delete-orphan")
    clients = relationship("Client", back_populates="firm", cascade="all, delete-orphan")
    conflict_index = relationship("ConflictIndex", back_populates="firm", cascade="all, delete-orphan")
    audit_logs = relationship("AuditLog", back_populates="firm", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<LawFirm {self.firm_name}>"


# ─────────────────────────────────────────────
# 2. Users (Lawyers and Admins)
# ─────────────────────────────────────────────

class User(db.Model):
    __tablename__ = "users"

    user_id = Column(String(36), primary_key=True, default=new_uuid)
    firm_id = Column(String(36), ForeignKey("law_firms.firm_id", ondelete="CASCADE"), nullable=False)
    name = Column(String(255), nullable=False)
    email = Column(String(255), nullable=False, unique=True)
    password_hash = Column(String(255), nullable=False)
    role = Column(PgEnum(UserRole, name="user_role_enum"), nullable=False, default=UserRole.lawyer)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)

    firm = relationship("LawFirm", back_populates="users")
    audit_logs = relationship("AuditLog", back_populates="performed_by_user")
    conflict_decisions = relationship("ConflictResult", back_populates="reviewer")

    __table_args__ = (
        Index("ix_users_firm_id", "firm_id"),
        Index("ix_users_email", "email"),
    )

    def __repr__(self):
        return f"<User {self.name} ({self.role.value})>"


# ─────────────────────────────────────────────
# 3. Clients
# ─────────────────────────────────────────────

class Client(db.Model):
    __tablename__ = "clients"

    client_id = Column(String(36), primary_key=True, default=new_uuid)
    firm_id = Column(String(36), ForeignKey("law_firms.firm_id", ondelete="CASCADE"), nullable=False)
    reference_id = Column(String(30), unique=True, nullable=False)   # e.g. ITF-2026-04821
    portal_token = Column(String(128), unique=True, nullable=False)
    full_name = Column(String(255), nullable=False)
    email = Column(String(255), nullable=True)
    phone = Column(String(30), nullable=True)
    channel = Column(PgEnum(ClientChannel, name="client_channel_enum"), nullable=False)
    whatsapp_state = Column(
        PgEnum(WhatsAppState, name="whatsapp_state_enum"),
        nullable=True,
        default=WhatsAppState.greeting
    )
    status = Column(
        PgEnum(ClientStatus, name="client_status_enum"),
        nullable=False,
        default=ClientStatus.pending
    )
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    token_expires_at = Column(DateTime(timezone=True), nullable=True)

    firm = relationship("LawFirm", back_populates="clients")
    passports = relationship("Passport", back_populates="client", cascade="all, delete-orphan")
    emirates_ids = relationship("EmiratesID", back_populates="client", cascade="all, delete-orphan")
    statements = relationship("Statement", back_populates="client", cascade="all, delete-orphan", order_by="Statement.sequence_number")
    documents = relationship("Document", back_populates="client", cascade="all, delete-orphan")
    ai_briefs = relationship("AIBrief", back_populates="client", cascade="all, delete-orphan")
    conflict_results = relationship("ConflictResult", back_populates="client", cascade="all, delete-orphan")
    client_edits = relationship("ClientEdit", back_populates="client", cascade="all, delete-orphan")
    engagement_letters = relationship("EngagementLetter", back_populates="client", cascade="all, delete-orphan")
    kyc_records        = relationship("KYCRecord",        back_populates="client", cascade="all, delete-orphan")
    calendly_bookings  = relationship("CalendlyBooking",  back_populates="client", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_clients_firm_id", "firm_id"),
        Index("ix_clients_reference_id", "reference_id"),
        Index("ix_clients_portal_token", "portal_token"),
        Index("ix_clients_status", "status"),
    )

    def __repr__(self):
        return f"<Client {self.reference_id} — {self.full_name}>"


# ─────────────────────────────────────────────
# 4. Passports (multiple per client)
# ─────────────────────────────────────────────

class Passport(db.Model):
    __tablename__ = "passports"

    passport_id = Column(String(36), primary_key=True, default=new_uuid)
    client_id = Column(String(36), ForeignKey("clients.client_id", ondelete="CASCADE"), nullable=False)
    passport_number = Column(String(50), nullable=True)
    nationality = Column(String(100), nullable=True)
    date_of_birth = Column(String(20), nullable=True)   # stored as string from OCR
    expiry_date = Column(String(20), nullable=True)
    image_path = Column(String(512), nullable=False)
    ocr_raw = Column(JSONB, nullable=True)               # raw PaddleOCR output
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    client = relationship("Client", back_populates="passports")

    __table_args__ = (
        Index("ix_passports_client_id", "client_id"),
    )

    def __repr__(self):
        return f"<Passport {self.passport_number} — {self.nationality}>"


# ─────────────────────────────────────────────
# 5. Emirates IDs
# ─────────────────────────────────────────────

class EmiratesID(db.Model):
    __tablename__ = "emirates_ids"

    id_record_id = Column(String(36), primary_key=True, default=new_uuid)
    client_id = Column(String(36), ForeignKey("clients.client_id", ondelete="CASCADE"), nullable=False)
    id_number = Column(String(50), nullable=True)
    image_path = Column(String(512), nullable=False)
    ocr_raw = Column(JSONB, nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    client = relationship("Client", back_populates="emirates_ids")

    __table_args__ = (
        Index("ix_emirates_ids_client_id", "client_id"),
    )

    def __repr__(self):
        return f"<EmiratesID {self.id_number}>"


# ─────────────────────────────────────────────
# 6. Statements (max 3 per client)
# ─────────────────────────────────────────────

class Statement(db.Model):
    __tablename__ = "statements"

    statement_id = Column(String(36), primary_key=True, default=new_uuid)
    client_id = Column(String(36), ForeignKey("clients.client_id", ondelete="CASCADE"), nullable=False)
    sequence_number = Column(Integer, nullable=False)           # 1, 2, or 3
    raw_audio_path = Column(String(512), nullable=True)
    whisper_transcription = Column(Text, nullable=True)         # raw Whisper output
    client_edited_text = Column(Text, nullable=True)            # final confirmed version
    channel = Column(PgEnum(StatementChannel, name="statement_channel_enum"), nullable=False)
    flagged_for_review = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    client = relationship("Client", back_populates="statements")

    __table_args__ = (
        Index("ix_statements_client_id", "client_id"),
    )

    def __repr__(self):
        return f"<Statement #{self.sequence_number} for client {self.client_id}>"


# ─────────────────────────────────────────────
# 7. Documents
# ─────────────────────────────────────────────

class Document(db.Model):
    __tablename__ = "documents"

    document_id = Column(String(36), primary_key=True, default=new_uuid)
    client_id = Column(String(36), ForeignKey("clients.client_id", ondelete="CASCADE"), nullable=False)
    original_filename = Column(String(512), nullable=False)
    saved_filename = Column(String(512), nullable=False)        # ClientName_DocumentType_YYYY-MM-DD
    file_path = Column(String(1024), nullable=False)
    file_type = Column(
        PgEnum(DocumentCategory, name="document_category_enum"),
        nullable=False,
        default=DocumentCategory.other
    )
    uploaded_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    requested_by_firm = Column(Boolean, default=False, nullable=False)

    client = relationship("Client", back_populates="documents")

    __table_args__ = (
        Index("ix_documents_client_id", "client_id"),
        Index("ix_documents_file_type", "file_type"),
    )

    def __repr__(self):
        return f"<Document {self.saved_filename}>"


# ─────────────────────────────────────────────
# 8. AI Briefs
# ─────────────────────────────────────────────

class AIBrief(db.Model):
    __tablename__ = "ai_briefs"

    brief_id = Column(String(36), primary_key=True, default=new_uuid)
    client_id = Column(String(36), ForeignKey("clients.client_id", ondelete="CASCADE"), nullable=False)
    client_summary = Column(Text, nullable=True)                # 2–3 sentence overview
    situation_overview = Column(Text, nullable=True)
    key_facts = Column(JSONB, nullable=True)                    # list of strings
    documents_provided = Column(JSONB, nullable=True)           # list of doc types
    inconsistencies = Column(Text, nullable=True)
    questions_for_lawyer = Column(JSONB, nullable=True)         # list of strings
    risk_notes = Column(Text, nullable=True)
    lawyer_notes = Column(Text, nullable=True)                  # admin can annotate
    generated_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    raw_gpt_response = Column(Text, nullable=True)              # stored for debugging

    client = relationship("Client", back_populates="ai_briefs")

    __table_args__ = (
        Index("ix_ai_briefs_client_id", "client_id"),
    )

    def __repr__(self):
        return f"<AIBrief for client {self.client_id}>"


# ─────────────────────────────────────────────
# 9. Conflict Index (existing firm database)
# ─────────────────────────────────────────────

class ConflictIndex(db.Model):
    """
    The law firm's existing client/opposing party database used for
    conflict of interest checks. name_embedding is a pgvector column.
    """
    __tablename__ = "conflict_index"

    record_id = Column(String(36), primary_key=True, default=new_uuid)
    firm_id = Column(String(36), ForeignKey("law_firms.firm_id", ondelete="CASCADE"), nullable=False)
    full_name = Column(String(255), nullable=False)
    # pgvector column — defined via raw DDL in init script; SQLAlchemy uses Text as placeholder
    name_embedding = Column(Text, nullable=True)                # overridden by pgvector DDL
    passport_numbers = Column(ARRAY(String), nullable=True, default=list)
    emirates_id = Column(String(50), nullable=True)
    nationality = Column(ARRAY(String), nullable=True, default=list)
    entity_names = Column(ARRAY(String), nullable=True, default=list)
    case_type = Column(String(100), nullable=True)
    opposing_party = Column(String(255), nullable=True)
    source_file = Column(String(512), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    firm = relationship("LawFirm", back_populates="conflict_index")
    conflict_results = relationship("ConflictResult", back_populates="matched_record")

    __table_args__ = (
        Index("ix_conflict_index_firm_id", "firm_id"),
        Index("ix_conflict_index_full_name", "full_name"),
    )

    def __repr__(self):
        return f"<ConflictIndex {self.full_name}>"


# ─────────────────────────────────────────────
# 10. Conflict Results
# ─────────────────────────────────────────────

class ConflictResult(db.Model):
    __tablename__ = "conflict_results"

    conflict_id = Column(String(36), primary_key=True, default=new_uuid)
    client_id = Column(String(36), ForeignKey("clients.client_id", ondelete="CASCADE"), nullable=False)
    match_type = Column(PgEnum(MatchType, name="match_type_enum"), nullable=False)
    matched_record_id = Column(String(36), ForeignKey("conflict_index.record_id", ondelete="SET NULL"), nullable=True)
    confidence_score = Column(Numeric(5, 2), nullable=False, default=0)   # 0–100
    reviewed_by = Column(String(36), ForeignKey("users.user_id", ondelete="SET NULL"), nullable=True)
    decision = Column(
        PgEnum(ConflictDecision, name="conflict_decision_enum"),
        nullable=False,
        default=ConflictDecision.pending
    )
    decision_reason = Column(Text, nullable=True)               # required if approved despite conflict
    decision_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    client = relationship("Client", back_populates="conflict_results")
    matched_record = relationship("ConflictIndex", back_populates="conflict_results")
    reviewer = relationship("User", back_populates="conflict_decisions")

    __table_args__ = (
        Index("ix_conflict_results_client_id", "client_id"),
        Index("ix_conflict_results_decision", "decision"),
    )

    def __repr__(self):
        return f"<ConflictResult {self.match_type.value} score={self.confidence_score}>"


# ─────────────────────────────────────────────
# 11. Client Edits (Audit Trail)
# ─────────────────────────────────────────────

class ClientEdit(db.Model):
    __tablename__ = "client_edits"

    edit_id = Column(String(36), primary_key=True, default=new_uuid)
    client_id = Column(String(36), ForeignKey("clients.client_id", ondelete="CASCADE"), nullable=False)
    field_changed = Column(String(100), nullable=False)
    old_value = Column(Text, nullable=True)
    new_value = Column(Text, nullable=True)
    changed_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    re_conflict_check_triggered = Column(Boolean, default=False, nullable=False)

    client = relationship("Client", back_populates="client_edits")

    __table_args__ = (
        Index("ix_client_edits_client_id", "client_id"),
    )

    def __repr__(self):
        return f"<ClientEdit {self.field_changed} for {self.client_id}>"


# ─────────────────────────────────────────────
# 12. Audit Logs (All Admin Actions)
# ─────────────────────────────────────────────

class AuditLog(db.Model):
    __tablename__ = "audit_logs"

    log_id = Column(String(36), primary_key=True, default=new_uuid)
    firm_id = Column(String(36), ForeignKey("law_firms.firm_id", ondelete="CASCADE"), nullable=False)
    action = Column(Text, nullable=False)
    performed_by = Column(String(36), ForeignKey("users.user_id", ondelete="SET NULL"), nullable=True)
    record_type = Column(String(100), nullable=True)            # e.g. "client", "document"
    record_id = Column(String(36), nullable=True)
    timestamp = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    metadata = Column(JSONB, nullable=True)                     # extra structured detail

    firm = relationship("LawFirm", back_populates="audit_logs")
    performed_by_user = relationship("User", back_populates="audit_logs")

    __table_args__ = (
        Index("ix_audit_logs_firm_id", "firm_id"),
        Index("ix_audit_logs_timestamp", "timestamp"),
        Index("ix_audit_logs_record_id", "record_id"),
    )

    def __repr__(self):
        return f"<AuditLog {self.action} at {self.timestamp}>"


# ─────────────────────────────────────────────
# 13. Engagement Letters
# ─────────────────────────────────────────────

class EngagementLetter(db.Model):
    __tablename__ = "engagement_letters"

    letter_id = Column(String(36), primary_key=True, default=new_uuid)
    client_id = Column(String(36), ForeignKey("clients.client_id", ondelete="CASCADE"), nullable=False)
    matter_type = Column(String(255), nullable=True)
    scope_of_work = Column(Text, nullable=True)
    fee_structure = Column(Text, nullable=True)
    retainer_amount = Column(Numeric(12, 2), nullable=True)
    billing_type = Column(String(100), nullable=True)           # e.g. hourly, fixed, retainer
    timeline = Column(Text, nullable=True)
    pdf_path = Column(String(512), nullable=True)
    docuseal_status = Column(
        PgEnum(DocuSealStatus, name="docuseal_status_enum"),
        nullable=True
    )
    docuseal_document_id = Column(String(255), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    signed_at = Column(DateTime(timezone=True), nullable=True)

    client = relationship("Client", back_populates="engagement_letters")

    __table_args__ = (
        Index("ix_engagement_letters_client_id", "client_id"),
    )

    def __repr__(self):
        return f"<EngagementLetter for client {self.client_id} — {self.docuseal_status}>"


# ─────────────────────────────────────────────
# 14. Firm-Requested Documents (checklist per client)
# ─────────────────────────────────────────────

class RequestedDocument(db.Model):
    """
    Tracks documents the firm has specifically requested from a client.
    Appears as a checklist on both the admin dashboard and client portal.
    """
    __tablename__ = "requested_documents"

    request_id = Column(String(36), primary_key=True, default=new_uuid)
    client_id = Column(String(36), ForeignKey("clients.client_id", ondelete="CASCADE"), nullable=False)
    firm_id = Column(String(36), ForeignKey("law_firms.firm_id", ondelete="CASCADE"), nullable=False)
    document_type = Column(
        PgEnum(DocumentCategory, name="document_category_enum"),
        nullable=False
    )
    notes = Column(Text, nullable=True)
    is_received = Column(Boolean, default=False, nullable=False)
    received_document_id = Column(String(36), ForeignKey("documents.document_id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    __table_args__ = (
        Index("ix_requested_documents_client_id", "client_id"),
        Index("ix_requested_documents_firm_id", "firm_id"),
    )

    def __repr__(self):
        return f"<RequestedDocument {self.document_type.value} received={self.is_received}>"


# ─────────────────────────────────────────────
# 15. KYC Records
# ─────────────────────────────────────────────

class KYCRecord(db.Model):
    """
    Know Your Customer questionnaire submitted by the client before
    proceeding to the statement stage.
    """
    __tablename__ = "kyc_records"

    kyc_id              = Column(String(36), primary_key=True, default=new_uuid)
    client_id           = Column(String(36), ForeignKey("clients.client_id", ondelete="CASCADE"), nullable=False)
    source_of_funds     = Column(Text, nullable=True)           # free-text description
    is_pep              = Column(Boolean, nullable=True)        # politically exposed person
    pep_details         = Column(Text, nullable=True)           # if is_pep == True
    sanctions_ack       = Column(Boolean, default=False)        # client acks sanctions check
    occupation          = Column(String(255), nullable=True)
    employer            = Column(String(255), nullable=True)
    country_of_residence= Column(String(100), nullable=True)
    submitted_at        = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    reviewed            = Column(Boolean, default=False)

    client = relationship("Client", back_populates="kyc_records")

    __table_args__ = (
        Index("ix_kyc_records_client_id", "client_id"),
    )

    def __repr__(self):
        return f"<KYCRecord client={self.client_id} pep={self.is_pep}>"


# ─────────────────────────────────────────────
# 17. Calendly Bookings
# ─────────────────────────────────────────────

class CalendlyBooking(db.Model):
    """
    Stores Calendly consultation bookings made via the admin portal.
    Populated by the Calendly webhook when an invitee schedules.
    """
    __tablename__ = "calendly_bookings"

    booking_id   = Column(String(36), primary_key=True, default=new_uuid)
    client_id    = Column(String(36), ForeignKey("clients.client_id", ondelete="CASCADE"), nullable=True)
    firm_id      = Column(String(36), ForeignKey("law_firms.firm_id", ondelete="CASCADE"), nullable=False)
    event_uuid   = Column(String(255), nullable=True, unique=True)   # Calendly event UUID
    event_name   = Column(String(255), nullable=True)
    invitee_name = Column(String(255), nullable=True)
    invitee_email= Column(String(255), nullable=True)
    start_time   = Column(DateTime(timezone=True), nullable=True)
    end_time     = Column(DateTime(timezone=True), nullable=True)
    cancel_url   = Column(String(512), nullable=True)
    reschedule_url = Column(String(512), nullable=True)
    status       = Column(String(50), nullable=True, default="active")   # active | canceled
    raw_payload  = Column(JSONB, nullable=True)
    created_at   = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    client = relationship("Client", back_populates="calendly_bookings")

    __table_args__ = (
        Index("ix_calendly_bookings_client_id", "client_id"),
        Index("ix_calendly_bookings_firm_id",   "firm_id"),
        Index("ix_calendly_bookings_email",     "invitee_email"),
    )

    def __repr__(self):
        return f"<CalendlyBooking {self.event_name} — {self.invitee_email}>"
