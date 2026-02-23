/**
 * upload.js — Drag-and-drop file upload handling
 *
 * Usage:
 *   const uploader = new FileUploader({
 *     zoneId:       'passport-zone',     // <div> drop zone element
 *     previewId:    'passport-previews', // container for file thumbnails
 *     uploadUrl:    '/client/upload/passport',
 *     allowMultiple: false,              // true for documents
 *     accept:       'image/*,.pdf',
 *     maxSizeMB:    10,
 *     extraData:    { client_id: '...' }, // extra form fields
 *     onSuccess:    (data) => {},
 *     onError:      (msg) => {},
 *   });
 */

class FileUploader {
  constructor({
    zoneId,
    previewId,
    uploadUrl,
    allowMultiple = false,
    accept = 'image/*,.pdf',
    maxSizeMB = 10,
    extraData = {},
    onSuccess = () => {},
    onError = () => {},
  } = {}) {
    this.zone         = document.getElementById(zoneId);
    this.previewContainer = previewId ? document.getElementById(previewId) : null;
    this.uploadUrl    = uploadUrl;
    this.allowMultiple = allowMultiple;
    this.accept       = accept;
    this.maxSizeBytes = maxSizeMB * 1024 * 1024;
    this.extraData    = extraData;
    this.onSuccess    = onSuccess;
    this.onError      = onError;

    this.uploadedFiles = [];  // track uploads for this zone

    if (this.zone) this._bind();
  }

  // ── Event binding ────────────────────────────────────────────
  _bind() {
    const zone = this.zone;

    zone.addEventListener('dragover', e => {
      e.preventDefault();
      zone.classList.add('drag-over');
    });

    zone.addEventListener('dragleave', () => {
      zone.classList.remove('drag-over');
    });

    zone.addEventListener('drop', e => {
      e.preventDefault();
      zone.classList.remove('drag-over');
      this._handleFiles(e.dataTransfer.files);
    });

    zone.addEventListener('click', () => {
      const input = this._createFileInput();
      input.click();
    });
  }

  _createFileInput() {
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = this.accept;
    input.multiple = this.allowMultiple;
    input.style.display = 'none';
    document.body.appendChild(input);

    input.addEventListener('change', () => {
      this._handleFiles(input.files);
      document.body.removeChild(input);
    });

    return input;
  }

  // ── File handling ────────────────────────────────────────────
  _handleFiles(fileList) {
    const files = Array.from(fileList);

    if (!this.allowMultiple && files.length > 1) {
      files.splice(1);  // only take the first
    }

    files.forEach(file => {
      if (!this._validate(file)) return;
      this._upload(file);
    });
  }

  _validate(file) {
    if (file.size > this.maxSizeBytes) {
      this.onError(`${file.name} is too large. Maximum size is ${this.maxSizeBytes / 1024 / 1024} MB.`);
      return false;
    }
    return true;
  }

  // ── Upload ───────────────────────────────────────────────────
  async _upload(file) {
    const previewEl = this._addPreview(file);

    const formData = new FormData();
    formData.append('file', file);
    Object.entries(this.extraData).forEach(([k, v]) => formData.append(k, v));

    try {
      const res = await fetch(this.uploadUrl, {
        method: 'POST',
        body: formData,
      });

      const data = await res.json();

      if (!res.ok || !data.success) {
        this._setPreviewError(previewEl, data.error || 'Upload failed.');
        this.onError(data.error || 'Upload failed.');
        return;
      }

      this._setPreviewSuccess(previewEl, data.data);
      this.uploadedFiles.push(data.data);
      this.onSuccess(data.data, file);

    } catch (err) {
      this._setPreviewError(previewEl, 'Network error. Please try again.');
      this.onError('Network error. Please try again.');
    }
  }

  // ── Preview rendering ────────────────────────────────────────
  _addPreview(file) {
    if (!this.previewContainer) return null;

    const el = document.createElement('div');
    el.className = 'file-preview';
    el.dataset.filename = file.name;

    // Thumbnail (image preview or generic icon)
    const thumb = document.createElement('div');
    thumb.className = 'file-preview-thumb';
    thumb.style.cssText = 'display:flex;align-items:center;justify-content:center;font-size:1.2rem;';

    if (file.type.startsWith('image/')) {
      const img = document.createElement('img');
      img.className = 'file-preview-thumb';
      img.src = URL.createObjectURL(file);
      el.appendChild(img);
    } else {
      thumb.textContent = '📄';
      el.appendChild(thumb);
    }

    const name = document.createElement('span');
    name.className = 'file-preview-name';
    name.textContent = file.name;

    const status = document.createElement('span');
    status.className = 'spinner';
    status.style.marginLeft = 'auto';

    el.appendChild(name);
    el.appendChild(status);

    this.previewContainer.appendChild(el);
    return el;
  }

  _setPreviewSuccess(el, data) {
    if (!el) return;
    const statusEl = el.querySelector('.spinner');
    if (statusEl) {
      statusEl.className = '';
      statusEl.textContent = '✓';
      statusEl.style.cssText = 'color:var(--green);font-weight:700;margin-left:auto;';
    }

    // Add remove button
    const removeBtn = document.createElement('button');
    removeBtn.className = 'file-preview-remove';
    removeBtn.textContent = '×';
    removeBtn.title = 'Remove';
    removeBtn.dataset.documentId = data.document_id || data.passport_id || data.id_record_id || '';
    removeBtn.addEventListener('click', () => this._removeFile(el, removeBtn.dataset.documentId));
    el.appendChild(removeBtn);
  }

  _setPreviewError(el, message) {
    if (!el) return;
    const statusEl = el.querySelector('.spinner');
    if (statusEl) {
      statusEl.className = '';
      statusEl.textContent = '✗';
      statusEl.style.cssText = 'color:var(--red);font-weight:700;margin-left:auto;';
      statusEl.title = message;
    }
  }

  async _removeFile(el, documentId) {
    if (!documentId) {
      el.remove();
      return;
    }

    // Determine delete URL from upload URL context
    const deleteUrl = this.uploadUrl.replace('/upload', '') + '/' + documentId;

    try {
      const res = await fetch(deleteUrl, { method: 'DELETE' });
      if (res.ok) {
        el.remove();
        this.uploadedFiles = this.uploadedFiles.filter(
          f => (f.document_id || f.passport_id) !== documentId
        );
      }
    } catch {
      el.remove();  // Remove from UI regardless
    }
  }
}

// Make available globally
window.FileUploader = FileUploader;
