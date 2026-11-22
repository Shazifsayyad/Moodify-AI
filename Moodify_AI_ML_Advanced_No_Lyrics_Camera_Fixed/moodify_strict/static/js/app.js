const $ = id => document.getElementById(id);
const audio = $("audio");
const userId = (()=>{let id=localStorage.getItem("moodify_user_id");if(!id){id="u_"+Math.random().toString(36).slice(2)+Date.now().toString(36);localStorage.setItem("moodify_user_id",id)}return id})();
let assistantMood = null;
let songs = [], queue = [], currentIndex = -1, currentMood = "neutral", cameraStream = null, scanTimer = null, modelsReady = false, scanInProgress = false;
let libraries = [], selectedLibraryId = null;
let favoriteFiles = new Set();
let pendingMood = null, pendingMoodCount = 0, activeAutoMood = null, detectionCompleted = false, cameraManuallyStopped = false;
let cameraStartedAt = 0;
const MOOD_CONFIRM_FRAMES = 3; // require three consistent readings before choosing a mood
const MIN_SCAN_DURATION_MS = 1400; // allow three readings at the 700 ms scan interval
const MIN_EMOTION_CONFIDENCE = 0.35;
const SCAN_INTERVAL_MS = 700;
const moodEmoji = {happy:"☀️",sad:"🌧️",angry:"😠",neutral:"☁️",surprise:"✨",fear:"🌿"};
function toast(msg){const t=$("toast");t.textContent=msg;t.classList.add("show");setTimeout(()=>t.classList.remove("show"),2800)}
function esc(s){return String(s).replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]))}
async function api(url,opts={}){const r=await fetch(url,opts);if(!r.ok){const x=await r.json().catch(()=>({}));throw Error(x.error||"Request failed")}return r.json()}
async function loadSongs(){songs=await api("/api/songs");}
async function loadLibraries(){libraries=await api("/api/libraries");renderLibraries();}
function renderLibraries(){
  $("libraryGrid").innerHTML=libraries.map(lib=>`<button class="library-card" data-library="${lib.id}"><span class="library-emoji">${esc(lib.emoji)}</span><span class="library-card-copy"><b>${esc(lib.name)}</b><small>${esc(lib.description||"Your music collection")}</small><small class="library-count">${lib.song_count} ${lib.song_count===1?"song":"songs"}</small></span><span class="library-arrow">→</span></button>`).join("");
}
function openLibrary(id){const lib=libraries.find(x=>x.id===Number(id));if(!lib)return;selectedLibraryId=lib.id;$("libraryOverview").hidden=true;$("libraryDetail").hidden=false;$("selectedLibraryTitle").textContent=lib.emoji+" "+lib.name;$("selectedLibraryDescription").textContent=lib.description||"Your music collection";$("selectedLibraryKicker").textContent=lib.name.toUpperCase()+" COLLECTION";$("deleteLibraryBtn").hidden=!!lib.is_default;$("deleteLibraryBtn").disabled=lib.song_count>0;$("deleteLibraryBtn").title=lib.song_count>0?"Only empty custom libraries can be deleted":"Delete this empty library";renderLibrarySongs();}
function closeLibrary(){selectedLibraryId=null;$("libraryDetail").hidden=true;$("libraryOverview").hidden=false;}
function renderSongs(list,el,allowDelete=false){el.innerHTML=list.length?list.map(s=>{const saved=favoriteFiles.has(s.filename);return `<div class="song-row"><div class="track-art">♫</div><div class="track-info"><b>${esc(s.title)}</b><small>${esc((s.type||"audio").toUpperCase())} · ${esc(s.library_name||"Music library")} · ${s.duration?Math.round(s.duration)+"s · ":""}${s.feature_source==="measured WAV signal"?"Signal energy":"File estimate"} ${Math.round((s.energy||.5)*100)}%${s.feature_source==="measured WAV signal"?" · ~"+Math.round(s.tempo||100)+" BPM":""}</small></div><button class="track-play" data-play="${esc(s.filename)}">▶</button><button class="track-heart ${saved?"is-favorite":""}" data-fav="${esc(s.filename)}" aria-pressed="${saved}" title="${saved?"Remove from favorites":"Add to favorites"}">${saved?"♥":"♡"}</button><button class="feedback-btn" data-feedback="1" data-file="${esc(s.filename)}" data-feedback-mood="${esc((s.library_name||currentMood).toLowerCase())}" title="I like this">👍</button><button class="feedback-btn" data-feedback="-1" data-file="${esc(s.filename)}" data-feedback-mood="${esc((s.library_name||currentMood).toLowerCase())}" title="Not for me">👎</button>${allowDelete?`<button class="track-delete" data-delete-song="${esc(s.filename)}" title="Delete song" aria-label="Delete ${esc(s.title)}">✕</button>`:""}</div>`}).join(""):'<div class="empty">No songs in this library yet. Use “Upload songs” to add your music.</div>'}
async function renderLibrarySongs(){const list=await api(`/api/songs?library_id=${selectedLibraryId}`);renderSongs(list,$("selectedLibrarySongs"),true);}
async function deleteSong(filename){
  const song=songs.find(x=>x.filename===filename)||queue.find(x=>x.filename===filename);
  if(!confirm(`Delete "${song?.title||filename}" from this library and permanently remove its audio file?`))return;
  try{
    await api("/api/songs",{method:"DELETE",headers:{"Content-Type":"application/json"},body:JSON.stringify({filename})});
    if(audio.src&&audio.src.includes(encodeURIComponent(filename))){audio.pause();audio.removeAttribute("src");audio.load();$("nowTitle").textContent="Nothing playing";$("nowMood").textContent="Song deleted";$("playBtn").textContent="▶";}
    queue=queue.filter(x=>x.filename!==filename);currentIndex=queue.length?Math.min(currentIndex,queue.length-1):-1;
    await loadSongs();await loadLibraries();if(selectedLibraryId)await renderLibrarySongs();
    if($("view-favorites").classList.contains("active"))await loadFavorites();
    toast("Song deleted successfully");
  }catch(e){toast(e.message)}
}
function syncFavoriteButtons(filename){const saved=favoriteFiles.has(filename);document.querySelectorAll(`[data-fav="${CSS.escape(filename)}"]`).forEach(btn=>{btn.classList.toggle("is-favorite",saved);btn.textContent=saved?"♥":"♡";btn.setAttribute("aria-pressed",String(saved));btn.title=saved?"Remove from favorites":"Add to favorites"});const player=$("nowFavorite");if(player.dataset.filename===filename){player.classList.toggle("is-favorite",saved);player.textContent=saved?"♥":"♡";player.title=saved?"Remove from favorites":"Add to favorites";player.setAttribute("aria-pressed",String(saved));}}
function playSong(filename){let idx=queue.findIndex(s=>s.filename===filename);if(idx<0)return;currentIndex=idx;const s=queue[idx];audio.src=s.url;audio.play().catch(()=>toast("Tap play to start audio."));$("nowTitle").textContent=s.title;$("nowMood").textContent=`${s.library_name||currentMood+" mood mix"}`;$("playBtn").textContent="Ⅱ";$("nowFavorite").dataset.filename=s.filename;syncFavoriteButtons(s.filename);}
function playNext(dir=1){if(!queue.length)return;currentIndex=(currentIndex+dir+queue.length)%queue.length;playSong(queue[currentIndex].filename)}
document.addEventListener("click",e=>{const fb=e.target.closest("[data-feedback]");if(fb){sendFeedback(fb.dataset.file,Number(fb.dataset.feedback),fb.dataset.feedbackMood);return}const mood=e.target.closest("[data-mood]");if(mood)recommend(mood.dataset.mood);const p=e.target.closest("[data-play]");if(p){if($("libraryDetail")&&!$("libraryDetail").hidden&&selectedLibraryId){api(`/api/songs?library_id=${selectedLibraryId}`).then(list=>{queue=list;playSong(p.dataset.play)}).catch(err=>toast(err.message))}else if($("view-favorites").classList.contains("active")){api("/api/favorites").then(async fav=>{const all=await api("/api/songs");queue=fav.map(f=>all.find(s=>s.filename===f.filename)).filter(Boolean);playSong(p.dataset.play)}).catch(err=>toast(err.message))}else playSong(p.dataset.play)}const d=e.target.closest("[data-delete-song]");if(d)deleteSong(d.dataset.deleteSong);const f=e.target.closest("[data-fav]");if(f)toggleFavorite(f.dataset.fav);const card=e.target.closest("[data-library]");if(card)openLibrary(card.dataset.library);});
async function recommend(mood,confidence=1){
  currentMood=mood;
  activeAutoMood=mood;
  document.querySelectorAll(".mood-card").forEach(b=>b.classList.toggle("selected",b.dataset.mood===mood));
  // Option A: stop the previous track immediately before loading the new mood.
  audio.pause(); audio.currentTime=0; audio.removeAttribute("src"); audio.load();
  queue=[]; currentIndex=-1;
  $("playBtn").textContent="▶";
  $("nowTitle").textContent="Finding a matching song…";
  $("nowMood").textContent=`${mood[0].toUpperCase()+mood.slice(1)} library only`;
  const data=await api("/api/recommend",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({mood,confidence,user_id:userId})});
  // Shuffle only the songs returned for this exact mood library.
  queue=[...data.songs];
  if(!data.ml_personalized){for(let i=queue.length-1;i>0;i--){const j=Math.floor(Math.random()*(i+1));[queue[i],queue[j]]=[queue[j],queue[i]];}}
  currentIndex=-1;
  $("recommendTitle").textContent=`${moodEmoji[mood]||"♫"} ${mood[0].toUpperCase()+mood.slice(1)} mood mix`;
  $("recommendMeta").textContent=`${data.confidence}% confidence · ${data.library} only · ${data.model}`;
  renderSongs(queue,$("recommendations"));
  if(queue.length){playSong(queue[0].filename);toast(`${data.message} Starting playback.`)}
  else {$("nowTitle").textContent="Nothing playing";$("nowMood").textContent=`No songs in ${mood} library` ;toast(data.message)}
  loadHistory();
}
async function toggleFavorite(filename){try{const removing=favoriteFiles.has(filename);await api("/api/favorites",{method:removing?"DELETE":"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({filename})});if(removing)favoriteFiles.delete(filename);else favoriteFiles.add(filename);syncFavoriteButtons(filename);if($("view-favorites").classList.contains("active"))await loadFavorites();toast(removing?"Removed from Favorites":"Added to Favorites");}catch(e){toast(e.message)}}
async function loadFavorites(){const fav=await api("/api/favorites");favoriteFiles=new Set(fav.map(f=>f.filename));const list=fav.map(f=>songs.find(s=>s.filename===f.filename)).filter(Boolean);renderSongs(list,$("favoriteSongs"));if($("nowFavorite").dataset.filename)syncFavoriteButtons($("nowFavorite").dataset.filename)}
async function loadHistory(){const h=await api(`/api/history?user_id=${encodeURIComponent(userId)}`);$("historyList").innerHTML=h.length?h.map(x=>`<div class="history-item"><span>${moodEmoji[x.mood]||"♫"} &nbsp;${esc(x.mood[0].toUpperCase()+x.mood.slice(1))} <small>· ${Math.round(x.confidence*100)}% confidence</small></span><small>${esc(x.created_at)}</small></div>`).join(""):'<div class="empty">Your mood check-ins will appear here.</div>'}
document.querySelectorAll(".nav-item").forEach(b=>b.addEventListener("click",()=>{document.querySelectorAll(".nav-item").forEach(x=>x.classList.remove("active"));b.classList.add("active");document.querySelectorAll(".view").forEach(x=>x.classList.remove("active"));$("view-"+b.dataset.view).classList.add("active");$("pageTitle").textContent=({discover:"Discover your mood",library:"Your music libraries",favorites:"Favorite tracks",history:"Mood history",assistant:"AI Music Assistant",analytics:"AI / ML Analytics"})[b.dataset.view];if(b.dataset.view==="favorites")loadFavorites();if(b.dataset.view==="history")loadHistory();if(b.dataset.view==="analytics")loadAnalytics();if(b.dataset.view==="library"){closeLibrary();loadLibraries()}}));
$("showCreateLibrary").addEventListener("click",()=>{$("createLibraryPanel").hidden=false;$("newLibraryName").focus()});
$("cancelCreateLibrary").addEventListener("click",()=>{$("createLibraryPanel").hidden=true;$("newLibraryName").value=""});
$("createLibraryBtn").addEventListener("click",async()=>{const name=$("newLibraryName").value.trim();const emoji=$("newLibraryEmoji").value.trim()||"🎵";if(!name){toast("Enter a library name first.");return}try{const result=await api("/api/libraries",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({name,emoji})});$("createLibraryPanel").hidden=true;$("newLibraryName").value="";await loadLibraries();openLibrary(result.id);toast("Your new music library is ready!")}catch(e){toast(e.message)}});
$("backToLibraries").addEventListener("click",closeLibrary);
$("deleteLibraryBtn").addEventListener("click",async()=>{if(!selectedLibraryId)return;if(!confirm("Delete this empty custom library?"))return;try{await api("/api/libraries",{method:"DELETE",headers:{"Content-Type":"application/json"},body:JSON.stringify({id:selectedLibraryId})});closeLibrary();await loadLibraries();toast("Library deleted") }catch(e){toast(e.message)}});
$("libraryUpload").addEventListener("change",async e=>{const files=[...e.target.files];if(!selectedLibraryId||!files.length)return;let added=0;for(const file of files){const fd=new FormData();fd.append("song",file);fd.append("library_id",selectedLibraryId);try{await api("/api/upload",{method:"POST",body:fd});added++}catch(err){toast(err.message)}}e.target.value="";await loadSongs();await loadLibraries();if(selectedLibraryId)await renderLibrarySongs();toast(`${added} song${added===1?"":"s"} added to library`) });
$("nowFavorite").addEventListener("click",()=>{const f=$("nowFavorite").dataset.filename;if(f)toggleFavorite(f)});
$("playBtn").addEventListener("click",()=>{if(!audio.src){if(queue.length)playSong(queue[0].filename);else toast("Choose a mood and track first");return}if(audio.paused)audio.play();else audio.pause()});
$("prevBtn").addEventListener("click",()=>playNext(-1));$("nextBtn").addEventListener("click",()=>playNext(1));audio.addEventListener("ended",()=>playNext(1));audio.addEventListener("play",()=>$("playBtn").textContent="Ⅱ");audio.addEventListener("pause",()=>$("playBtn").textContent="▶");
audio.addEventListener("timeupdate",()=>{if(audio.duration){$("progress").value=audio.currentTime/audio.duration*100;$("currentTime").textContent=fmt(audio.currentTime);$("duration").textContent=fmt(audio.duration)}});$("progress").addEventListener("input",e=>{if(audio.duration)audio.currentTime=audio.duration*e.target.value/100});$("volume").addEventListener("input",e=>audio.volume=e.target.value);audio.volume=.8;function fmt(t){return `${Math.floor(t/60)}:${String(Math.floor(t%60)).padStart(2,"0")}`}
async function sendFeedback(filename,rating,libraryMood){
  try{const result=await api("/api/feedback",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({filename,rating,mood:( ["happy","sad","angry","neutral","surprise","fear"].includes(libraryMood)?libraryMood:currentMood),user_id:userId})});toast(result.message);}
  catch(e){toast(e.message)}
}
async function loadAnalytics(){
  try{
    const a=await api(`/api/analytics?user_id=${encodeURIComponent(userId)}`);const f=a.feedback||{};
    $("analyticsStats").innerHTML=`<div class="stat-card"><b>${a.moods.reduce((n,x)=>n+x.count,0)}</b><small>Mood check-ins</small></div><div class="stat-card"><b>${f.likes||0}</b><small>Liked tracks</small></div><div class="stat-card"><b>${f.skips||0}</b><small>Disliked tracks</small></div>`;
    const total=a.moods.reduce((n,x)=>n+x.count,0)||1;
    $("moodBars").innerHTML=a.moods.length?a.moods.map(x=>`<div class="mood-bar-row"><span>${esc(x.mood)}</span><div class="bar-track"><i style="width:${Math.max(3,x.count/total*100)}%"></i></div><b>${x.count}</b></div>`).join(""):'<div class="empty">Mood analytics will appear after your first mood detection.</div>';
    const c=await api("/api/clusters");$("clusterPanel").textContent=c.available?`${c.model} grouped ${c.profiles} profiles using mood distribution and Like/Skip counts: `+c.clusters.map(x=>`${x.user.slice(0,10)} → Cluster ${x.cluster}`).join(" · "):c.message;
  }catch(e){$("clusterPanel").textContent=e.message}
}
$("assistantForm").addEventListener("submit",async e=>{
  e.preventDefault();const query=$("assistantQuery").value.trim();if(!query)return;$("assistantReply").textContent="Moodify AI is analysing your request…";$("assistantUseMood").hidden=true;
  try{const r=await api("/api/assistant",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({query})});assistantMood=r.mood;$("assistantReply").textContent=`${r.reply} Model: ${r.model}.`;$("assistantUseMood").hidden=false;}catch(err){$("assistantReply").textContent=err.message}
});
$("assistantUseMood").addEventListener("click",()=>{if(assistantMood){document.querySelector('[data-view="discover"]').click();recommend(assistantMood,1)}});
$("refreshAnalytics").addEventListener("click",loadAnalytics);

async function startCamera(){
  try{
    if(cameraStream)return;
    cameraManuallyStopped=false;
    detectionCompleted=false;
    pendingMood=null;
    pendingMoodCount=0;
    cameraStartedAt=Date.now();
    $("emotionReadout").textContent="Waiting for your face…";
    $("emotionPercent").textContent="0%";
    $("emotionProgress").style.width="0%";
    $("startCameraBtn").disabled=true;
    $("stopCameraBtn").disabled=true;
    if(!navigator.mediaDevices?.getUserMedia) throw Error("Camera access needs localhost or HTTPS. Open this app at http://127.0.0.1:5000.");
    if(!window.faceapi) throw Error("AI model library did not load. Check your internet connection and refresh.");
    $("cameraStatus").textContent="Requesting camera permission…";

    // Start the camera and download the AI models at the same time to reduce startup delay.
    const cameraPromise=navigator.mediaDevices.getUserMedia({video:{facingMode:"user",width:{ideal:640},height:{ideal:480}},audio:false});
    const modelPromise=(async()=>{
      const MODEL="https://cdn.jsdelivr.net/gh/justadudewhohacks/face-api.js@master/weights";
      await Promise.all([
        faceapi.nets.tinyFaceDetector.loadFromUri(MODEL),
        faceapi.nets.faceExpressionNet.loadFromUri(MODEL)
      ]);
    })();

    cameraStream=await cameraPromise;
    if(cameraManuallyStopped){releaseCamera();return;}
    $("stopCameraBtn").disabled=false;
    $("camera").srcObject=cameraStream;
    await $("camera").play();
    if(cameraManuallyStopped||!cameraStream)return;
    $("cameraPlaceholder").style.display="none";
    $("camera").style.display="block";
    document.querySelector(".camera-frame").classList.add("scanning");
    $("cameraStatus").textContent="Camera on · preparing AI detection…";

    await modelPromise;
    if(cameraManuallyStopped||!cameraStream)return;
    modelsReady=true;
    cameraStartedAt=Date.now();
    $("cameraStatus").textContent="Camera active · keep your face in view for a few seconds";
    scanFace();
    scanTimer=setInterval(scanFace,SCAN_INTERVAL_MS);
    toast("Camera is active. It will close automatically after detecting your mood.");
  }catch(e){
    releaseCamera();
    $("cameraStatus").textContent="Camera permission or AI model needs attention";
    toast(e.message||"Allow camera access and check your internet connection.");
    console.error(e);
  }
}

async function scanFace(){
  if(detectionCompleted||cameraManuallyStopped||!modelsReady||scanInProgress||$("camera").readyState<2)return;
  scanInProgress=true;
  try{
    const result=await faceapi.detectSingleFace($("camera"),new faceapi.TinyFaceDetectorOptions({inputSize:416,scoreThreshold:.30})).withFaceExpressions();
    if(!result){
      pendingMood=null;pendingMoodCount=0;
      $("cameraStatus").textContent="No face detected · move closer and look toward the camera";
      $("emotionReadout").textContent="Looking for a face…";
      $("emotionPercent").textContent="—";
      $("emotionProgress").style.width="0%";
      return;
    }
    const entries=Object.entries(result.expressions).sort((a,b)=>b[1]-a[1]);
    let [mood,score]=entries[0];
    if(mood==="disgust")mood="angry";
    if(!["happy","sad","angry","neutral","surprise","fear"].includes(mood))mood="neutral";
    const percent=Math.round(score*100);
    $("emotionReadout").textContent=`${mood[0].toUpperCase()+mood.slice(1)} emotion detected`;
    $("emotionPercent").textContent=`${percent}%`;
    $("emotionProgress").style.width=`${percent}%`;
    if(score<MIN_EMOTION_CONFIDENCE){
      pendingMood=null;pendingMoodCount=0;
      $("cameraStatus").textContent=`Face found · emotion confidence is low (${percent}%). Hold still in good light.`;
      return;
    }
    if(pendingMood===mood)pendingMoodCount++;else{pendingMood=mood;pendingMoodCount=1}
    const elapsed=Date.now()-cameraStartedAt;
    const readyToConfirm=pendingMoodCount>=MOOD_CONFIRM_FRAMES&&elapsed>=MIN_SCAN_DURATION_MS;
    $("cameraStatus").textContent=`Checking ${mood} · ${percent}% confidence · stable reading ${Math.min(pendingMoodCount,MOOD_CONFIRM_FRAMES)}/${MOOD_CONFIRM_FRAMES}`;
    if(readyToConfirm&&!detectionCompleted){
      detectionCompleted=true;
      // Close the webcam immediately after confirming one expression.
      releaseCamera();
      $("camera").pause();
      $("camera").srcObject=null;
      $("camera").style.display="none";
      $("cameraPlaceholder").style.display="flex";
      $("cameraPlaceholder").querySelector("b").textContent="Mood detected · camera off";
      $("cameraPlaceholder").querySelector("small").textContent=`${mood[0].toUpperCase()+mood.slice(1)} expression confirmed at ${percent}%`;
      $("cameraStatus").textContent=`${mood[0].toUpperCase()+mood.slice(1)} confirmed · ${percent}% confidence · camera closed`;
      document.querySelector(".camera-frame").classList.remove("scanning");
      await recommend(mood,score);
      $("cameraStatus").textContent=`${mood[0].toUpperCase()+mood.slice(1)} detected · ${percent}% confidence · playing a random song`;
    }
  }catch(e){console.error("Expression scan failed:",e)}
  finally{scanInProgress=false}
}

function releaseCamera(){
  $("stopCameraBtn").disabled=true;
  if(scanTimer)clearInterval(scanTimer);
  scanTimer=null;
  if(cameraStream)cameraStream.getTracks().forEach(t=>t.stop());
  cameraStream=null;
  $("startCameraBtn").disabled=false;
}
$("startCameraBtn").addEventListener("click",()=>{
  startCamera();
});
$("stopCameraBtn").addEventListener("click",()=>{
  if(!cameraStream)return;
  cameraManuallyStopped=true;detectionCompleted=true;pendingMood=null;pendingMoodCount=0;
  $("emotionReadout").textContent="Detection stopped";$("emotionPercent").textContent="—";$("emotionProgress").style.width="0%";
  releaseCamera();$("camera").pause();$("camera").srcObject=null;$("camera").style.display="none";
  $("cameraPlaceholder").style.display="flex";
  $("cameraPlaceholder").querySelector("b").textContent="Camera stopped";
  $("cameraPlaceholder").querySelector("small").textContent="Scanning stopped. Click Start camera to scan again.";
  $("cameraStatus").textContent="Camera stopped manually · no expression scan";
  document.querySelector(".camera-frame").classList.remove("scanning");
  toast("Camera and mood scanning stopped");
});
window.addEventListener("beforeunload",releaseCamera);

loadSongs().catch(e=>toast(e.message));loadFavorites().catch(e=>toast(e.message));loadLibraries().catch(e=>toast(e.message));loadHistory().catch(()=>{});
// Camera stays off until the user explicitly clicks Start camera.
