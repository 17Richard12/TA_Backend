import os
import firebase_admin
from firebase_admin import credentials, firestore
from dotenv import load_dotenv

load_dotenv()

import os
import glob
import json
import firebase_admin
from firebase_admin import credentials, firestore
from dotenv import load_dotenv

load_dotenv()

TARGET_PROJECT_ID = os.getenv("FIREBASE_PROJECT_ID", "flutter-agent-doctor")

if not firebase_admin._apps:
    cred = None

    # 1. Cek string JSON dari environment variable (FIREBASE_KEY_JSON)
    key_json_str = os.getenv("FIREBASE_KEY_JSON")
    if key_json_str:
        try:
            key_dict = json.loads(key_json_str)
            cred = credentials.Certificate(key_dict)
            print("Firebase initialized with FIREBASE_KEY_JSON env variable")
        except Exception as e:
            print(f"Warning: Failed to parse FIREBASE_KEY_JSON: {e}")

    # 2. Cek FIREBASE_PATH atau GOOGLE_APPLICATION_CREDENTIALS jika filenya ada
    if not cred:
        for env_var in ["FIREBASE_PATH", "GOOGLE_APPLICATION_CREDENTIALS"]:
            env_path = os.getenv(env_var)
            if env_path and os.path.exists(env_path):
                cred = credentials.Certificate(env_path)
                print(f"Firebase initialized with file from {env_var}: {env_path}")
                break

    # 3. Cek lokasi mount secret standar Cloud Run (/secrets/firebase/key.json)
    if not cred and os.path.exists("/secrets/firebase/key.json"):
        cred = credentials.Certificate("/secrets/firebase/key.json")
        print("Firebase initialized with secret mount at /secrets/firebase/key.json")

    # 4. Cek file JSON lokal di folder config/ atau root (untuk development lokal)
    if not cred:
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        json_patterns = [
            os.path.join(base_dir, "config", "*firebase*.json"),
            os.path.join(base_dir, "config", "*service*.json"),
            os.path.join(base_dir, "*firebase*.json"),
        ]
        for pattern in json_patterns:
            matches = glob.glob(pattern)
            if matches:
                cred = credentials.Certificate(matches[0])
                print(f"Firebase initialized with local file: {matches[0]}")
                break

    # 5. Inisialisasi Firebase App
    if cred:
        firebase_admin.initialize_app(cred)
    else:
        # Fallback ADC dengan project ID target
        print(f"Firebase initializing with ADC for project {TARGET_PROJECT_ID}")
        firebase_admin.initialize_app(options={"projectId": TARGET_PROJECT_ID})

# Inisialisasi Firestore client
try:
    db = firestore.client()
except Exception:
    db = firestore.client(project=TARGET_PROJECT_ID)
