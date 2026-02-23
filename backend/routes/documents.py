"""
documents.py — Admin document management routes.

File naming convention: ClientName_DocumentType_YYYY-MM-DD (utils/naming.py)
Client-side uploads are handled in routes/client.py.
These routes are for admin access: list, download, rename, delete.
"""

import os
import mimetypes
import logging
from flask import Blueprint, request, send_file, current_app

from database import db
from models import Document, Client, RequestedDocument
from utils.response import success, error, not_found, forbidden
from utils.auth import login_required, get_current_firm_id
from utils.naming import make_document_filename
from datetime import date

documents_bp = Blueprint("documents", __name__)
logger = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════
#  Admin — get document metadata
# ════════════════════════════════════════════════════════════

@documents_bp.route("/<document_id>", methods=["GET"])
@login_required
def get_document(document_id):
    """
    GET /api/documents/<document_id>
    Returns document metadata (admin only, firm-scoped).
    """
    firm_id = get_current_firm_id()
    doc     = _get_doc_scoped(document_id, firm_id)
    if not doc:
        return not_found("Document")

    return success(data=_doc_dict(doc))


# ════════════════════════════════════════════════════════════
#  Admin — download / serve file
# ════════════════════════════════════════════════════════════

@documents_bp.route("/<document_id>/download", methods=["GET"])
@login_required
def download_document(document_id):
    """
    GET /api/documents/<document_id>/download
    Serves the actual file for admin download (firm-scoped).
    """
    firm_id = get_current_firm_id()
    doc     = _get_doc_scoped(document_id, firm_id)
    if not doc:
        return not_found("Document")

    if not os.path.exists(doc.file_path):
        logger.warning(f"File missing on disk: {doc.file_path}")
        return not_found("Document file (missing from disk)")

    mime, _ = mimetypes.guess_type(doc.file_path)
    return send_file(
        doc.file_path,
        mimetype=mime or "application/octet-stream",
        as_attachment=True,
        download_name=doc.saved_filename,
    )


# ════════════════════════════════════════════════════════════
#  Admin — inline preview (images / PDF)
# ════════════════════════════════════════════════════════════

@documents_bp.route("/<document_id>/preview", methods=["GET"])
@login_required
def preview_document(document_id):
    """
    GET /api/documents/<document_id>/preview
    Serves the file inline (for images and PDFs displayed in browser).
    """
    firm_id = get_current_firm_id()
    doc     = _get_doc_scoped(document_id, firm_id)
    if not doc:
        return not_found("Document")

    if not os.path.exists(doc.file_path):
        return not_found("Document file")

    mime, _ = mimetypes.guess_type(doc.file_path)
    return send_file(
        doc.file_path,
        mimetype=mime or "application/octet-stream",
        as_attachment=False,
    )


# ════════════════════════════════════════════════════════════
#  Admin — rename / re-categorise
# ════════════════════════════════════════════════════════════

@documents_bp.route("/<document_id>", methods=["PATCH"])
@login_required
def update_document(document_id):
    """
    PATCH /api/documents/<document_id>
    Body: { "file_type": str }  — update category; regenerates saved_filename.
    """
    from models import DocumentCategory
    firm_id = get_current_firm_id()
    doc     = _get_doc_scoped(document_id, firm_id)
    if not doc:
        return not_found("Document")

    body = request.get_json() or {}
    new_type_str = body.get("file_type")
    if new_type_str:
        try:
            new_type = DocumentCategory[new_type_str.lower().replace(" ", "_")]
        except KeyError:
            new_type = DocumentCategory.other
        doc.file_type = new_type

        # Regenerate saved filename using naming convention
        client = Client.query.get(doc.client_id)
        if client:
            ext      = doc.saved_filename.rsplit(".", 1)[-1] if "." in doc.saved_filename else "pdf"
            new_name = make_document_filename(client.full_name, new_type.value, ext, date.today())
            new_path = os.path.join(os.path.dirname(doc.file_path), new_name)
            # Only rename if name actually changed and target doesn't exist
            if new_path != doc.file_path and not os.path.exists(new_path):
                try:
                    os.rename(doc.file_path, new_path)
                    doc.file_path     = new_path
                    doc.saved_filename = new_name
                except OSError as e:
                    logger.warning(f"Could not rename document file: {e}")

    db.session.commit()
    return success(data=_doc_dict(doc))


# ════════════════════════════════════════════════════════════
#  Admin — delete
# ════════════════════════════════════════════════════════════

@documents_bp.route("/<document_id>", methods=["DELETE"])
@login_required
def delete_document(document_id):
    """
    DELETE /api/documents/<document_id>
    Admin delete — removes file from disk and DB record.
    Resets any linked RequestedDocument checklist item.
    """
    firm_id = get_current_firm_id()
    doc     = _get_doc_scoped(document_id, firm_id)
    if not doc:
        return not_found("Document")

    # Remove file from disk
    if doc.file_path and os.path.exists(doc.file_path):
        try:
            os.remove(doc.file_path)
        except OSError as e:
            logger.warning(f"Could not delete file {doc.file_path}: {e}")

    # Reset checklist item if linked
    req = RequestedDocument.query.filter_by(received_document_id=document_id).first()
    if req:
        req.is_received          = False
        req.received_document_id = None

    db.session.delete(doc)
    db.session.commit()
    return success(data={"document_id": document_id, "deleted": True})


# ════════════════════════════════════════════════════════════
#  Admin — list all documents for a client
# ════════════════════════════════════════════════════════════

@documents_bp.route("/client/<client_id>", methods=["GET"])
@login_required
def list_client_documents(client_id):
    """
    GET /api/documents/client/<client_id>
    Returns all documents for a client (firm-scoped).
    """
    firm_id = get_current_firm_id()
    client  = Client.query.filter_by(client_id=client_id, firm_id=firm_id).first()
    if not client:
        return not_found("Client")

    docs = Document.query.filter_by(client_id=client_id).order_by(Document.uploaded_at.desc()).all()

    return success(data={
        "documents": [_doc_dict(d) for d in docs],
        "total":     len(docs),
    })


# ════════════════════════════════════════════════════════════
#  Admin — list all documents across firm (paginated)
# ════════════════════════════════════════════════════════════

@documents_bp.route("/", methods=["GET"])
@login_required
def list_all_documents():
    """
    GET /api/documents/?page=1&per_page=50&file_type=<type>
    Returns documents across all clients for this firm.
    """
    firm_id  = get_current_firm_id()
    page     = int(request.args.get("page", 1))
    per_page = min(int(request.args.get("per_page", 50)), 200)
    file_type_filter = request.args.get("file_type")

    query = (
        Document.query
        .join(Client, Document.client_id == Client.client_id)
        .filter(Client.firm_id == firm_id)
        .order_by(Document.uploaded_at.desc())
    )

    if file_type_filter:
        from models import DocumentCategory
        try:
            ft = DocumentCategory[file_type_filter.lower().replace(" ", "_")]
            query = query.filter(Document.file_type == ft)
        except KeyError:
            pass

    total = query.count()
    docs  = query.offset((page - 1) * per_page).limit(per_page).all()

    return success(data={
        "documents": [_doc_dict(d) for d in docs],
        "total":     total,
        "page":      page,
        "per_page":  per_page,
        "pages":     (total + per_page - 1) // per_page,
    })


# ════════════════════════════════════════════════════════════
#  Admin — bulk download (zip) for a client
# ════════════════════════════════════════════════════════════

@documents_bp.route("/client/<client_id>/zip", methods=["GET"])
@login_required
def download_client_zip(client_id):
    """
    GET /api/documents/client/<client_id>/zip
    Returns a ZIP archive of all documents for a client.
    """
    import io, zipfile
    firm_id = get_current_firm_id()
    client  = Client.query.filter_by(client_id=client_id, firm_id=firm_id).first()
    if not client:
        return not_found("Client")

    docs = Document.query.filter_by(client_id=client_id).all()
    if not docs:
        return error("No documents to download.", 404)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for doc in docs:
            if os.path.exists(doc.file_path):
                zf.write(doc.file_path, arcname=doc.saved_filename)
    buf.seek(0)

    safe_name = client.full_name.replace(" ", "_")
    return send_file(
        buf,
        mimetype="application/zip",
        as_attachment=True,
        download_name=f"{safe_name}_{client.reference_id}_documents.zip",
    )


# ════════════════════════════════════════════════════════════
#  Helpers
# ════════════════════════════════════════════════════════════

def _get_doc_scoped(document_id: str, firm_id: str):
    """Return a Document only if it belongs to the given firm."""
    doc = Document.query.get(document_id)
    if not doc:
        return None
    client = Client.query.filter_by(client_id=doc.client_id, firm_id=firm_id).first()
    if not client:
        return None
    return doc


def _doc_dict(doc: Document) -> dict:
    return {
        "document_id":       doc.document_id,
        "client_id":         doc.client_id,
        "original_filename": doc.original_filename,
        "saved_filename":    doc.saved_filename,
        "file_type":         doc.file_type.value,
        "requested_by_firm": doc.requested_by_firm,
        "uploaded_at":       doc.uploaded_at.isoformat() if doc.uploaded_at else None,
        "download_url":      f"/api/documents/{doc.document_id}/download",
        "preview_url":       f"/api/documents/{doc.document_id}/preview",
        "file_exists":       os.path.exists(doc.file_path),
    }
