/**
 * 语音能力客户端：
 * - TTS 语音合成：POST /api/speech/tts → audio/wav Blob → 浏览器播放
 * - ASR 流式语音识别：麦克风采集 PCM → 16kHz int16 → /ws/asr WebSocket 透传
 *
 * 后端代理 onnx-hub 并注入 API Key，前端无需接触真实密钥。
 */

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL || "";

// ---------------------------------------------------------------------------
// TTS：文本转语音
// ---------------------------------------------------------------------------

/** 请求后端 TTS 代理，返回 WAV Blob。 */
export async function synthesizeSpeech(text: string): Promise<Blob> {
  const resp = await fetch(`${API_BASE}/api/speech/tts`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text, speaker_id: 0, speed: 1.0 }),
  });
  if (!resp.ok) {
    let detail = "语音合成失败";
    try {
      const data = await resp.json();
      detail = data.detail || detail;
    } catch {
      /* ignore */
    }
    throw new Error(detail);
  }
  return await resp.blob();
}

/** 停止当前 TTS 播放。 */
export function stopPlaying(): void {
  TtsPlayer.stopActive();
}

/**
 * 将长文本切分为适合 TTS 的短句段：先按句末标点切，超长句再按句内标点二次切，
 * 相邻短句合并到 maxLen 上限内，减少请求次数的同时控制单段合成延迟。
 */
export function splitTtsText(text: string, maxLen = 60): string[] {
  const parts: string[] = [];
  let buf = "";
  const pushBuf = () => {
    const seg = buf.trim();
    if (seg) parts.push(seg);
    buf = "";
  };
  // 按句末标点/换行切句（保留标点），超长句再按句内标点细分
  const sentences = text.split(/(?<=[。！？；!?;\n])/);
  for (const sentence of sentences) {
    if (!sentence.trim()) continue;
    if ((buf + sentence).length <= maxLen) {
      buf += sentence;
      continue;
    }
    pushBuf();
    if (sentence.length <= maxLen) {
      buf = sentence;
      continue;
    }
    // 超长句：按句内标点切分后逐段并入；单段仍超长时按 maxLen 硬切
    let sub = "";
    const flushSub = () => {
      const seg = sub.trim();
      if (seg) parts.push(seg);
      sub = "";
    };
    for (const piece of sentence.split(/(?<=[，,、：:—])/)) {
      if (!piece.trim()) continue;
      if (piece.length >= maxLen) {
        flushSub();
        for (let i = 0; i < piece.length; i += maxLen) {
          const seg = piece.slice(i, i + maxLen).trim();
          if (seg) parts.push(seg);
        }
        continue;
      }
      if ((sub + piece).length > maxLen) {
        flushSub();
        sub = piece;
      } else {
        sub += piece;
      }
    }
    buf = sub;
  }
  pushBuf();
  return parts;
}

export interface TtsPlayerCallbacks {
  /** 首段合成完成、即将开始播放（用于把 UI 置为“播放中”） */
  onStart?: () => void;
  /** 全部段落播放完毕 */
  onEnd?: () => void;
  /** 合成失败 */
  onError?: (message: string) => void;
}

/**
 * 长文本流式 TTS 播放器：切句后逐段「合成 → 播放」流水线化，
 * 播放当前段时预取下一段音频，显著降低首字节播放延迟与段间等待。
 */
export class TtsPlayer {
  /** 当前正在播放的实例（模块级唯一：同时只有一条消息在播放）。 */
  private static active: TtsPlayer | null = null;

  /** 停止当前正在播放的播放器。 */
  static stopActive(): void {
    TtsPlayer.active?.stop();
  }

  private segments: string[] = [];
  private index = 0;
  private stopped = true;
  private audio: HTMLAudioElement | null = null;
  private prefetch: Promise<Blob | null> | null = null;
  private callbacks: TtsPlayerCallbacks = {};

  /** 开始播放（自动停掉当前播放器）。 */
  async play(text: string, callbacks: TtsPlayerCallbacks = {}): Promise<void> {
    this.stop();
    TtsPlayer.active?.stop();
    TtsPlayer.active = this;

    this.callbacks = callbacks;
    this.segments = splitTtsText(text);
    this.index = 0;
    if (this.segments.length === 0) {
      callbacks.onEnd?.();
      return;
    }
    this.stopped = false;
    try {
      await this.runPipeline();
      if (!this.stopped) this.callbacks.onEnd?.();
    } catch (err) {
      if (!this.stopped) this.callbacks.onError?.(err instanceof Error ? err.message : "语音合成失败");
    }
  }

  /** 停止播放并取消预取。 */
  stop(): void {
    this.stopped = true;
    this.prefetch = null;
    this.callbacks = {};
    if (this.audio) {
      this.audio.pause();
      URL.revokeObjectURL(this.audio.src);
      this.audio = null;
    }
    if (TtsPlayer.active === this) TtsPlayer.active = null;
  }

  get isPlaying(): boolean {
    return !this.stopped;
  }

  private async runPipeline(): Promise<void> {
    while (this.index < this.segments.length) {
      if (this.stopped) return;
      const text = this.segments[this.index];
      this.index += 1;

      // 优先用预取结果，否则现合成首段
      let blob = this.prefetch ? await this.prefetch : null;
      this.prefetch = null;
      if (this.stopped) return;
      if (!blob) blob = await synthesizeSpeech(text);
      if (this.stopped) return;

      if (this.index === 1) this.callbacks.onStart?.();

      // 播放当前段的同时预取下一段
      const nextText = this.segments[this.index];
      if (nextText) {
        this.prefetch = synthesizeSpeech(nextText).catch(() => null);
      }

      await this.playSegment(blob);
    }
  }

  private playSegment(blob: Blob): Promise<void> {
    return new Promise((resolve) => {
      if (this.stopped) return resolve();
      const audio = new Audio(URL.createObjectURL(blob));
      this.audio = audio;
      const done = () => {
        URL.revokeObjectURL(audio.src);
        if (this.audio === audio) this.audio = null;
        resolve();
      };
      audio.addEventListener("ended", done);
      audio.addEventListener("error", done);
      void audio.play().catch(done);
    });
  }
}

// ---------------------------------------------------------------------------
// ASR：流式语音识别（麦克风 → WebSocket）
// ---------------------------------------------------------------------------

export interface AsrCallbacks {
  /** 收到识别结果（isFinal=true 表示本段最终结果） */
  onResult: (text: string, isFinal: boolean) => void;
  /** 连接/识别出错 */
  onError: (message: string) => void;
  /** 连接关闭（code 非 1000 视为异常） */
  onClose: (code: number) => void;
}

const TARGET_SAMPLE_RATE = 16000;

/** 计算 WebSocket 地址：显式配置 > API_BASE 同源 > 窗口地址（dev 下 next 不代理 WS，直连后端）。 */
function resolveWsBase(): string {
  const explicit = process.env.NEXT_PUBLIC_WS_BASE_URL;
  if (explicit) return explicit.replace(/\/$/, "");
  if (API_BASE) return API_BASE.replace(/^http/, "ws").replace(/\/$/, "");
  if (typeof window !== "undefined") {
    const { protocol, hostname, port } = window.location;
    const scheme = protocol === "https:" ? "wss" : "ws";
    // next dev 默认跑在 3000 端口且 rewrite 不代理 WebSocket → 直连后端 8000
    if (port === "3000") return `${scheme}://${hostname}:8000`;
    return `${scheme}://${hostname}${port ? `:${port}` : ""}`;
  }
  return "";
}

/** Float32 [-1,1] → int16 小端字节。 */
function floatToInt16(samples: Float32Array): Int16Array {
  const out = new Int16Array(samples.length);
  for (let i = 0; i < samples.length; i++) {
    const s = Math.max(-1, Math.min(1, samples[i]));
    out[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
  }
  return out;
}

export class AsrRecorder {
  private ws: WebSocket | null = null;
  private audioContext: AudioContext | null = null;
  private stream: MediaStream | null = null;
  private processor: ScriptProcessorNode | null = null;
  private source: MediaStreamAudioSourceNode | null = null;
  private callbacks: AsrCallbacks;
  private recording = false;
  private resamplePos = 0;

  constructor(callbacks: AsrCallbacks) {
    this.callbacks = callbacks;
  }

  get isRecording(): boolean {
    return this.recording;
  }

  /** 开始录音并连接识别服务。 */
  async start(): Promise<void> {
    if (this.recording) return;
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
    });

    const ws = new WebSocket(`${resolveWsBase()}/ws/asr`);
    ws.binaryType = "arraybuffer";
    this.ws = ws;

    ws.onmessage = (ev) => {
      if (typeof ev.data !== "string") return;
      try {
        const data = JSON.parse(ev.data) as { text?: string; is_final?: boolean };
        const text = data.text || "";
        if (text) this.callbacks.onResult(text, Boolean(data.is_final));
      } catch {
        /* 忽略非 JSON 下行 */
      }
    };
    ws.onerror = () => this.callbacks.onError("语音识别服务连接失败");
    ws.onclose = (ev) => {
      this.cleanupAudio();
      this.recording = false;
      if (ev.code !== 1000) {
        const reasonMap: Record<number, string> = {
          4401: "API Key 无效",
          4404: "模型不存在",
          4409: "模型未启动",
          4502: "语音识别服务不可达",
          4503: "语音服务未配置",
        };
        this.callbacks.onError(reasonMap[ev.code] || `连接关闭（${ev.code}）`);
      }
      this.callbacks.onClose(ev.code);
    };

    await new Promise<void>((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error("连接识别服务超时")), 10000);
      ws.onopen = () => {
        clearTimeout(timer);
        // sherpa-onnx 握手：告知客户端将发送的采样率
        ws.send(JSON.stringify({ ExpectedSampleRate: TARGET_SAMPLE_RATE }));
        resolve();
      };
      ws.onerror = () => {
        clearTimeout(timer);
        reject(new Error("语音识别服务连接失败"));
      };
    });

    this.startAudioCapture();
    this.recording = true;
  }

  /** 停止录音并断开识别服务。 */
  stop(): void {
    if (!this.recording) return;
    this.recording = false;
    this.cleanupAudio();
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.close(1000, "client stop");
    }
    this.ws = null;
  }

  private startAudioCapture(): void {
    const stream = this.stream!;
    const ctx = new AudioContext();
    this.audioContext = ctx;
    this.source = ctx.createMediaStreamSource(stream);
    // ScriptProcessorNode 虽被标记弃用，但兼容性最好且无需额外 worklet 文件
    this.processor = ctx.createScriptProcessor(4096, 1, 1);
    this.resamplePos = 0;
    this.processor.onaudioprocess = (e) => this.onAudioProcess(ctx, e);
    this.source.connect(this.processor);
    this.processor.connect(ctx.destination);
  }

  private onAudioProcess(ctx: AudioContext, e: AudioProcessingEvent): void {
    if (!this.recording || !this.ws || this.ws.readyState !== WebSocket.OPEN) return;
    const input = e.inputBuffer.getChannelData(0);
    const inputRate = ctx.sampleRate;
    if (inputRate === TARGET_SAMPLE_RATE) {
      this.sendPcm(input);
      return;
    }
    // 线性插值降采样到 16kHz（resamplePos 为跨块的连续小数游标，避免边界不连续）
    const ratio = inputRate / TARGET_SAMPLE_RATE;
    const out: number[] = [];
    let pos = this.resamplePos;
    while (pos < input.length - 1) {
      const idx = Math.floor(pos);
      const frac = pos - idx;
      out.push(input[idx] + (input[idx + 1] - input[idx]) * frac);
      pos += ratio;
    }
    this.resamplePos = pos - input.length;
    this.sendPcm(Float32Array.from(out));
  }

  private sendPcm(samples: Float32Array): void {
    const int16 = floatToInt16(samples);
    this.ws?.send(int16.buffer);
  }

  private cleanupAudio(): void {
    this.processor?.disconnect();
    this.source?.disconnect();
    this.processor = null;
    this.source = null;
    this.stream?.getTracks().forEach((t) => t.stop());
    this.stream = null;
    void this.audioContext?.close();
    this.audioContext = null;
  }
}
