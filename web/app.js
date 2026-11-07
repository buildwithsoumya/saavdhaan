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

// access token: opened once as /?t=<token>, then remembered on this device
(() => {
  const url = new URL(location.href);
  const t = url.searchParams.get("t");
  if (t) {
    store.set("token", t);
    url.searchParams.delete("t");
    history.replaceState(null, "", url);
  }
})();
const withToken = (path) => `${path}${path.includes("?") ? "&" : "?"}token=${encodeURIComponent(store.get("token", ""))}`;
const api = (path, opts) => fetch(withToken(path), opts);

let ws = null, ctx = null, mic = null, node = null, wakeLock = null;
let listening = false, callId = null, retry = 0;
let muted = false, lastSpeechEnd = null, failures = 0, alertPending = false, level = 0, honeypotOn = false;
const queue = []; // alert audio plays one clip at a time
let playing = false;

// ---- settings -------------------------------------------------------------
async function loadVoices() {
  let family = [];
  try {
    const r = await api("/api/voices");
    if (r.ok) family = await r.json();
  } catch {}
  const voice = $("voice");
  voice.innerHTML = family.map((f) => `<option value="family:${f.id}">परिवार · Family: ${esc(f.name)}</option>`).join("")
    + VOICES.map((v) => `<option>${v}</option>`).join("");
  const saved = store.get("voice", "Shivani");
  voice.value = [...voice.options].some((o) => o.value === saved) ? saved : "Shivani";
  // honeypot: only voices whose owner allowed them to talk to scam callers
  const hp = $("hp-voice");
  hp.innerHTML = `<option value="">सामान्य आवाज़ · Neutral voice</option>`
    + family.filter((f) => f.honeypot).map((f) => `<option value="family:${f.id}">मेरी आवाज़ · My voice: ${esc(f.name)}</option>`).join("");
  const hpSaved = store.get("hp-voice", "");
  hp.value = [...hp.options].some((o) => o.value === hpSaved) ? hpSaved : "";
  $("voices").innerHTML = family.map((f) => `<li><span>${esc(f.name)}</span>
    <button class="btn btn-quiet" type="button" data-del="${f.id}">हटाएँ · Delete</button></li>`).join("");
}

// ---- family voice enrolment ----------------------------------------------
let clip = null, recorder = null;
function setClip(blob, label) {
  clip = blob;
  $("enrol-clip").textContent = label;
  $("enrol-save").disabled = !(clip && $("enrol-consent").checked);
}
async function recordClip() {
  if (recorder) return recorder.state === "recording" && recorder.stop();
  let stream;
  try { stream = await navigator.mediaDevices.getUserMedia({ audio: true }); }
  catch (err) { return ($("enrol-status").textContent = `माइक नहीं मिला · Microphone blocked: ${err.message}`); }
  const chunks = [], started = Date.now();
  try {
    recorder = new MediaRecorder(stream);
  } catch (err) {
    stream.getTracks().forEach((t) => t.stop());
    return ($("enrol-status").textContent = `रिकॉर्डिंग नहीं हो सकती · Recording not supported: ${err.message}`);
  }
  recorder.ondataavailable = (e) => chunks.push(e.data);
  const tick = setInterval(() => {
    const s = Math.round((Date.now() - started) / 1000);
    $("enrol-clip").textContent = `रिकॉर्डिंग… ${s}s · Recording… press again to stop`;
    if (s >= 20 && recorder?.state === "recording") recorder.stop();
  }, 250);
  recorder.onstop = () => {
    clearInterval(tick);
    const secs = Math.round((Date.now() - started) / 1000);
    if (secs < 5) $("enrol-status").textContent = "कम से कम 5 सेकंड बोलें · Please speak for at least 5 seconds";
    stream.getTracks().forEach((t) => t.stop());
    setClip(new Blob(chunks, { type: recorder.mimeType }), `${secs}s रिकॉर्ड हुआ · recorded`);
    recorder = null;
    $("enrol-rec").textContent = "दोबारा रिकॉर्ड करें · Record again";
  };
  recorder.start();
  $("enrol-rec").textContent = "रोकें · Stop";
}
async function saveVoice() {
  const form = new FormData();
  form.append("audio", clip, "reference");
  form.append("name", $("enrol-name").value);
  form.append("consent", $("enrol-consent").checked);
  form.append("honeypot", $("enrol-hp").checked);
  $("enrol-save").disabled = true;
  $("enrol-status").textContent = "आवाज़ बना रहे हैं… · Creating voice…";
  try {
    const r = await api("/api/voices", { method: "POST", body: form });
    const body = await r.json();
    if (!r.ok) throw new Error(body.detail || r.statusText);
    $("enrol-status").textContent = `${body.name} की आवाज़ जुड़ गई · voice added`;
    store.set("voice", `family:${body.id}`);
    if (body.honeypot) store.set("hp-voice", `family:${body.id}`);
    $("enrol-name").value = "";
    $("enrol-hp").checked = false;
    setClip(null, "कोई रिकॉर्डिंग नहीं · No recording yet");
    await loadVoices();
  } catch (err) {
    $("enrol-status").textContent = `नहीं हो पाया · Could not add voice: ${reason(err)}`;
  } finally {
    $("enrol-save").disabled = !(clip && $("enrol-consent").checked);
  }
}

function initSettings() {
  const voice = $("voice");
  loadVoices();
  voice.onchange = () => store.set("voice", voice.value);
  $("hp-voice").onchange = (e) => store.set("hp-voice", e.target.value);
  $("enrol-rec").onclick = recordClip;
  $("enrol-file").onchange = (e) => e.target.files[0] && setClip(e.target.files[0], e.target.files[0].name);
  $("enrol-consent").onchange = () => setClip(clip, $("enrol-clip").textContent);
  $("enrol-save").onclick = saveVoice;
  $("voices").onclick = async (e) => {
    const id = e.target.dataset.del;
    if (!id) return;
    await api(`/api/voices/${id}`, { method: "DELETE" });
    if (store.get("voice", "") === `family:${id}`) store.set("voice", "Shivani");
    if (store.get("hp-voice", "") === `family:${id}`) store.set("hp-voice", "");
    loadVoices();
  };
  $("family").value = store.get("family", "");
  $("family").oninput = (e) => store.set("family", e.target.value);
  $("hp-allow").checked = store.get("hp-allow", "0") === "1";
  $("hp-allow").onchange = (e) => { store.set("hp-allow", e.target.checked ? "1" : "0"); renderHoneypot(); };
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

function renderLevel(lvl) {
  const L = LEVELS[lvl];
  level = lvl;
  if (document.body.dataset.level !== String(lvl)) {
    document.body.classList.remove("stamped");
    void document.body.offsetWidth; // restart the stamp animation
    document.body.classList.add("stamped");
  }
  document.body.dataset.level = lvl;
  $("status-hi").textContent = L.hi;
  $("status-en").textContent = L.en;
  $("status-level").textContent = `L${lvl}`;
  $("advice").innerHTML = `<span lang="hi">${L.advice[0]}</span> <span lang="en">${L.advice[1]}</span>`;
  $("helpline").hidden = lvl < 3;
  const family = store.get("family", "").trim();
  $("family-line").hidden = !family;
  $("family-line").textContent = family ? `या परिवार को फ़ोन करें · Or call family: ${family}` : "";
  renderHoneypot();
}

function renderHoneypot() {
  const allowed = store.get("hp-allow", "0") === "1";
  const done = !$("hp-done").hidden;
  // the proof stays on screen after the elder stops listening and hangs up
  $("honeypot").hidden = !(allowed && ((listening && (level >= 2 || honeypotOn)) || done));
  $("hp-offer").hidden = honeypotOn || done;
  $("hp-live").hidden = !honeypotOn;
}

const STAGE_HI = Object.fromEntries(STAGES.map(([id, hi]) => [id, hi]));
function renderProof(items, facts = []) {
  $("hp-facts").innerHTML = facts.map((f) => `<div><dt>${esc(f.label)}</dt><dd>${esc(f.value)}</dd>${f.said ? `<small>“${esc(f.said)}”</small>` : ""}</div>`).join("");
  $("hp-proof-list").innerHTML = items.map((p) =>
    `<li><b>${STAGE_HI[p.stage] || ""} · ${esc(p.name)}: “${esc(p.phrase)}”</b>${esc(p.said)}</li>`).join("");
  $("hp-proof").hidden = items.length === 0 && facts.length === 0;
}

function addLine(text, lang, persona = false) {
  const lines = $("lines");
  lines.querySelector(".empty")?.remove();
  const li = document.createElement("li");
  li.textContent = persona ? `Saavdhaan: ${text}` : text;
  if (persona) li.className = "persona";
  if (lang) li.lang = lang.split("-")[0];
  lines.append(li);
  while (lines.children.length > 40) lines.firstChild.remove();
  li.scrollIntoView({ block: "nearest" });
}

const esc = (s) => s.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const conn = (t) => ($("conn").textContent = t);
// fetch() rejects with a TypeError ("Failed to fetch") only when the server can't be reached at all
const reason = (err) => err instanceof TypeError
  ? "लैपटॉप से नहीं जुड़ पाया · Cannot reach the laptop. Is python -m app.serve running and the phone on the same Wi-Fi?"
  : err.message;

// ---- alerts ---------------------------------------------------------------
function playAlert(bytes) {
  bytes.isAlert = alertPending; // persona replies play too, but only alerts count for latency
  alertPending = false;
  queue.push(bytes);
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
  const isAlert = bytes.isAlert;
  muted = !!isAlert; // persona speech stays audible to STT after echo cancellation
  audio.addEventListener("playing", () => {
    if (isAlert && lastSpeechEnd != null) $("latency").textContent = `${Math.round(performance.now() - lastSpeechEnd)} ms`;
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

// A phone that turns its screen off stops the mic; keep it awake while listening.
async function keepAwake(on) {
  try {
    if (on) {
      if ("wakeLock" in navigator && !(wakeLock && !wakeLock.released)) wakeLock = await navigator.wakeLock.request("screen");
    } else {
      await wakeLock?.release();
      wakeLock = null;
    }
  } catch {}
}
document.addEventListener("visibilitychange", () => listening && document.visibilityState === "visible" && keepAwake(true));

async function startAudio() {
  mic = await navigator.mediaDevices.getUserMedia({
    // Keep listening through persona speech; browser echo cancellation suppresses
    // this device's own playback while the caller can still interrupt.
    audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
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
  keepAwake(true);
}

function stopAudio() {
  keepAwake(false);
  mic?.getTracks().forEach((t) => t.stop());
  ctx?.close();
  mic = ctx = node = null;
}

// ---- socket ---------------------------------------------------------------
function connect() {
  const q = new URLSearchParams({ call: callId, voice: store.get("voice", "Shivani"), record: store.get("record", "0"),
    token: store.get("token", ""), hpvoice: store.get("hp-voice", ""),
    silent: store.get("hp-allow", "0") }); // honeypot allowed: warnings on screen only, never spoken into the call
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
    } else if (m.type === "alert" && !m.silent) alertPending = true;
    else if (m.type === "say") addLine(m.text, null, true);
    else if (m.type === "honeypot") {
      honeypotOn = m.on;
      $("hp-done").hidden = m.reason !== "evidence";
      if (m.proof) renderProof(m.proof, m.facts);
      renderHoneypot();
    } else if (m.type === "error") conn(`गड़बड़ · Problem: ${m.message}`);
  };
  sock.onclose = (e) => {
    if (sock === ws && honeypotOn) { // the server ends honeypot mode when the socket drops
      honeypotOn = false;
      renderHoneypot();
    }
    if (e.code === 4401 && sock === ws) {
      if (listening) toggle();
      conn("ऐप का लिंक गलत है · Wrong app link: open the link with ?t= once");
      return;
    }
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
    $("report-row").hidden = store.get("record", "0") !== "1";
    $("report-link").hidden = true;
    $("report").disabled = false;
    $("report").textContent = "कॉल की रिपोर्ट बनाएँ · Make call report";
  } else {
    $("report-row").hidden = true;
    try {
      await startAudio();
    } catch (err) {
      conn(`माइक नहीं मिला · Microphone blocked: ${err.message}`);
      return;
    }
    listening = true;
    callId = crypto.randomUUID();
    honeypotOn = false;
    $("hp-done").hidden = true;
    $("hp-proof").hidden = true;
    failures = 0;
    $("lines").innerHTML = "";
    $("latency").textContent = "—";
    connect();
  }
  document.body.dataset.listening = listening;
  renderHoneypot();
  btn.setAttribute("aria-pressed", listening);
  $("listen-label").textContent = listening ? "सुनना बंद करें · Stop listening" : "सुनना शुरू करें · Start listening";
}

async function makeReport() {
  const btn = $("report");
  btn.disabled = true;
  btn.textContent = "रिपोर्ट बन रही है (1–2 मिनट)… · Making report…";
  try {
    let r;
    for (let attempt = 0; attempt < 3; attempt++) { // the recording is saved as the call closes
      r = await api(`/api/report/${callId}`, { method: "POST" });
      if (r.status !== 404) break;
      await new Promise((ok) => setTimeout(ok, 1500));
    }
    const body = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(body.detail || r.statusText);
    $("report-link").href = withToken(body.url);
    $("report-link").hidden = false;
    btn.textContent = "रिपोर्ट तैयार · Report ready";
  } catch (err) {
    btn.disabled = false;
    btn.textContent = "दोबारा कोशिश करें · Try again";
    conn(`रिपोर्ट नहीं बनी · Report failed: ${reason(err)}`);
  }
}

initSettings();
if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("sw.js").catch((err) => console.warn("offline app shell unavailable:", err));
}
$("report").onclick = makeReport;
$("hp-start").onclick = () => ws?.send(JSON.stringify({ type: "honeypot", on: true }));
$("hp-stop").onclick = () => ws?.send(JSON.stringify({ type: "honeypot", on: false }));
renderStages(null);
renderLevel(0);
$("listen").onclick = toggle;
