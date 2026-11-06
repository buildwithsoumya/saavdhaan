// Saavdhaan page: mic -> 16 kHz int16 PCM -> backend WS; renders tracker state; plays alerts.
const $ = (id) => document.getElementById(id);

const STAGES = [
  ["S1", "अधिकार", "Authority"],
  ["S2", "डर", "Fear"],
  ["S3", "अकेला करना", "Isolation"],
  ["S4", "जल्दी", "Urgency"],
  ["S5", "पैसा माँगना", "Money ask"],
];
const LEVELS = [
  { hi: "सुरक्षित", en: "Safe", advice: ["बात जारी रखें।", "You can keep talking."] },
  { hi: "ध्यान दें", en: "Caution", advice: ["ध्यान से सुनें। कुछ भी न भेजें।", "Listen carefully. Do not send anything."] },
  { hi: "सावधान", en: "Saavdhaan", advice: ["यह धोखा लगता है। फ़ोन काट दें।", "This looks like a scam. Hang up."] },
  { hi: "रुकिए", en: "Stop", advice: ["धोखा है! पैसा न भेजें, फ़ोन काटें।", "Scam! Send no money. Hang up."] },
];
const VOICES = ["Shivani", "Lakshmi", "Kalpana", "Chetana", "Vasudha", "Riyaa", "Tarika",
  "Avinash", "Shubham", "Prakash", "Devansh", "Yashraj", "Harshad"];
const LANGS = { hi: "हिंदी", en: "English", ta: "தமிழ்", bn: "বাংলা", kn: "ಕನ್ನಡ", te: "తెలుగు",
  mr: "मराठी", gu: "ગુજરાતી", ml: "മലയാളം", pa: "ਪੰਜਾਬੀ" };

const store = {
  get(k, d) { try { return localStorage.getItem(k) ?? d; } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch {} },
};

let ws = null, ctx = null, mic = null, node = null;
let listening = false, callId = null, retry = 0;
let muted = false, lastSpeechEnd = null, failures = 0;
const queue = []; // alert audio plays one clip at a time
let playing = false;

// ---- settings -------------------------------------------------------------
function initSettings() {
  const voice = $("voice");
  voice.innerHTML = VOICES.map((v) => `<option>${v}</option>`).join("");
  voice.value = store.get("voice", "Shivani");
  voice.onchange = () => store.set("voice", voice.value);
  $("family").value = store.get("family", "");
  $("family").oninput = (e) => store.set("family", e.target.value);
  $("record").checked = store.get("record", "0") === "1";
  $("record").onchange = (e) => store.set("record", e.target.checked ? "1" : "0");
}

// ---- rendering ------------------------------------------------------------
function renderStages(explain) {
  const hit = explain?.stages || {};
  $("stages").innerHTML = STAGES.map(([id, hi, en], i) => {
    const phrases = [...new Set((hit[id] || []).map((h) => h.phrase))];
    const on = phrases.length > 0;
    const heard = on ? `<span class="heard">“${phrases.slice(0, 3).map(esc).join("”, “")}”</span>`
                     : `<span class="heard none">अभी नहीं · not yet</span>`;
    return `<li class="stage${on ? " on" : ""}">
      <span class="stage-no">${i + 1}</span>
      <span class="stage-name"><span lang="hi">${hi}</span> <span lang="en">${en}</span></span>
      <span class="sr">${on ? "heard" : "not heard"}</span>
      ${heard}</li>`;
  }).join("");
}

function renderLevel(level) {
  const L = LEVELS[level];
  if (document.body.dataset.level !== String(level)) {
    document.body.classList.remove("stamped");
    void document.body.offsetWidth; // restart the stamp animation
    document.body.classList.add("stamped");
  }
  document.body.dataset.level = level;
  $("status-hi").textContent = L.hi;
  $("status-en").textContent = L.en;
  $("status-level").textContent = `L${level}`;
  $("advice").innerHTML = `<span lang="hi">${L.advice[0]}</span> <span lang="en">${L.advice[1]}</span>`;
  $("helpline").hidden = level < 3;
  const family = store.get("family", "").trim();
  $("family-line").hidden = !family;
  $("family-line").textContent = family ? `या परिवार को फ़ोन करें · Or call family: ${family}` : "";
}

function addLine(text, lang) {
  const lines = $("lines");
  lines.querySelector(".empty")?.remove();
  const li = document.createElement("li");
  li.textContent = text;
  if (lang) li.lang = lang.split("-")[0];
  lines.append(li);
  while (lines.children.length > 40) lines.firstChild.remove();
  li.scrollIntoView({ block: "nearest" });
}

const esc = (s) => s.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const conn = (t) => ($("conn").textContent = t);

// ---- alerts ---------------------------------------------------------------
function playAlert(bytes) {
  queue.push(bytes);
  muted = true; // don't let our own warning be transcribed back into the tracker
  if (!playing) playNext();
}

function playNext() {
  const bytes = queue.shift();
  if (!bytes) {
    playing = false;
    setTimeout(() => { if (!playing) muted = false; }, 400);
    return;
  }
  playing = true;
  const url = URL.createObjectURL(new Blob([bytes], { type: "audio/wav" }));
  const audio = new Audio(url);
  audio.addEventListener("playing", () => {
    if (lastSpeechEnd != null) $("latency").textContent = `${Math.round(performance.now() - lastSpeechEnd)} ms`;
  }, { once: true });
  let finished = false;
  const done = () => {
    if (finished) return;
    finished = true;
    URL.revokeObjectURL(url);
    playNext();
  };
  audio.addEventListener("ended", done, { once: true });
  audio.addEventListener("error", done, { once: true });
  audio.play().catch(done);
}

// ---- audio capture --------------------------------------------------------
// Runs in the audio thread: box-filter downsample to 16 kHz, int16, 512-sample frames.
const WORKLET = `
class Pcm16k extends AudioWorkletProcessor {
  constructor() { super(); this.ratio = sampleRate / 16000; this.acc = 0; this.n = 0; this.pos = 0;
    this.buf = new Int16Array(512); this.i = 0; }
  process(inputs) {
    const ch = inputs[0][0];
    if (!ch) return true;
    for (let k = 0; k < ch.length; k++) {
      this.acc += ch[k]; this.n++; this.pos++;
      if (this.pos >= this.ratio) {
        this.pos -= this.ratio;
        const s = Math.max(-1, Math.min(1, this.acc / this.n));
        this.buf[this.i++] = s < 0 ? s * 0x8000 : s * 0x7fff;
        this.acc = 0; this.n = 0;
        if (this.i === 512) { this.port.postMessage(this.buf.buffer, [this.buf.buffer]); this.buf = new Int16Array(512); this.i = 0; }
      }
    }
    return true;
  }
}
registerProcessor("pcm16k", Pcm16k);`;

async function startAudio() {
  mic = await navigator.mediaDevices.getUserMedia({
    audio: { channelCount: 1, echoCancellation: false, noiseSuppression: false, autoGainControl: true },
  });
  ctx = new AudioContext();
  const url = URL.createObjectURL(new Blob([WORKLET], { type: "application/javascript" }));
  await ctx.audioWorklet.addModule(url);
  node = new AudioWorkletNode(ctx, "pcm16k");
  node.port.onmessage = (e) => {
    if (ws?.readyState !== WebSocket.OPEN) return;
    ws.send(muted ? new ArrayBuffer(1024) : e.data);
  };
  ctx.createMediaStreamSource(mic).connect(node);
}

function stopAudio() {
  mic?.getTracks().forEach((t) => t.stop());
  ctx?.close();
  mic = ctx = node = null;
}

// ---- socket ---------------------------------------------------------------
function connect() {
  const q = new URLSearchParams({ call: callId, voice: store.get("voice", "Shivani"), record: store.get("record", "0") });
  const sock = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws?${q}`);
  ws = sock;
  sock.binaryType = "arraybuffer";
  conn("जुड़ रहा है… · Connecting…");
  sock.onmessage = (e) => {
    if (e.data instanceof ArrayBuffer) return playAlert(e.data);
    const m = JSON.parse(e.data);
    if (m.type === "ready") { retry = 0; failures = 0; conn("सुन रहा है · Listening"); renderStages(m.explain); renderLevel(m.explain.level); }
    else if (m.type === "speech_end") lastSpeechEnd = performance.now();
    else if (m.type === "transcript") {
      addLine(m.text, m.lang);
      if (m.lang) $("lang").textContent = LANGS[m.lang.split("-")[0]] || m.lang;
      renderStages(m.explain);
      renderLevel(m.explain.level);
    } else if (m.type === "error") conn(`गड़बड़ · Problem: ${m.message}`);
  };
  sock.onclose = () => {
    if (!listening || sock !== ws) return; // an old socket closing after stop/start
    if (++failures > 5) {
      toggle(); // stop first: toggle() resets the status line
      conn("सर्वर से नहीं जुड़ पा रहा · Cannot reach the server. Check the laptop and try again.");
      return;
    }
    retry = Math.min(retry + 1, 5);
    conn("दोबारा जुड़ रहा है… · Reconnecting…");
    setTimeout(() => listening && connect(), 500 * retry);
  };
}

async function toggle() {
  const btn = $("listen");
  if (listening) {
    listening = false;
    ws?.send(JSON.stringify({ type: "stop" }));
    ws?.close();
    stopAudio();
    conn("सुन नहीं रहा · Not listening");
  } else {
    try {
      await startAudio();
    } catch (err) {
      conn(`माइक नहीं मिला · Microphone blocked: ${err.message}`);
      return;
    }
    listening = true;
    callId = crypto.randomUUID();
    failures = 0;
    $("lines").innerHTML = "";
    $("latency").textContent = "—";
    connect();
  }
  document.body.dataset.listening = listening;
  btn.setAttribute("aria-pressed", listening);
  $("listen-label").textContent = listening ? "सुनना बंद करें · Stop listening" : "सुनना शुरू करें · Start listening";
}

initSettings();
renderStages(null);
renderLevel(0);
$("listen").onclick = toggle;
