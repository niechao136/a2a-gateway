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

/** 当前正在播放的音频（模块级单例：同时只播放一段）。 */
let playingAudio: HTMLAudioElement | null = null;

/** 播放 WAV Blob；返回 audio 元素（可手动 stop）。再次调用会停止上一次播放。 */
export function playBlob(blob: Blob): HTMLAudioElement {
  stopPlaying();
  const audio = new Audio(URL.createObjectURL(blob));
  playingAudio = audio;
  audio.addEventListener("ended", () => {
    if (playingAudio === audio) playingAudio = null;
    URL.revokeObjectURL(audio.src);
  });
  void audio.play();
  return audio;
}

/** 停止当前 TTS 播放。 */
export function stopPlaying(): void {
  if (playingAudio) {
    playingAudio.pause();
    URL.revokeObjectURL(playingAudio.src);
    playingAudio = null;
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
