/**
 * recorder.js — Browser audio recording with waveform visualisation
 *
 * Usage:
 *   const recorder = new AudioRecorder({
 *     canvasId:     'waveform-canvas',   // <canvas> element for visualisation
 *     onStop:       (blob, url) => {},   // called when recording stops
 *     onTranscript: (text) => {},        // called when Whisper returns text
 *   });
 *
 *   recorder.start();
 *   recorder.stop();
 *   recorder.reset();
 */

class AudioRecorder {
  constructor({ canvasId, onStop, onTranscript } = {}) {
    this.canvasId     = canvasId;
    this.onStop       = onStop       || (() => {});
    this.onTranscript = onTranscript || (() => {});

    this.mediaRecorder  = null;
    this.audioChunks    = [];
    this.audioBlob      = null;
    this.audioUrl       = null;
    this.stream         = null;
    this.animationFrame = null;
    this.analyser       = null;
    this.isRecording    = false;
  }

  // ── Start recording ──────────────────────────────────────────
  async start() {
    if (this.isRecording) return;

    try {
      this.stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (err) {
      console.error('Microphone access denied:', err);
      alert('Microphone access is required to record a voice note. Please allow access and try again.');
      return;
    }

    // Set up AudioContext for waveform
    const audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    const source   = audioCtx.createMediaStreamSource(this.stream);
    this.analyser  = audioCtx.createAnalyser();
    this.analyser.fftSize = 256;
    source.connect(this.analyser);

    // MediaRecorder — prefer webm/opus, fall back gracefully
    const mimeType = MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
      ? 'audio/webm;codecs=opus'
      : 'audio/webm';

    this.audioChunks  = [];
    this.mediaRecorder = new MediaRecorder(this.stream, { mimeType });

    this.mediaRecorder.ondataavailable = e => {
      if (e.data.size > 0) this.audioChunks.push(e.data);
    };

    this.mediaRecorder.onstop = () => {
      this.audioBlob = new Blob(this.audioChunks, { type: mimeType });
      this.audioUrl  = URL.createObjectURL(this.audioBlob);
      this.onStop(this.audioBlob, this.audioUrl);
      this._stopWaveform();
      this._releaseMic();
    };

    this.mediaRecorder.start(100); // collect data every 100ms
    this.isRecording = true;

    if (this.canvasId) this._startWaveform();
  }

  // ── Stop recording ───────────────────────────────────────────
  stop() {
    if (!this.isRecording || !this.mediaRecorder) return;
    this.isRecording = false;
    this.mediaRecorder.stop();
  }

  // ── Reset ────────────────────────────────────────────────────
  reset() {
    this.stop();
    this.audioChunks  = [];
    this.audioBlob    = null;
    this.audioUrl     = null;
    this._clearCanvas();
    this._releaseMic();
  }

  // ── Waveform visualisation ───────────────────────────────────
  _startWaveform() {
    const canvas = document.getElementById(this.canvasId);
    if (!canvas) return;
    const ctx    = canvas.getContext('2d');
    const buffer = new Uint8Array(this.analyser.frequencyBinCount);

    const draw = () => {
      if (!this.isRecording) return;
      this.animationFrame = requestAnimationFrame(draw);

      this.analyser.getByteTimeDomainData(buffer);

      const theme = document.documentElement.getAttribute('data-theme') || 'dark';
      const bgColor   = theme === 'dark' ? '#1E1E1E' : '#FFFFFF';
      const lineColor = theme === 'dark' ? '#6B6347' : '#6B6347';

      ctx.fillStyle = bgColor;
      ctx.fillRect(0, 0, canvas.width, canvas.height);

      ctx.lineWidth   = 1.5;
      ctx.strokeStyle = lineColor;
      ctx.beginPath();

      const sliceWidth = canvas.width / buffer.length;
      let x = 0;

      for (let i = 0; i < buffer.length; i++) {
        const v = buffer[i] / 128.0;
        const y = (v * canvas.height) / 2;
        if (i === 0) ctx.moveTo(x, y);
        else         ctx.lineTo(x, y);
        x += sliceWidth;
      }

      ctx.lineTo(canvas.width, canvas.height / 2);
      ctx.stroke();
    };

    draw();
  }

  _stopWaveform() {
    if (this.animationFrame) {
      cancelAnimationFrame(this.animationFrame);
      this.animationFrame = null;
    }
    this._clearCanvas();
  }

  _clearCanvas() {
    const canvas = document.getElementById(this.canvasId);
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    const theme = document.documentElement.getAttribute('data-theme') || 'dark';
    ctx.fillStyle = theme === 'dark' ? '#1E1E1E' : '#FFFFFF';
    ctx.fillRect(0, 0, canvas.width, canvas.height);
  }

  _releaseMic() {
    if (this.stream) {
      this.stream.getTracks().forEach(t => t.stop());
      this.stream = null;
    }
  }

  // ── Upload to server and get transcription ───────────────────
  async uploadForTranscription(clientId, sequenceNumber) {
    if (!this.audioBlob) {
      throw new Error('No recording available to upload.');
    }

    const formData = new FormData();
    formData.append('audio_file', this.audioBlob, `statement_${sequenceNumber}.webm`);
    formData.append('client_id', clientId);
    formData.append('sequence_number', sequenceNumber);

    const res = await fetch('/client/statement/audio', {
      method: 'POST',
      body: formData,
    });

    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error(data.error || 'Upload failed.');
    }

    return await res.json();
  }
}

// Make available globally
window.AudioRecorder = AudioRecorder;
