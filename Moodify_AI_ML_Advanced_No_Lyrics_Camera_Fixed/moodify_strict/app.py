from flask import Flask, render_template, jsonify, request, send_from_directory
from werkzeug.utils import secure_filename
from pathlib import Path
import sqlite3, time, math, wave, struct, re, random

BASE = Path(__file__).resolve().parent
SONG_DIR = BASE / "static" / "songs"
DB_PATH = BASE / "database" / "music_player.db"
ALLOWED = {".mp3", ".wav", ".ogg", ".m4a"}
MAX_UPLOAD = 30 * 1024 * 1024
app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD
SONG_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

DEFAULT_LIBRARIES = [
    ("Happy", "☀️", "Songs played only when a happy expression is detected"),
    ("Sad", "🌧️", "Songs played only when a sad expression is detected"),
    ("Angry", "😠", "Songs played only when an angry expression is detected"),
    ("Neutral", "☁️", "Songs for a neutral expression"),
    ("Surprise", "✨", "Songs for a surprised expression"),
    ("Fear", "🌿", "Songs for a fearful expression"),
    ("Workout", "⚡", "Your workout collection"),
    ("Romantic", "💗", "Your romantic collection"),
    ("Relaxing", "🕊️", "Your relaxing collection"),
    ("My Music", "🎵", "Your personal collection"),
]
# Strict one-to-one mapping: a detected mood can play songs from only its
# matching mood library. Other personal libraries are never used as fallback.
MOOD_LIBRARIES = {
    "happy": "happy", "sad": "sad", "angry": "angry",
    "neutral": "neutral", "surprise": "surprise", "fear": "fear",
}

def safe_user_id(value):
    value = re.sub(r"[^a-zA-Z0-9_-]", "", str(value or "guest"))[:64]
    return value or "guest"


def audio_features(path):
    """Extract lightweight audio features using Python's standard library.
    WAV files get measured duration, RMS energy and zero-crossing rate. Other
    formats receive conservative file-metadata features until optional codecs are added.
    """
    result = {"duration": 0.0, "energy": 0.5, "zcr": 0.1, "tempo": 100.0,
              "feature_source": "file metadata"}
    try:
        if Path(path).suffix.lower() == ".wav":
            with wave.open(str(path), "rb") as wf:
                rate, frames, channels, width = wf.getframerate(), wf.getnframes(), wf.getnchannels(), wf.getsampwidth()
                result["duration"] = frames / float(rate or 1)
                raw = wf.readframes(min(frames, rate * 30))
                if width in (1, 2, 4) and raw:
                    fmt = {1: "B", 2: "h", 4: "i"}[width]
                    step = width * channels
                    vals = []
                    for i in range(0, len(raw) - width + 1, step):
                        v = struct.unpack_from("<" + fmt, raw, i)[0]
                        if width == 1: v -= 128
                        vals.append(v / float(2 ** (8 * width - 1)))
                    if vals:
                        result["energy"] = min(1.0, math.sqrt(sum(v*v for v in vals) / len(vals)) * 3.2)
                        crossings = sum(1 for a,b in zip(vals, vals[1:]) if (a < 0) != (b < 0))
                        result["zcr"] = crossings / max(1, len(vals)-1)
                        # Estimate a broad tempo proxy from 100ms energy pulses.
                        hop = max(1, int(rate * .1))
                        envelope = [sum(abs(v) for v in vals[i:i+hop]) / max(1,len(vals[i:i+hop])) for i in range(0,len(vals),hop)]
                        best = (0.0, 6)
                        for bpm in range(60, 181, 2):
                            lag = max(1, round(600 / bpm))
                            if lag < len(envelope):
                                score = sum(envelope[i]*envelope[i-lag] for i in range(lag,len(envelope))) / (len(envelope)-lag)
                                if score > best[0]: best = (score, bpm)
                        result["tempo"] = float(best[1])
                        result["feature_source"] = "measured WAV signal"
        else:
            size = Path(path).stat().st_size
            result["energy"] = min(.95, .25 + (size % 1000000) / 1400000)
            result["zcr"] = .1 + (size % 50000) / 100000
            result["feature_source"] = "metadata estimate (codec not decoded)"
    except Exception as exc:
        app.logger.warning("Audio analysis skipped for %s: %s", path, exc)
    return result


def train_knn_preferences(user_id, mood, candidates):
    """KNN-style preference learner over song audio features and explicit feedback."""
    with db() as con:
        feedback = con.execute("""SELECT f.rating, sf.energy, sf.zcr, sf.tempo
            FROM song_feedback f JOIN song_features sf ON sf.filename=f.filename
            WHERE f.user_id=? AND f.mood=?""", (user_id, mood)).fetchall()
    if not feedback:
        return candidates, False
    def vec(row): return [float(row["energy"]), float(row["zcr"]), float(row["tempo"])/180.0]
    liked = [vec(r) for r in feedback if r["rating"] > 0]
    disliked = [vec(r) for r in feedback if r["rating"] < 0]
    for song in candidates:
        f = song.get("features") or {"energy":.5,"zcr":.1,"tempo":100}
        v = [float(f.get("energy",.5)),float(f.get("zcr",.1)),float(f.get("tempo",100))/180.0]
        def sim(items):
            if not items: return 0.0
            distances = sorted(math.sqrt(sum((a-b)**2 for a,b in zip(v,x))) for x in items)[:3]
            return sum(1/(1+d) for d in distances)/len(distances)
        song["ml_score"] = round(sim(liked) - .85*sim(disliked), 4)
    candidates.sort(key=lambda x:x.get("ml_score",0), reverse=True)
    return candidates, True


class TinyNaiveBayes:
    """Small multinomial Naive Bayes intent model trained on bundled examples."""
    examples = {
        "happy": ["I feel happy joyful excited cheerful play upbeat songs", "make me energetic and positive", "celebrate good mood", "mai khush hu mast gaane chalao"],
        "sad": ["I feel sad lonely low blue play soft music", "heartbroken need emotional songs", "feeling down today", "mai dukhi hu udaas gaane chalao"],
        "angry": ["I am angry frustrated irritated need calm music", "I feel mad stressed tension", "cool me down", "mujhe gussa aa raha hai shaant music chalao"],
        "neutral": ["play normal background music", "I feel okay balanced relaxed", "nothing special", "I want peaceful relaxing easy listening music", "mai theek hu normal music"],
        "surprise": ["surprise me with something unexpected", "I am amazed excited wow", "play something different", "mujhe hairani ho rahi hai"],
        "fear": ["I feel scared anxious nervous need soothing music", "I am worried and afraid", "help me relax gently", "I am anxious panicking need calm soft music", "mujhe dar lag raha hai"]
    }
    def __init__(self):
        self.vocab=set(); self.counts={}; self.totals={}
        for label, texts in self.examples.items():
            counts={}; total=0
            for text in texts:
                for token in re.findall(r"[a-z]+", text.lower()):
                    self.vocab.add(token); counts[token]=counts.get(token,0)+1; total+=1
            self.counts[label]=counts; self.totals[label]=total
    def predict(self, text):
        tokens=re.findall(r"[a-z]+", text.lower())
        scores={}
        for label, counts in self.counts.items():
            score=math.log(1/len(self.counts))
            denom=self.totals[label]+len(self.vocab)
            for token in tokens: score += math.log((counts.get(token,0)+1)/denom)
            scores[label]=score
        label=max(scores,key=scores.get)
        exps={k:math.exp(v-max(scores.values())) for k,v in scores.items()}
        confidence=exps[label]/sum(exps.values()) if exps else 0
        return label, confidence

ASSISTANT_MODEL = TinyNaiveBayes()


def kmeans(points, k=3, iterations=30):
    """Deterministic pure-Python K-Means; returns cluster ids for feature vectors."""
    if len(points) < k: return []
    centers=[list(points[i]) for i in range(k)]
    labels=[0]*len(points)
    for _ in range(iterations):
        changed=False
        for i,pnt in enumerate(points):
            label=min(range(k),key=lambda c:sum((a-b)**2 for a,b in zip(pnt,centers[c])))
            if labels[i]!=label: labels[i]=label; changed=True
        for c in range(k):
            members=[points[i] for i,x in enumerate(labels) if x==c]
            if members: centers[c]=[sum(v[j] for v in members)/len(members) for j in range(len(points[0]))]
        if not changed: break
    return labels

def db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con

def init_db():
    with db() as con:
        con.execute("""CREATE TABLE IF NOT EXISTS history (
            id INTEGER PRIMARY KEY AUTOINCREMENT, mood TEXT NOT NULL,
            confidence REAL NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP)""")
        con.execute("""CREATE TABLE IF NOT EXISTS favorites (
            id INTEGER PRIMARY KEY AUTOINCREMENT, filename TEXT UNIQUE NOT NULL,
            title TEXT NOT NULL)""")
        con.execute("""CREATE TABLE IF NOT EXISTS libraries (
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL COLLATE NOCASE UNIQUE,
            emoji TEXT NOT NULL DEFAULT '🎵', description TEXT NOT NULL DEFAULT '',
            is_default INTEGER NOT NULL DEFAULT 0, created_at TEXT DEFAULT CURRENT_TIMESTAMP)""")
        con.execute("""CREATE TABLE IF NOT EXISTS songs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, filename TEXT UNIQUE NOT NULL,
            library_id INTEGER NOT NULL, title TEXT NOT NULL,
            FOREIGN KEY(library_id) REFERENCES libraries(id))""")
        con.execute("""CREATE TABLE IF NOT EXISTS song_features (
            filename TEXT PRIMARY KEY, duration REAL DEFAULT 0, energy REAL DEFAULT .5,
            zcr REAL DEFAULT .1, tempo REAL DEFAULT 100, feature_source TEXT DEFAULT 'metadata')""")
        con.execute("""CREATE TABLE IF NOT EXISTS song_feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL, filename TEXT NOT NULL,
            mood TEXT NOT NULL, rating INTEGER NOT NULL CHECK(rating IN (-1,1)),
            created_at TEXT DEFAULT CURRENT_TIMESTAMP, UNIQUE(user_id,filename,mood))""")
        # Backward-compatible user identifier for existing mood-history databases.
        cols = {r[1] for r in con.execute("PRAGMA table_info(history)")}
        if "user_id" not in cols: con.execute("ALTER TABLE history ADD COLUMN user_id TEXT DEFAULT 'guest'")
        for name, emoji, desc in DEFAULT_LIBRARIES:
            con.execute("INSERT OR IGNORE INTO libraries(name,emoji,description,is_default) VALUES(?,?,?,1)", (name, emoji, desc))
        library_ids = {r["name"].lower(): r["id"] for r in con.execute("SELECT id,name FROM libraries")}
        # Import audio already present in the project (including the six demo tracks).
        for p in SONG_DIR.iterdir():
            if p.is_file() and p.suffix.lower() in ALLOWED:
                stem = p.stem.lower()
                target = "my music"
                for tag, lib in [("happy", "happy"), ("sad", "sad"), ("angry", "angry"),
                                 ("fear", "fear"), ("neutral", "neutral"), ("surprise", "surprise")]:
                    if tag in stem:
                        target = lib
                        break
                con.execute("INSERT OR IGNORE INTO songs(filename,library_id,title) VALUES(?,?,?)",
                            (p.name, library_ids[target], p.stem.replace("_", " ").replace("-", " ").title()))
        # Upgrade known demo tracks from earlier Moodify versions into their
        # dedicated strict-matching libraries without moving user-uploaded songs.
        for demo_name, mood_name in [("happy_sample.wav", "happy"), ("sad_sample.wav", "sad"),
                                     ("angry_sample.wav", "angry"), ("neutral_sample.wav", "neutral"),
                                     ("surprise_sample.wav", "surprise"), ("fear_sample.wav", "fear")]:
            con.execute("UPDATE songs SET library_id=? WHERE filename=?",
                        (library_ids[mood_name], demo_name))
        for row in con.execute("SELECT filename FROM songs").fetchall():
            if not con.execute("SELECT 1 FROM song_features WHERE filename=?", (row["filename"],)).fetchone():
                f = audio_features(SONG_DIR / row["filename"])
                con.execute("INSERT OR REPLACE INTO song_features VALUES(?,?,?,?,?,?)",
                            (row["filename"],f["duration"],f["energy"],f["zcr"],f["tempo"],f["feature_source"]))

def list_libraries():
    with db() as con:
        rows = con.execute("""SELECT l.id,l.name,l.emoji,l.description,l.is_default,
            COUNT(s.id) AS song_count FROM libraries l LEFT JOIN songs s ON s.library_id=l.id
            GROUP BY l.id ORDER BY l.is_default DESC, l.name COLLATE NOCASE""").fetchall()
    return [dict(r) for r in rows]

def list_songs(library_id=None):
    query = """SELECT s.filename,s.title,l.id AS library_id,l.name AS library_name,
        sf.duration AS duration,sf.energy AS energy,sf.zcr AS zcr,sf.tempo AS tempo,sf.feature_source AS feature_source
        FROM songs s JOIN libraries l ON l.id=s.library_id
        LEFT JOIN song_features sf ON sf.filename=s.filename"""
    params = ()
    if library_id is not None:
        query += " WHERE l.id=?"
        params = (library_id,)
    query += " ORDER BY s.title COLLATE NOCASE"
    with db() as con:
        rows = con.execute(query, params).fetchall()
    return [{**dict(r), "url": f"/songs/{r['filename']}", "type": Path(r["filename"]).suffix.lower()[1:]} for r in rows]

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/libraries", methods=["GET", "POST", "DELETE"])
def libraries():
    if request.method == "GET":
        return jsonify(list_libraries())
    data = request.get_json(silent=True) or {}
    if request.method == "POST":
        name = " ".join(str(data.get("name", "")).split())
        if not name or len(name) > 40:
            return jsonify({"error": "Enter a library name (1–40 characters)."}), 400
        emoji = str(data.get("emoji", "🎵"))[:8]
        description = str(data.get("description", "My custom music collection"))[:100]
        try:
            with db() as con:
                cur = con.execute("INSERT INTO libraries(name,emoji,description,is_default) VALUES(?,?,?,0)",
                                  (name, emoji, description))
                new_id = cur.lastrowid
        except sqlite3.IntegrityError:
            return jsonify({"error": "A library with that name already exists."}), 409
        return jsonify({"ok": True, "id": new_id})
    try:
        library_id = int(data.get("id", 0))
    except (TypeError, ValueError):
        return jsonify({"error": "Invalid library."}), 400
    with db() as con:
        lib = con.execute("SELECT is_default FROM libraries WHERE id=?", (library_id,)).fetchone()
        if not lib:
            return jsonify({"error": "Library not found."}), 404
        if lib["is_default"]:
            return jsonify({"error": "Built-in libraries cannot be deleted."}), 400
        count = con.execute("SELECT COUNT(*) FROM songs WHERE library_id=?", (library_id,)).fetchone()[0]
        if count:
            return jsonify({"error": "This library has songs. Move or remove its songs before deleting it."}), 400
        con.execute("DELETE FROM libraries WHERE id=?", (library_id,))
    return jsonify({"ok": True})

@app.route("/api/songs")
def songs():
    library_id = request.args.get("library_id", type=int)
    return jsonify(list_songs(library_id))

@app.route("/api/songs", methods=["DELETE"])
def delete_song():
    data = request.get_json(silent=True) or {}
    filename = secure_filename(str(data.get("filename", "")))
    if not filename:
        return jsonify({"error": "Choose a song to delete."}), 400
    with db() as con:
        song = con.execute("SELECT filename FROM songs WHERE filename=?", (filename,)).fetchone()
        if not song:
            return jsonify({"error": "Song not found."}), 404
        # Remove database references and the uploaded audio file.
        con.execute("DELETE FROM favorites WHERE filename=?", (filename,))
        con.execute("DELETE FROM song_feedback WHERE filename=?", (filename,))
        con.execute("DELETE FROM song_features WHERE filename=?", (filename,))
        con.execute("DELETE FROM songs WHERE filename=?", (filename,))
    audio_path = SONG_DIR / filename
    try:
        audio_path.unlink(missing_ok=True)
    except OSError:
        app.logger.exception("Could not remove audio file %s", filename)
        return jsonify({"error": "Song was removed from the library, but its audio file could not be deleted."}), 500
    return jsonify({"ok": True})

@app.route("/api/recommend", methods=["POST"])
def recommend():
    data = request.get_json(silent=True) or {}
    mood = str(data.get("mood", "neutral")).lower()
    user_id = safe_user_id(data.get("user_id"))
    try: confidence = max(0.0, min(1.0, float(data.get("confidence", 0))))
    except (TypeError, ValueError): confidence = 0.0
    if mood not in MOOD_LIBRARIES: mood = "neutral"
    with db() as con: con.execute("INSERT INTO history(mood, confidence, user_id) VALUES (?, ?, ?)", (mood, confidence, user_id))
    libs = {x["name"].lower(): x["id"] for x in list_libraries()}
    target_name = MOOD_LIBRARIES[mood]; target_id = libs.get(target_name)
    matches = [s for s in list_songs(target_id)] if target_id else []
    with db() as con:
        for song in matches:
            f=con.execute("SELECT duration,energy,zcr,tempo,feature_source FROM song_features WHERE filename=?",(song["filename"],)).fetchone()
            song["features"]=dict(f) if f else {"energy":.5,"zcr":.1,"tempo":100,"duration":0,"feature_source":"unavailable"}
    matches, learned = train_knn_preferences(user_id,mood,matches)
    message = (f"ML ranked tracks using your feedback in the {mood.title()} library." if matches and learned else
               f"Playing a random track from your {mood.title()} library." if matches else
               f"No songs in your {mood.title()} library. Add songs to that library; other moods will not be used.")
    # Preserve the user's random-play requirement: randomize within the top-ranked preference tier.
    if learned and matches:
        top=matches[:max(1,min(3,len(matches)))]; random.shuffle(top); matches=top+matches[len(top):]
    for song in matches: song.pop("features",None)
    return jsonify({"mood":mood,"confidence":round(confidence*100,1),"library":target_name.title(),
                    "songs":matches,"message":message,"ml_personalized":learned,
                    "model":"KNN preference learner" if learned else "awaiting feedback"})

@app.route("/api/history")
def history():
    user_id=safe_user_id(request.args.get("user_id"))
    with db() as con:
        rows=con.execute("SELECT mood,confidence,created_at FROM history WHERE user_id=? ORDER BY id DESC LIMIT 20",(user_id,)).fetchall()
    return jsonify([dict(r) for r in rows])

@app.route("/api/feedback", methods=["POST"])
def feedback():
    data=request.get_json(silent=True) or {}; user_id=safe_user_id(data.get("user_id"))
    filename=secure_filename(str(data.get("filename",""))); mood=str(data.get("mood","neutral")).lower()
    try: rating=1 if int(data.get("rating",0))>0 else -1
    except (TypeError,ValueError): return jsonify({"error":"Invalid feedback."}),400
    if mood not in MOOD_LIBRARIES: return jsonify({"error":"Invalid mood."}),400
    with db() as con:
        if not con.execute("SELECT 1 FROM songs WHERE filename=?",(filename,)).fetchone(): return jsonify({"error":"Song not found."}),404
        con.execute("INSERT INTO song_feedback(user_id,filename,mood,rating) VALUES(?,?,?,?) ON CONFLICT(user_id,filename,mood) DO UPDATE SET rating=excluded.rating,created_at=CURRENT_TIMESTAMP",(user_id,filename,mood,rating))
    return jsonify({"ok":True,"message":"Preference learned. Recommendations will adapt within this mood library."})

@app.route("/api/analytics")
def analytics():
    user_id=safe_user_id(request.args.get("user_id"))
    with db() as con:
        rows=con.execute("SELECT mood,COUNT(*) AS count,AVG(confidence) AS avg_confidence FROM history WHERE user_id=? GROUP BY mood ORDER BY count DESC",(user_id,)).fetchall()
        feedback=con.execute("SELECT SUM(CASE WHEN rating=1 THEN 1 ELSE 0 END) AS likes,SUM(CASE WHEN rating=-1 THEN 1 ELSE 0 END) AS skips,COUNT(*) AS total FROM song_feedback WHERE user_id=?",(user_id,)).fetchone()
        recent=con.execute("SELECT mood,confidence,created_at FROM history WHERE user_id=? ORDER BY id DESC LIMIT 8",(user_id,)).fetchall()
    return jsonify({"moods":[dict(x) for x in rows],"feedback":dict(feedback),"recent":[dict(x) for x in recent]})

@app.route("/api/assistant", methods=["POST"])
def assistant():
    data=request.get_json(silent=True) or {}; query=str(data.get("query",""))[:500]
    if not query.strip(): return jsonify({"error":"Type what kind of music you want."}),400
    mood,confidence=ASSISTANT_MODEL.predict(query)
    libs={x["name"].lower():x for x in list_libraries()}; lib=libs.get(MOOD_LIBRARIES[mood])
    count=lib["song_count"] if lib else 0
    reply=(f"I understood a {mood} music intent ({round(confidence*100)}% model confidence). "
           +(f"Your {mood.title()} library has {count} song(s). I can play only from that matching library." if count else f"Your {mood.title()} library is empty. Add songs there; I will not use another mood's library."))
    return jsonify({"mood":mood,"confidence":round(confidence,3),"reply":reply,"model":"Multinomial Naive Bayes intent classifier"})

@app.route("/api/clusters")
def clusters():
    with db() as con:
        users=[r[0] for r in con.execute("SELECT DISTINCT user_id FROM history UNION SELECT DISTINCT user_id FROM song_feedback").fetchall()]
        vectors=[]
        for uid in users:
            mood_counts={r["mood"]:r["n"] for r in con.execute("SELECT mood,COUNT(*) n FROM history WHERE user_id=? GROUP BY mood",(uid,)).fetchall()}
            likes=con.execute("SELECT SUM(rating=1),SUM(rating=-1) FROM song_feedback WHERE user_id=?",(uid,)).fetchone()
            total=sum(mood_counts.values()) or 1
            vectors.append([mood_counts.get(m,0)/total for m in ("happy","sad","angry","neutral","surprise","fear")]+[float(likes[0] or 0),float(likes[1] or 0)])
    if len(users)<3:
        return jsonify({"available":False,"profiles":len(users),"required":3,"message":"K-Means needs at least 3 distinct listening profiles. Use another browser/device or create test profiles to compare listening patterns."})
    k=min(3,len(users)); labels=kmeans(vectors,k)
    return jsonify({"available":True,"profiles":len(users),"clusters":[{"user":u,"cluster":int(c)+1} for u,c in zip(users,labels)],"model":"K-Means clustering","features":"Mood distribution + Like/Skip counts"})

@app.route("/api/favorites", methods=["GET", "POST", "DELETE"])
def favorites():
    if request.method == "GET":
        with db() as con:
            rows = con.execute("SELECT filename, title FROM favorites ORDER BY title").fetchall()
        return jsonify([dict(r) for r in rows])
    data = request.get_json(silent=True) or {}
    filename = Path(str(data.get("filename", ""))).name
    song = next((s for s in list_songs() if s["filename"] == filename), None)
    if not song:
        return jsonify({"error": "Song not found"}), 404
    with db() as con:
        if request.method == "POST":
            con.execute("INSERT OR IGNORE INTO favorites(filename,title) VALUES (?,?)", (filename, song["title"]))
        else:
            con.execute("DELETE FROM favorites WHERE filename=?", (filename,))
    return jsonify({"ok": True})

@app.route("/api/upload", methods=["POST"])
def upload():
    f = request.files.get("song")
    if not f or not f.filename:
        return jsonify({"error": "Choose an audio file first."}), 400
    safe = secure_filename(f.filename)
    ext = Path(safe).suffix.lower()
    if ext not in ALLOWED:
        return jsonify({"error": "Allowed formats: MP3, WAV, OGG, M4A."}), 400
    library_id = request.form.get("library_id", type=int)
    with db() as con:
        lib = con.execute("SELECT id FROM libraries WHERE id=?", (library_id,)).fetchone() if library_id else None
        if not lib:
            return jsonify({"error": "Choose a music library before uploading."}), 400
    target = SONG_DIR / safe
    if target.exists():
        target = SONG_DIR / f"{int(time.time() * 1000)}_{safe}"
    f.save(target)
    title = Path(safe).stem.replace("_", " ").replace("-", " ").title()
    features=audio_features(target)
    with db() as con:
        con.execute("INSERT INTO songs(filename,library_id,title) VALUES(?,?,?)", (target.name, library_id, title))
        con.execute("INSERT OR REPLACE INTO song_features VALUES(?,?,?,?,?,?)",(target.name,features["duration"],features["energy"],features["zcr"],features["tempo"],features["feature_source"]))
    return jsonify({"ok": True, "filename": target.name, "library_id": library_id, "audio_analysis":features})

@app.route("/songs/<path:filename>")
def serve_song(filename):
    return send_from_directory(SONG_DIR, filename)

@app.route("/health")
def health():
    return jsonify({"status": "ok", "songs": len(list_songs()), "libraries": len(list_libraries())})

init_db()
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
