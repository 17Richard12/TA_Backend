import firebase_admin
from firebase_admin import credentials, firestore
from dotenv import load_dotenv
import os

load_dotenv()

print("DEBUG FIREBASE_PATH:", os.getenv("FIREBASE_PATH"))

cred = credentials.Certificate(os.getenv("FIREBASE_PATH"))
firebase_admin.initialize_app(cred)

db = firestore.client()