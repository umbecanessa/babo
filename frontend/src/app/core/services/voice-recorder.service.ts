import { Injectable, signal } from '@angular/core';

export type RecordingState = 'idle' | 'recording' | 'transcribing';

@Injectable({ providedIn: 'root' })
export class VoiceRecorderService {
  /** Current state of the recorder. */
  readonly state = signal<RecordingState>('idle');

  /** Recording duration in seconds, updated every 100ms while recording. */
  readonly duration = signal(0);

  /**
   * Waveform frequency data (0-255 per bin).
   * Updated ~60fps during recording for visualization.
   */
  readonly waveformData = signal<Uint8Array>(new Uint8Array(0));

  private mediaRecorder: MediaRecorder | null = null;
  private audioChunks: Blob[] = [];
  private audioContext: AudioContext | null = null;
  private analyser: AnalyserNode | null = null;
  private animationFrame = 0;
  private durationInterval: ReturnType<typeof setInterval> | null = null;
  private startTime = 0;

  /** Start recording audio from the microphone. */
  async startRecording(): Promise<void> {
    if (this.state() === 'recording') return;

    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });

    // Set up Web Audio API for waveform visualization
    this.audioContext = new AudioContext();
    const source = this.audioContext.createMediaStreamSource(stream);
    this.analyser = this.audioContext.createAnalyser();
    this.analyser.fftSize = 256;
    source.connect(this.analyser);

    // Set up MediaRecorder
    const mimeType = MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
      ? 'audio/webm;codecs=opus'
      : 'audio/webm';

    this.mediaRecorder = new MediaRecorder(stream, { mimeType });
    this.audioChunks = [];

    this.mediaRecorder.ondataavailable = (event) => {
      if (event.data.size > 0) {
        this.audioChunks.push(event.data);
      }
    };

    this.mediaRecorder.start(100); // Collect data every 100ms
    this.state.set('recording');
    this.startTime = Date.now();
    this.duration.set(0);

    // Duration counter
    this.durationInterval = setInterval(() => {
      this.duration.set(Math.floor((Date.now() - this.startTime) / 1000));
    }, 100);

    // Waveform animation loop
    this._animateWaveform();
  }

  /** Stop recording and return the audio blob. */
  async stopRecording(): Promise<Blob> {
    return new Promise((resolve, reject) => {
      if (!this.mediaRecorder || this.state() !== 'recording') {
        reject(new Error('Not recording'));
        return;
      }

      this.mediaRecorder.onstop = () => {
        const blob = new Blob(this.audioChunks, { type: 'audio/webm' });
        this._cleanup();
        this.state.set('transcribing');
        resolve(blob);
      };

      this.mediaRecorder.stop();
      // Stop all tracks to release mic
      this.mediaRecorder.stream.getTracks().forEach(t => t.stop());
    });
  }

  /** Cancel recording without producing output. */
  cancelRecording(): void {
    if (this.mediaRecorder && this.state() === 'recording') {
      this.mediaRecorder.stop();
      this.mediaRecorder.stream.getTracks().forEach(t => t.stop());
    }
    this._cleanup();
    this.state.set('idle');
  }

  /** Set state back to idle (call after transcription completes). */
  finishTranscribing(): void {
    this.state.set('idle');
  }

  private _animateWaveform(): void {
    if (!this.analyser) return;

    const bufferLength = this.analyser.frequencyBinCount;
    const dataArray = new Uint8Array(bufferLength);

    const draw = () => {
      if (this.state() !== 'recording') return;
      this.animationFrame = requestAnimationFrame(draw);
      this.analyser!.getByteTimeDomainData(dataArray);
      // Clone to trigger signal change detection
      this.waveformData.set(new Uint8Array(dataArray));
    };

    this.animationFrame = requestAnimationFrame(draw);
  }

  private _cleanup(): void {
    if (this.durationInterval) {
      clearInterval(this.durationInterval);
      this.durationInterval = null;
    }
    if (this.animationFrame) {
      cancelAnimationFrame(this.animationFrame);
      this.animationFrame = 0;
    }
    if (this.audioContext) {
      this.audioContext.close();
      this.audioContext = null;
    }
    this.analyser = null;
    this.mediaRecorder = null;
    this.audioChunks = [];
    this.waveformData.set(new Uint8Array(0));
    this.duration.set(0);
  }
}
