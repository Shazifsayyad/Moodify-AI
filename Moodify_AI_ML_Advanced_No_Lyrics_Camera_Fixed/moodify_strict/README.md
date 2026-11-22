# Moodify — Advanced AI + Machine Learning Emotion-Based Music Player

Moodify is a local Flask + SQLite music player with browser-based facial-expression recognition and working lightweight ML algorithms. It preserves strict mood libraries, random song playback, camera stop, and per-song deletion.

## Run on Windows

1. Extract this ZIP and open the `moodify_strict` folder in VS Code.
2. Use Python 3.10 or newer. The app uses Flask and Python's standard library for its ML/audio-analysis logic; it does not require NumPy, TensorFlow, or scikit-learn.
3. In PowerShell:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python app.py
```

4. Open `http://127.0.0.1:5000`.

If PowerShell blocks activation for this terminal session, run:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.venv\Scripts\Activate.ps1
```

## AI and ML modules included

### 1. CNN facial-expression recognition
- Uses the pretrained Face-api.js Tiny Face Detector and Face Expression network in the browser.
- The expression network is a pretrained convolutional neural network; it is used for inference, not trained from scratch by this project.
- Camera frames remain in the browser. The Flask server receives only the predicted mood label and confidence, not camera images.
- The camera stays off until **Start camera** is clicked. The first detected face/expression is used for mood selection, and the webcam shuts down immediately. **Stop camera** cancels scanning at any time.

### 2. ML music recommendation — KNN-style preference learner
- Every track is analysed for audio features and each user's Like / Not for me feedback is stored in SQLite.
- The learner compares candidate track feature vectors with the user's nearest liked and disliked examples for that mood.
- It ranks songs only inside the detected mood's matching library. For random playback, it randomizes within the top preference-ranked tracks after feedback exists; before feedback it shuffles the matching library.
- Feedback is learned separately for each browser profile and mood. Use 👍 / 👎 beside a song to teach it.

### 3. Audio feature analysis
- WAV files: measures duration, RMS signal energy, zero-crossing rate and an approximate tempo from the audio waveform.
- MP3, OGG and M4A: currently use conservative file-metadata estimates because no external audio codec is bundled. These estimates are not presented as measured waveform features.
- Feature values are stored in the `song_features` SQLite table and used by the preference learner.

### 4. Mood analytics
- Stores mood check-ins and confidence by browser profile.
- The AI / ML Analytics page shows mood distribution and Like / Skip totals.

### 5. AI Music Assistant — Multinomial Naive Bayes
- A small text-intent classifier trained on bundled example phrases (including common English and romanized Hindi phrases).
- It predicts one of the supported moods and can send that mood to the strict mood recommendation flow.
- This is a local intent classifier, not a generative chatbot or a medical/psychological assessment.

### 6. K-Means listener clustering
- Groups distinct browser profiles using mood distribution and Like / Skip counts.
- It requires at least 3 distinct profiles before showing clusters. One browser profile alone is not enough data for meaningful user-to-user clustering.
- To test, use separate browser profiles/devices and create mood history and feedback in each.



### Manual camera start
The camera does not turn on when the page loads. Click **Start camera** to begin face and expression detection. The webcam stream is stopped automatically after the first face/expression detection. Click **Stop camera** to cancel, or **Start camera** again to scan another time.
