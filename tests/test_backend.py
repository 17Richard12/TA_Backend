import hashlib
import json
import os
import sys
import unittest
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from unittest.mock import patch

# Ensure TA_Backend root is on sys.path
BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)


# =====================================================================
# In-Memory Mock Firestore (Never touches production Firestore)
# =====================================================================
class MockDocumentSnapshot:
    def __init__(self, doc_id: str, data: Optional[Dict[str, Any]], ref: "MockDocumentReference"):
        self.id = doc_id
        self._data = dict(data) if data is not None else None
        self.reference = ref

    @property
    def exists(self) -> bool:
        return self._data is not None

    def to_dict(self) -> Optional[Dict[str, Any]]:
        return dict(self._data) if self._data is not None else None


class MockDocumentReference:
    def __init__(self, collection: "MockCollectionReference", doc_id: str):
        self._collection = collection
        self.id = doc_id

    def get(self) -> MockDocumentSnapshot:
        data = self._collection._docs.get(self.id)
        return MockDocumentSnapshot(self.id, data, self)

    def set(self, data: Dict[str, Any], merge: bool = False) -> None:
        if merge and self.id in self._collection._docs:
            merged = dict(self._collection._docs[self.id])
            merged.update(dict(data))
            self._collection._docs[self.id] = merged
        else:
            self._collection._docs[self.id] = dict(data)

    def update(self, data: Dict[str, Any]) -> None:
        if self.id not in self._collection._docs:
            raise KeyError(f"Document {self.id} does not exist")
        self._collection._docs[self.id].update(dict(data))

    def delete(self) -> None:
        self._collection._docs.pop(self.id, None)

    def collection(self, sub_name: str) -> "MockCollectionReference":
        sub_key = f"{self._collection._name}/{self.id}/{sub_name}"
        return self._collection._db.collection(sub_key)


class MockQuery:
    def __init__(
        self,
        collection: "MockCollectionReference",
        filters: Optional[List[Any]] = None,
        limit_val: Optional[int] = None,
        order_field: Optional[str] = None,
        order_dir: str = "ASCENDING",
    ):
        self._collection = collection
        self._filters = list(filters or [])
        self._limit = limit_val
        self._order_field = order_field
        self._order_dir = order_dir

    def where(self, field_path: Optional[str] = None, op_string: Optional[str] = None, value: Any = None, filter: Any = None) -> "MockQuery":
        new_filters = list(self._filters)
        if filter is not None:
            new_filters.append((filter.field_path, filter.op_string, filter.value))
        else:
            new_filters.append((field_path, op_string, value))
        return MockQuery(self._collection, new_filters, self._limit, self._order_field, self._order_dir)

    def limit(self, count: int) -> "MockQuery":
        return MockQuery(self._collection, self._filters, count, self._order_field, self._order_dir)

    def order_by(self, field_path: str, direction: str = "ASCENDING") -> "MockQuery":
        return MockQuery(self._collection, self._filters, self._limit, field_path, direction)

    def stream(self):
        results: List[MockDocumentSnapshot] = []
        for doc_id, data in list(self._collection._docs.items()):
            matched = True
            for f_path, op, val in self._filters:
                actual = data.get(f_path)
                if op == "==" and actual != val:
                    matched = False
                    break
                elif op == "in" and actual not in (val or []):
                    matched = False
                    break
            if matched:
                results.append(
                    MockDocumentSnapshot(doc_id, data, MockDocumentReference(self._collection, doc_id))
                )

        if self._order_field:
            reverse = str(self._order_dir).upper() == "DESCENDING"
            results.sort(key=lambda s: str((s.to_dict() or {}).get(self._order_field, "")), reverse=reverse)

        if self._limit is not None:
            results = results[: self._limit]

        return iter(results)


class MockCollectionReference(MockQuery):
    def __init__(self, db_instance: "MockFirestoreDB", name: str):
        self._db = db_instance
        self._name = name
        self._docs: Dict[str, Dict[str, Any]] = {}
        super().__init__(self)

    def document(self, doc_id: Optional[str] = None) -> MockDocumentReference:
        if not doc_id:
            doc_id = f"auto_{len(self._docs) + 1}"
        return MockDocumentReference(self, doc_id)

    def add(self, data: Dict[str, Any]):
        ref = self.document()
        ref.set(data)
        return None, ref


class MockFirestoreDB:
    def __init__(self):
        self._collections: Dict[str, MockCollectionReference] = {}

    def collection(self, name: str) -> MockCollectionReference:
        if name not in self._collections:
            self._collections[name] = MockCollectionReference(self, name)
        return self._collections[name]


# =====================================================================
# Test Cases
# =====================================================================
class TestBackendSecurityAndFeatures(unittest.TestCase):
    def setUp(self):
        self.mock_db = MockFirestoreDB()

        # Patch db across all backend modules so no test ever touches real Firestore
        self.patchers = [
            patch("config.firebase.db", self.mock_db),
            patch("config.security.db", self.mock_db),
            patch("services.user_service.db", self.mock_db),
            patch("services.smoke_service.db", self.mock_db),
            patch("services.activity_service.db", self.mock_db),
            patch("services.chat_service.db", self.mock_db),
            patch("services.dashboard_service.db", self.mock_db),
            patch("services.photo_service.db", self.mock_db),
            patch("services.xray_service.db", self.mock_db),
        ]
        for p in self.patchers:
            p.start()

        from main import app
        from fastapi.testclient import TestClient
        from config.security import create_access_token, hash_password

        self.client = TestClient(app)
        self.create_access_token = create_access_token
        self.hash_password = hash_password

        # Seed mock accounts: patient1, patient2, doctor1 (authorized for patient1 only), admin1
        accounts = self.mock_db.collection("accounts")
        accounts.document("patient_1").set({
            "name": "Patient One",
            "email": "p1@example.com",
            "password": hash_password("Pass1234"),
            "role": "user",
            "assigned_doctor_uid": "doctor_1",
            "smoke_limit_low": 5,
            "smoke_limit_high": 10,
            "onboarding_completed": True,
        })
        accounts.document("patient_2").set({
            "name": "Patient Two",
            "email": "p2@example.com",
            "password": hashlib.sha256("LegacyPass1".encode("utf-8")).hexdigest(),  # legacy SHA-256
            "role": "user",
            "smoke_limit_low": 5,
            "smoke_limit_high": 10,
            "onboarding_completed": True,
        })
        accounts.document("doctor_1").set({
            "name": "Dr. One",
            "email": "doc1@example.com",
            "password": hash_password("DocPass123"),
            "role": "dokter",
            "assigned_patients": ["patient_1"],
        })
        accounts.document("admin_1").set({
            "name": "Master Admin",
            "email": "admin@example.com",
            "password": hash_password("AdminPass123"),
            "role": "master",
        })

        self.token_p1 = create_access_token("patient_1", "user")
        self.token_p2 = create_access_token("patient_2", "user")
        self.token_doc1 = create_access_token("doctor_1", "dokter")
        self.token_admin = create_access_token("admin_1", "master")

    def tearDown(self):
        for p in reversed(self.patchers):
            p.stop()

    def _headers(self, token: str) -> Dict[str, str]:
        return {"Authorization": f"Bearer {token}"}

    # -----------------------------------------------------------------
    # 1. Password Security & Registration Role Enforcement
    # -----------------------------------------------------------------
    def test_public_registration_forces_patient_role_and_hashes_password(self):
        resp = self.client.post(
            "/api/users/register",
            json={
                "name": "Attacker",
                "email": "attacker@example.com",
                "password": "StrongPassword1",
                "role": "master",  # Attempt privilege escalation
            },
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["status"], "success")
        data = body["data"]
        # Role MUST be forced to "user"
        self.assertEqual(data["role"], "user")
        self.assertIn("access_token", data)
        self.assertNotIn("password", data)

        # Verify stored hash in DB is bcrypt/argon2, not plaintext or raw SHA-256
        uid = data["uid"]
        stored_doc = self.mock_db.collection("accounts").document(uid).get().to_dict()
        self.assertEqual(stored_doc["role"], "user")
        self.assertTrue(
            stored_doc["password"].startswith(("$2b$", "$2a$", "$argon2")),
            f"Unexpected hash prefix: {stored_doc['password'][:10]}",
        )

    def test_legacy_sha256_account_can_login_without_data_loss(self):
        resp = self.client.post(
            "/api/users/login",
            json={"email": "p2@example.com", "password": "LegacyPass1"},
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()["data"]
        self.assertEqual(data["uid"], "patient_2")
        self.assertIn("access_token", data)

    def test_unified_password_validation_rejects_weak_passwords(self):
        # Weak password on register
        resp_reg = self.client.post(
            "/api/users/register",
            json={"name": "Weak", "email": "weak@example.com", "password": "123456"},
        )
        self.assertEqual(resp_reg.status_code, 422)

        # Weak password on change-password
        resp_chg = self.client.put(
            "/api/users/patient_1/change-password",
            headers=self._headers(self.token_p1),
            json={"current_password": "Pass1234", "new_password": "short"},
        )
        self.assertEqual(resp_chg.status_code, 422)

    # -----------------------------------------------------------------
    # 2. Authentication, Role Management, Doctor Scope & IDOR Prevention
    # -----------------------------------------------------------------
    def test_protected_endpoints_reject_unauthenticated_requests(self):
        self.assertEqual(self.client.get("/api/users/").status_code, 401)
        self.assertEqual(self.client.get("/api/users/patients").status_code, 401)
        self.assertEqual(self.client.get("/api/users/patient_1").status_code, 401)
        self.assertEqual(
            self.client.get("/api/smoke-count/?user_id=patient_1&timestamp=2026-11-09").status_code,
            401,
        )
        self.assertEqual(
            self.client.get("/api/dashboard/?user_id=patient_1&date=2026-11-09").status_code,
            401,
        )

    def test_only_admin_can_list_all_users_and_manage_roles(self):
        # Patient and Doctor cannot access GET /api/users/
        self.assertEqual(
            self.client.get("/api/users/", headers=self._headers(self.token_p1)).status_code,
            403,
        )
        self.assertEqual(
            self.client.get("/api/users/", headers=self._headers(self.token_doc1)).status_code,
            403,
        )
        # Admin can access GET /api/users/
        admin_list = self.client.get("/api/users/", headers=self._headers(self.token_admin))
        self.assertEqual(admin_list.status_code, 200)
        self.assertEqual(len(admin_list.json()["data"]), 4)

        # Patient cannot escalate their own role via PUT /api/users/{uid}/edit
        escalate_resp = self.client.put(
            "/api/users/patient_1/edit",
            headers=self._headers(self.token_p1),
            json={"role": "master"},
        )
        self.assertEqual(escalate_resp.status_code, 403)

        # Admin can change a user's role
        promote_resp = self.client.put(
            "/api/users/patient_2/edit",
            headers=self._headers(self.token_admin),
            json={"role": "dokter"},
        )
        self.assertEqual(promote_resp.status_code, 200)
        self.assertEqual(promote_resp.json()["data"]["role"], "dokter")

    def test_doctor_can_only_access_explicitly_authorized_patients(self):
        # GET /api/users/patients for doctor_1 should only return patient_1 (not patient_2)
        resp = self.client.get("/api/users/patients", headers=self._headers(self.token_doc1))
        self.assertEqual(resp.status_code, 200)
        patients = resp.json()["data"]
        returned_uids = [p["uid"] for p in patients]
        self.assertEqual(returned_uids, ["patient_1"])

        # Doctor can view authorized patient_1's daily report
        rep_ok = self.client.get(
            "/api/users/patient_1/daily-report?date=2026-11-09",
            headers=self._headers(self.token_doc1),
        )
        self.assertEqual(rep_ok.status_code, 200)

        # Doctor CANNOT view unauthorized patient_2's daily report or profile
        rep_forbidden = self.client.get(
            "/api/users/patient_2/daily-report?date=2026-11-09",
            headers=self._headers(self.token_doc1),
        )
        self.assertEqual(rep_forbidden.status_code, 403)

        # Doctor CANNOT edit patient_1's personal account info
        edit_forbidden = self.client.put(
            "/api/users/patient_1/edit",
            headers=self._headers(self.token_doc1),
            json={"name": "Modified by Doctor"},
        )
        self.assertEqual(edit_forbidden.status_code, 403)

    def test_users_can_only_change_their_own_password_with_verified_current_password(self):
        # Cannot change another user's password (IDOR)
        idor_resp = self.client.put(
            "/api/users/patient_2/change-password",
            headers=self._headers(self.token_p1),
            json={"current_password": "LegacyPass1", "new_password": "NewStrongPass1"},
        )
        self.assertEqual(idor_resp.status_code, 403)

        # Wrong current_password rejected with 401
        wrong_curr = self.client.put(
            "/api/users/patient_1/change-password",
            headers=self._headers(self.token_p1),
            json={"current_password": "WrongPassword1", "new_password": "NewStrongPass1"},
        )
        self.assertEqual(wrong_curr.status_code, 401)

        # Correct current_password succeeds
        ok_resp = self.client.put(
            "/api/users/patient_1/change-password",
            headers=self._headers(self.token_p1),
            json={"current_password": "Pass1234", "new_password": "NewStrongPass1"},
        )
        self.assertEqual(ok_resp.status_code, 200)

    # -----------------------------------------------------------------
    # 3. Smoke Count, Dashboard & Timezone / Consistency Tests
    # -----------------------------------------------------------------
    def test_smoke_count_validation_and_monthly_dashboard_consistency(self):
        # Negative smoke count must be rejected with 422
        neg_resp = self.client.post(
            "/api/smoke-count/",
            headers=self._headers(self.token_p1),
            json={"user_id": "patient_1", "timestamp": "2026-11-09", "total": -3},
        )
        self.assertEqual(neg_resp.status_code, 422)

        # Valid smoke count (accepts both YYYY-MM-DD and DD/MM/YYYY, normalizes to YYYY-MM-DD)
        ok_smoke = self.client.post(
            "/api/smoke-count/",
            headers=self._headers(self.token_p1),
            json={"user_id": "patient_1", "timestamp": "09/11/2026", "total": 4},
        )
        self.assertEqual(ok_smoke.status_code, 200)
        self.assertEqual(ok_smoke.json()["data"]["count"], 4)

        # Add a daily activity and mark it done
        add_act = self.client.post(
            "/api/users/patient_1/activities",
            headers=self._headers(self.token_doc1),
            json={"date": "2026-11-09", "activity": "Jalan pagi 15 menit"},
        )
        self.assertEqual(add_act.status_code, 200)
        act_id = add_act.json()["data"]["id"]

        chk_resp = self.client.patch(
            f"/api/activities/patient_1_2026-11-09/items/{act_id}/check",
            headers=self._headers(self.token_p1),
            json={"done": True},
        )
        self.assertEqual(chk_resp.status_code, 200)

        # Seed a chat session with a message at 2026-11-08T18:30:00Z (which is 2026-11-09 01:30 WIB!)
        chat_doc = self.mock_db.collection("chats").document("chat_session_1")
        chat_doc.set({"user": "patient_1", "sessionID": "sess_1", "title": "Test Chat"})
        chat_doc.collection("messages").document("m1").set({
            "role": "user",
            "response": "Halo dok",
            "timestamp": "2026-11-08T18:30:00+00:00",
        })

        # Daily dashboard on 2026-11-09 (WIB) should count smoke=4, done_activity=1, chat=1
        dash = self.client.get(
            "/api/dashboard/?user_id=patient_1&date=2026-11-09",
            headers=self._headers(self.token_p1),
        )
        self.assertEqual(dash.status_code, 200)
        dash_data = dash.json()
        self.assertEqual(dash_data["smoke_count_total"], 4)
        self.assertEqual(dash_data["daily_activities_done_total"], 1)
        self.assertEqual(dash_data["chat_total"], 1)

        # Monthly dashboard for 2026-11 should also match (total_smoke_count=4, total_activities=1, total_chats=1)
        monthly = self.client.get(
            "/api/dashboard/monthly?user_id=patient_1&year=2026&month=11",
            headers=self._headers(self.token_p1),
        )
        self.assertEqual(monthly.status_code, 200)
        m_data = monthly.json()["data"]
        self.assertEqual(m_data["total_smoke_count"], 4)
        self.assertEqual(m_data["total_activities"], 1)
        self.assertEqual(m_data["total_chats"], 1)
        self.assertIn("2026-11-09", m_data["available_dates"])

    # -----------------------------------------------------------------
    # 4. Emergency Detection & Multi-Agent Configuration Tests
    # -----------------------------------------------------------------
    def test_emergency_detection_categories_negation_and_scenarios(self):
        from services.chat_service import check_emergency

        # True emergencies across all 5 required categories:
        # 1. Chest pain
        self.assertTrue(check_emergency("Saya mengalami nyeri dada hebat yang menjalar ke lengan kiri."))
        # 2. Severe breathing difficulties / cyanosis (Scenario S10)
        self.assertTrue(
            check_emergency("Napas saya sangat sesak bahkan saat duduk diam, bibir tampak kebiruan.")
        )
        # 3. Possible stroke symptoms
        self.assertTrue(check_emergency("Ayah saya tiba-tiba wajah mencong dan bicara pelo."))
        # 4. Loss of consciousness
        self.assertTrue(check_emergency("Pasien baru saja pingsan dan tidak sadarkan diri."))
        # 5. Coughing up significant blood (Scenario S09)
        self.assertTrue(
            check_emergency("Saya baru saja batuk mengeluarkan darah segar sekitar dua sendok makan disertai nyeri dada.")
        )

        # Non-emergencies & negated statements (must NOT false-positive!):
        # Scenario S01 (negated blood)
        self.assertFalse(
            check_emergency("Saya batuk kering sudah 5 hari, terutama malam hari. Tidak ada demam dan tidak ada darah pada dahak.")
        )
        # Scenario S02 (negated chest pain)
        self.assertFalse(
            check_emergency("Setiap naik tangga 2 lantai saya merasa agak ngos-ngosan, saya perokok aktif. Tidak ada nyeri dada.")
        )
        # Generic words like "tolong" or "darurat" in non-emergency questions
        self.assertFalse(
            check_emergency("Tolong berikan tips berhenti merokok dan cara menyiapkan rencana darurat saat craving.")
        )

    def test_multi_agent_tool_consistency(self):
        from myAgent.agent import root_agent, symptom_analyzer, home_remedies
        from tools import search_medical_journal

        self.assertEqual(root_agent.name, "healthcare_coordinator")
        self.assertEqual(symptom_analyzer.name, "symptom_analyzer")
        self.assertEqual(home_remedies.name, "home_remedies_advisor")

        # Both symptom_analyzer and home_remedies must use search_medical_journal (not google_search)
        self.assertIn(search_medical_journal, symptom_analyzer.tools)
        self.assertIn(search_medical_journal, home_remedies.tools)

    # -----------------------------------------------------------------
    # 5. RAG Retrieval, DOI Extraction & Garbled PDF Detection Tests
    # -----------------------------------------------------------------
    def test_rag_search_and_doi_extraction(self):
        from services.rag_service import (
            extract_doi_and_url,
            is_garbled_text,
            search_journal,
        )

        # Garbled text detection
        self.assertTrue(is_garbled_text("ZK[KF ZIN FZ\\OIS K Kqqomt {qt{lkmm{ €y{vtzr {ztso~t€v{q"))
        self.assertFalse(
            is_garbled_text("Smoking cessation significantly reduces the rate of lung function decline in COPD patients.")
        )

        # DOI extraction
        doi, url, _ = extract_doi_and_url("Published online: doi: 10.1164/rccm.202301-0106PP.")
        self.assertEqual(doi, "10.1164/rccm.202301-0106PP")
        self.assertEqual(url, "https://doi.org/10.1164/rccm.202301-0106PP")

        # Empty query handling
        self.assertIn("Tidak ditemukan", search_journal(""))

        # Real query against local ChromaDB returns source filename, page number, and actual DOI/URL (never ?term= pubmed search)
        result = search_journal("COPD smoking cessation")
        self.assertIn("REFERENSI JURNAL MEDIS:", result)
        self.assertIn("Halaman", result)
        self.assertIn("https://doi.org/", result)
        self.assertNotIn("pubmed.ncbi.nlm.nih.gov/?term=", result)

    # -----------------------------------------------------------------
    # 6. JWT Secret Validation Regression Tests (Task 1)
    # -----------------------------------------------------------------
    def test_jwt_secret_validation_missing_weak_and_valid(self):
        from config.security import (
            create_access_token,
            decode_access_token,
            get_jwt_secret_key,
            validate_jwt_secret,
        )

        # Missing secrets must raise RuntimeError
        with self.assertRaises(RuntimeError):
            validate_jwt_secret(None)
        with self.assertRaises(RuntimeError):
            validate_jwt_secret("")
        with self.assertRaises(RuntimeError):
            validate_jwt_secret("   ")

        # Weak/short secrets (< 32 chars) must raise RuntimeError
        with self.assertRaises(RuntimeError):
            validate_jwt_secret("short-secret-12345")

        # Predictable fallback or placeholder prefixes must raise RuntimeError
        with self.assertRaises(RuntimeError):
            validate_jwt_secret("lungs-assistant-jwt-secret::predictable-seed-value")
        with self.assertRaises(RuntimeError):
            validate_jwt_secret("secret_123456789012345678901234567890")

        # Low-entropy repeated character secret must raise RuntimeError
        with self.assertRaises(RuntimeError):
            validate_jwt_secret("a" * 64)

        # Missing env variable at runtime must fail safely
        with patch.dict(os.environ, {"JWT_SECRET_KEY": ""}, clear=False):
            with self.assertRaises(RuntimeError):
                get_jwt_secret_key()
            with self.assertRaises(RuntimeError):
                create_access_token("patient_1", "user")

        # Valid strong secret succeeds for token creation and verification
        valid_secret = "9f8e7d6c5b4a3f2e1d0c9b8a7f6e5d4c3b2a1f0e9d8c7b6a5f4e3d2c1b0a9f8e"
        with patch.dict(os.environ, {"JWT_SECRET_KEY": valid_secret}, clear=False):
            self.assertEqual(get_jwt_secret_key(), valid_secret)
            tok = create_access_token("patient_1", "user")
            decoded = decode_access_token(tok)
            self.assertEqual(decoded["sub"], "patient_1")
            self.assertEqual(decoded["role"], "user")

    # -----------------------------------------------------------------
    # 7. Google Places Hospital Proxy Endpoint Tests (Task 3)
    # -----------------------------------------------------------------
    def test_nearby_hospitals_backend_proxy_auth_and_filtering(self):
        # Unauthenticated request must return 401
        unauth = self.client.get("/api/hospitals/nearby?lat=-6.2&lng=106.8")
        self.assertEqual(unauth.status_code, 401)

        # Out-of-range coordinates must return 422
        bad_coords = self.client.get(
            "/api/hospitals/nearby?lat=120.0&lng=106.8",
            headers=self._headers(self.token_p1),
        )
        self.assertEqual(bad_coords.status_code, 422)

        # Mock Google Places API response and verify general hospital filtering
        mock_places_payload = {
            "status": "OK",
            "results": [
                {
                    "name": "RSUD Pasar Minggu",
                    "place_id": "place_rsud_1",
                    "geometry": {"location": {"lat": -6.294, "lng": 106.820}},
                    "vicinity": "Jl. TB Simatupang No. 1",
                    "rating": 4.5,
                    "opening_hours": {"open_now": True},
                },
                {
                    "name": "RSIA Brawijaya",
                    "place_id": "place_rsia_2",
                    "geometry": {"location": {"lat": -6.250, "lng": 106.800}},
                    "vicinity": "Jl. Taman Brawijaya",
                    "rating": 4.6,
                },
                {
                    "name": "Klinik Pratama Sehat",
                    "place_id": "place_klinik_3",
                    "geometry": {"location": {"lat": -6.260, "lng": 106.810}},
                    "vicinity": "Jl. Raya",
                },
                {
                    "name": "RS Khusus Mata Nusantara",
                    "place_id": "place_mata_4",
                    "geometry": {"location": {"lat": -6.270, "lng": 106.815}},
                    "vicinity": "Jl. Nusantara",
                },
            ],
        }

        class DummyResponse:
            status_code = 200
            content = b"{}"

            @staticmethod
            def json():
                return mock_places_payload

        with patch("services.hospital_service.requests.get", return_value=DummyResponse()):
            resp = self.client.get(
                "/api/hospitals/nearby?lat=-6.29&lng=106.82&radius=3000",
                headers=self._headers(self.token_p1),
            )
            self.assertEqual(resp.status_code, 200)
            body = resp.json()
            self.assertEqual(body["status"], "success")
            hospitals = body["data"]
            self.assertEqual(len(hospitals), 1)
            self.assertEqual(hospitals[0]["name"], "RSUD Pasar Minggu")
            self.assertEqual(hospitals[0]["doctor_schedules"], [])

    # -----------------------------------------------------------------
    # 8. RAG Re-Ingestion & Corrupted PDF 58 Exclusion Tests (Task 4 & 5)
    # -----------------------------------------------------------------
    def test_rag_document_level_reingestion_and_corrupted_pdf58_exclusion(self):
        import tempfile
        from PyPDF2 import PdfReader
        from services.rag_service import (
            ingest_pdfs_from_directory,
            is_garbled_text,
            search_journal,
        )

        # In-memory mock ChromaDB collection to test document-level chunk replacement & rollback safely
        class InMemoryChromaCollection:
            def __init__(self):
                self.store: Dict[str, Dict[str, Any]] = {}
                self.op_log: List[str] = []
                self.fail_next_upsert: bool = False
                self.fail_next_delete: bool = False

            def get(self, where: Optional[Dict[str, Any]] = None, include: Optional[List[str]] = None):
                matched_ids = []
                matched_metas = []
                matched_docs = []
                for cid, entry in self.store.items():
                    if where:
                        ok = all(entry["metadata"].get(k) == v for k, v in where.items())
                        if not ok:
                            continue
                    matched_ids.append(cid)
                    matched_metas.append(dict(entry["metadata"]))
                    matched_docs.append(entry["document"])
                return {"ids": matched_ids, "metadatas": matched_metas, "documents": matched_docs}

            def upsert(self, documents: List[str], ids: List[str], metadatas: List[Dict[str, Any]]):
                self.op_log.append("upsert")
                if self.fail_next_upsert:
                    self.fail_next_upsert = False
                    # Simulate partial write before exception
                    if ids:
                        self.store[ids[0]] = {"document": "PARTIAL_CORRUPT_WRITE", "metadata": dict(metadatas[0])}
                    raise RuntimeError("Simulated ChromaDB upsert failure")
                for cid, doc, meta in zip(ids, documents, metadatas):
                    self.store[cid] = {"document": doc, "metadata": dict(meta)}

            def delete(self, ids: List[str]):
                self.op_log.append("delete")
                if self.fail_next_delete:
                    self.fail_next_delete = False
                    raise RuntimeError("Simulated ChromaDB delete interruption")
                for cid in ids:
                    self.store.pop(cid, None)

        class FakePage:
            def __init__(self, text: str):
                self._text = text

            def extract_text(self) -> str:
                return self._text

        class FakePdfReader:
            def __init__(self, pages_text: List[str], is_encrypted: bool = False):
                self.pages = [FakePage(t) for t in pages_text]
                self.metadata = {"/Title": "Clinical COPD Study"}
                self.is_encrypted = is_encrypted

            def decrypt(self, password: str) -> int:
                return 0 if self.is_encrypted else 1

        mock_col = InMemoryChromaCollection()

        with tempfile.TemporaryDirectory() as tmpdir:
            sample_pdf = os.path.join(tmpdir, "sample_study.pdf")
            encrypted_pdf = os.path.join(tmpdir, "encrypted_study.pdf")
            with open(sample_pdf, "wb") as f:
                f.write(b"%PDF-1.4 dummy")
            with open(encrypted_pdf, "wb") as f:
                f.write(b"%PDF-1.4 encrypted")

            four_pages = [
                f"Page {idx} clinical trial findings on chronic obstructive pulmonary disease and smoking cessation doi: 10.1000/test.{idx}"
                for idx in range(1, 5)
            ]

            def reader_factory_v1(filepath):
                if filepath.endswith("encrypted_study.pdf"):
                    return FakePdfReader([], is_encrypted=True)
                return FakePdfReader(four_pages)

            with patch("services.rag_service.PdfReader", side_effect=reader_factory_v1):
                summary1 = ingest_pdfs_from_directory(tmpdir, target_collection=mock_col)

            self.assertEqual(summary1["processed"], ["sample_study.pdf"])
            self.assertEqual(summary1["corrupted_or_unreadable"], ["encrypted_study.pdf"])
            self.assertEqual(len(mock_col.store), 4)
            self.assertIn("sample_study.pdf_page_3", mock_col.store)

            # Repeated ingestion of the same 4-page PDF produces zero duplicates
            with patch("services.rag_service.PdfReader", side_effect=reader_factory_v1):
                summary_repeat = ingest_pdfs_from_directory(tmpdir, target_collection=mock_col)
            self.assertEqual(len(mock_col.store), 4)
            self.assertEqual(summary_repeat["obsolete_chunks_removed"], [])

            # Simulate upsert failure when attempting to replace 4-page PDF with 2-page PDF:
            # All 4 original pages must be preserved/restored via rollback!
            two_pages = four_pages[:2]

            def reader_factory_v2(filepath):
                if filepath.endswith("encrypted_study.pdf"):
                    return FakePdfReader([], is_encrypted=True)
                return FakePdfReader(two_pages)

            mock_col.fail_next_upsert = True
            with patch("services.rag_service.PdfReader", side_effect=reader_factory_v2):
                summary_fail_upsert = ingest_pdfs_from_directory(tmpdir, target_collection=mock_col)

            self.assertEqual(summary_fail_upsert["rolled_back"], ["sample_study.pdf"])
            self.assertEqual(len(mock_col.store), 4)
            self.assertNotEqual(mock_col.store["sample_study.pdf_page_0"]["document"], "PARTIAL_CORRUPT_WRITE")
            self.assertIn("sample_study.pdf_page_3", mock_col.store)

            # Simulate interruption during obsolete chunk deletion: must also roll back to 4 original pages
            mock_col.fail_next_delete = True
            with patch("services.rag_service.PdfReader", side_effect=reader_factory_v2):
                summary_fail_del = ingest_pdfs_from_directory(tmpdir, target_collection=mock_col)

            self.assertEqual(summary_fail_del["rolled_back"], ["sample_study.pdf"])
            self.assertEqual(len(mock_col.store), 4)
            self.assertIn("sample_study.pdf_page_3", mock_col.store)

            # Successful replacement: verify upsert happens BEFORE delete, and obsolete pages 2 & 3 are removed
            mock_col.op_log.clear()
            with patch("services.rag_service.PdfReader", side_effect=reader_factory_v2):
                summary2 = ingest_pdfs_from_directory(tmpdir, target_collection=mock_col)

            self.assertEqual(mock_col.op_log, ["upsert", "delete"])
            self.assertEqual(len(mock_col.store), 2)
            self.assertIn("sample_study.pdf_page_0", mock_col.store)
            self.assertIn("sample_study.pdf_page_1", mock_col.store)
            self.assertNotIn("sample_study.pdf_page_2", mock_col.store)
            self.assertNotIn("sample_study.pdf_page_3", mock_col.store)
            self.assertEqual(
                mock_col.store["sample_study.pdf_page_0"]["metadata"]["doc_id"],
                "sample_study.pdf",
            )
            self.assertEqual(
                sorted(summary2["obsolete_chunks_removed"]),
                ["sample_study.pdf_page_2", "sample_study.pdf_page_3"],
            )

        # Verify 58.pdf in TA_Backend/data/58.pdf has corrupted font encoding and is excluded from RAG retrieval
        pdf58_path = os.path.join(BACKEND_DIR, "data", "58.pdf")
        self.assertTrue(os.path.exists(pdf58_path))
        reader58 = PdfReader(pdf58_path)
        self.assertEqual(len(reader58.pages), 18)
        for p in reader58.pages:
            self.assertTrue(is_garbled_text(p.extract_text() or ""))

        rag_res = search_journal(
            "Effect of tobacco smoking on the risk of developing community acquired pneumonia",
            n_results=5,
        )
        self.assertNotIn("Sumber Jurnal / Dokumen: 58.pdf", rag_res)
        self.assertNotIn("ZK[KF", rag_res)

    # -----------------------------------------------------------------
    # 9. Dependencies & Cloud Run Deployment Script Validation (Task 1 & 5)
    # -----------------------------------------------------------------
    def test_dependencies_and_deployment_script_configuration(self):
        req_path = os.path.join(BACKEND_DIR, "Requirements.txt")
        with open(req_path, "r") as f:
            reqs = [line.strip().lower() for line in f if line.strip() and not line.startswith("#")]

        for expected_pkg in ("fastapi", "requests", "pyjwt", "bcrypt", "chromadb==1.5.9", "pypdf2"):
            self.assertIn(expected_pkg, reqs)

        deploy_path = os.path.join(BACKEND_DIR, "deploy.sh")
        with open(deploy_path, "r") as f:
            deploy_script = f.read()

        self.assertIn("JWT_SECRET_KEY=${JWT_SECRET_NAME}:latest", deploy_script)
        self.assertIn("GOOGLE_MAPS_API_KEY=${MAPS_SECRET_NAME}:latest", deploy_script)
        self.assertIn("GOOGLE_API_KEY=${SECRET_NAME}:latest", deploy_script)
        self.assertIn("ensure_jwt_secret", deploy_script)
        self.assertIn("ensure_maps_secret", deploy_script)

    # -----------------------------------------------------------------
    # 10. Yesterday AI Summary & Feedback Endpoint Tests
    # -----------------------------------------------------------------
    def test_yesterday_ai_summary_endpoint_and_caching(self):
        # Seed yesterday's data (2026-11-08 when today is 2026-11-09)
        ok_smoke = self.client.post(
            "/api/smoke-count/",
            headers=self._headers(self.token_p1),
            json={"user_id": "patient_1", "timestamp": "2026-11-08", "total": 3},
        )
        self.assertEqual(ok_smoke.status_code, 200)

        # Add 2 activities on 2026-11-08, mark 1 done and leave 1 pending
        act1 = self.client.post(
            "/api/users/patient_1/activities",
            headers=self._headers(self.token_doc1),
            json={"date": "2026-11-08", "activity": "Latihan pernapasan diafragma"},
        )
        act2 = self.client.post(
            "/api/users/patient_1/activities",
            headers=self._headers(self.token_doc1),
            json={"date": "2026-11-08", "activity": "Minum air hangat 8 gelas"},
        )
        act1_id = act1.json()["data"]["id"]
        self.client.patch(
            f"/api/activities/patient_1_2026-11-08/items/{act1_id}/check",
            headers=self._headers(self.token_p1),
            json={"done": True},
        )

        # Unauthorized patient_2 cannot read patient_1's yesterday AI summary
        forbidden = self.client.get(
            "/api/dashboard/yesterday-ai-summary?user_id=patient_1&date=2026-11-09",
            headers=self._headers(self.token_p2),
        )
        self.assertEqual(forbidden.status_code, 403)

        # Patient_1 fetches yesterday AI summary (with mocked Gemini response)
        class FakeGeminiResponse:
            text = (
                '{"summary": "Kemarin Anda merokok 3 batang dan menyelesaikan 1 dari 2 aktivitas harian.", '
                '"feedback": "Pertahankan rokok di bawah batas rendah dan selesaikan target minum air hangat hari ini."}'
            )

        with patch.dict(os.environ, {"GOOGLE_API_KEY": "dummy-key"}, clear=False):
            with patch(
                "services.dashboard_service.genai.GenerativeModel.generate_content",
                return_value=FakeGeminiResponse(),
            ):
                resp = self.client.get(
                    "/api/dashboard/yesterday-ai-summary?user_id=patient_1&date=2026-11-09",
                    headers=self._headers(self.token_p1),
                )
        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertEqual(payload["status"], "success")
        data = payload["data"]
        self.assertEqual(data["yesterday_date"], "2026-11-08")
        self.assertEqual(data["smoke_count_total"], 3)
        self.assertEqual(data["daily_activities_done_total"], 1)
        self.assertEqual(data["daily_activities_total"], 2)
        self.assertEqual(data["source"], "gemini")
        self.assertIn("merokok 3 batang", data["summary"])
        self.assertIn("Pertahankan", data["feedback"])

        # Subsequent call with same data signature returns cached summary without calling Gemini
        resp_cached = self.client.get(
            "/api/dashboard/yesterday-ai-summary?user_id=patient_1&date=2026-11-09",
            headers=self._headers(self.token_p1),
        )
        self.assertEqual(resp_cached.status_code, 200)
        self.assertEqual(resp_cached.json()["data"]["source"], "cache")


if __name__ == "__main__":
    unittest.main()
