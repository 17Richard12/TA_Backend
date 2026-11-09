#!/usr/bin/env bash
# ============================================================================
#  Deploy backend "Lungs Assistant Agent" (FastAPI + Google ADK) ke Cloud Run
#
#  Aman dijalankan berulang kali (idempotent):
#    - resource Google Cloud yang sudah ada tidak dibuat ulang
#    - setiap deploy membangun image baru + revisi Cloud Run baru
#
#  Pemakaian (jalankan dari folder TA_Backend, tempat main.py berada):
#    ./deploy.sh                 deploy / update backend
#    ./deploy.sh secret          perbarui isi secret GOOGLE_API_KEY (Gemini)
#    ./deploy.sh jwt-secret      perbarui isi secret JWT_SECRET_KEY (min 32 karakter)
#    ./deploy.sh maps-secret     perbarui isi secret GOOGLE_MAPS_API_KEY (Places API)
#    ./deploy.sh firebase-secret perbarui isi secret Firebase service account
#    ./deploy.sh url             tampilkan URL layanan
#    ./deploy.sh logs            tampilkan log terbaru
#    ./deploy.sh rollback        daftar revisi terakhir
#    ./deploy.sh rollback REV    kembalikan 100% trafik ke revisi REV
#    ./deploy.sh fix-permissions beri izin build ke service account Cloud Build
#
#  Pengaturan bisa diubah lewat environment, contoh:
#    MIN_INSTANCES=1 ./deploy.sh          # tanpa cold start (hari demo/sidang)
#    PROJECT_ID=my-project REGION=asia-southeast1 ./deploy.sh
# ============================================================================
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

# ------------------------------ KONFIGURASI --------------------------------
PROJECT_ID="${PROJECT_ID:-$(gcloud config get-value project 2>/dev/null || true)}"
REGION="${REGION:-asia-southeast2}"                        # Jakarta
SERVICE="${SERVICE:-lungs-backend}"                        # nama layanan Cloud Run
REPO="${REPO:-lungs-backend}"                              # repository Artifact Registry
SA_NAME="${SA_NAME:-lungs-backend-sa}"                     # service account runtime
SECRET_NAME="${SECRET_NAME:-gemini-api-key}"               # secret GOOGLE_API_KEY di Secret Manager
JWT_SECRET_NAME="${JWT_SECRET_NAME:-jwt-secret-key}"       # secret JWT_SECRET_KEY di Secret Manager
MAPS_SECRET_NAME="${MAPS_SECRET_NAME:-google-maps-api-key}" # secret GOOGLE_MAPS_API_KEY di Secret Manager
FIREBASE_SECRET_NAME="${FIREBASE_SECRET_NAME:-firebase-key}" # secret service account Firebase

CPU="${CPU:-1}"
MEMORY="${MEMORY:-2Gi}"
MIN_INSTANCES="${MIN_INSTANCES:-0}"
# 1 instance saja: InMemoryRunner (ADK) menyimpan sesi di memori satu proses.
MAX_INSTANCES="${MAX_INSTANCES:-1}"
TIMEOUT="${TIMEOUT:-300}"                    # detik; cukup untuk respons SSE

SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
IMAGE_BASE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO}/${SERVICE}"

# ------------------------------- UTILITAS ----------------------------------
info() { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }
ok()   { printf '    \033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '    \033[33m! %s\033[0m\n' "$*" >&2; }
die()  { printf '\n\033[1;31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

read_env_var_from_dotenv() {
  local key="$1"
  if [[ -f .env ]]; then
    grep -E "^${key}=" .env | head -n1 | cut -d= -f2- | tr -d "\r\"'" || true
  fi
}

validate_jwt_secret_strength() {
  local s="${1:-}"
  [[ -n "$s" ]] || die "JWT_SECRET_KEY tidak boleh kosong."
  if (( ${#s} < 32 )); then
    die "JWT_SECRET_KEY terlalu pendek (minimal 32 karakter)."
  fi
  local lower
  lower="$(printf '%s' "$s" | tr '[:upper:]' '[:lower:]')"
  for prefix in "lungs-assistant-jwt-secret" "secret" "changeme" "password" "default" "your-secret-key" "test-secret"; do
    if [[ "$lower" == "${prefix}"* ]]; then
      die "JWT_SECRET_KEY menggunakan nilai placeholder/prediktabel yang tidak diizinkan."
    fi
  done
  local unique_chars
  unique_chars="$(printf '%s' "$s" | fold -w1 | sort -u | wc -l | tr -d ' ')"
  if (( unique_chars < 8 )); then
    die "JWT_SECRET_KEY memiliki entropi rendah (minimal 8 karakter unik)."
  fi
}

preflight() {
  command -v gcloud >/dev/null 2>&1 || die "gcloud CLI tidak ditemukan."
  [[ -n "$PROJECT_ID" ]] || die "Project belum diatur. Jalankan: gcloud config set project NAMA_PROJECT"
  ACCOUNT="$(gcloud auth list --filter=status:ACTIVE --format='value(account)' 2>/dev/null | head -n1)"
  [[ -n "$ACCOUNT" ]] || die "Belum login. Jalankan: gcloud auth login"
  [[ -f main.py && -f Dockerfile && -f Requirements.txt && -f constraints.txt ]] \
    || die "Jalankan dari folder backend dan pastikan main.py, Dockerfile, Requirements.txt, constraints.txt ada."
  echo "Project : $PROJECT_ID"
  echo "Region  : $REGION"
  echo "Layanan : $SERVICE"
  echo "Akun    : $ACCOUNT"
}

service_url() {
  gcloud run services describe "$SERVICE" --project "$PROJECT_ID" --region "$REGION" \
    --format='value(status.url)' 2>/dev/null || true
}

# ------------------------------ LANGKAH-LANGKAH ----------------------------
enable_apis() {
  info "Mengaktifkan API yang dibutuhkan"
  gcloud services enable \
    run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com \
    secretmanager.googleapis.com firestore.googleapis.com iam.googleapis.com \
    --project "$PROJECT_ID" >/dev/null
  ok "API aktif"

  if ! gcloud firestore databases list --project "$PROJECT_ID" --format='value(name)' 2>/dev/null | grep -q .; then
    info "Catatan: Database Firestore tidak berada di project '$PROJECT_ID'."
    info "Aplikasi akan menggunakan kredensial Firebase dari Secret Manager ($FIREBASE_SECRET_NAME)."
  fi
}

ensure_repo() {
  info "Memastikan repository Artifact Registry '$REPO'"
  if gcloud artifacts repositories describe "$REPO" --project "$PROJECT_ID" --location "$REGION" >/dev/null 2>&1; then
    ok "sudah ada"
  else
    gcloud artifacts repositories create "$REPO" --project "$PROJECT_ID" --location "$REGION" \
      --repository-format=docker --description="Image backend Lungs Assistant Agent" >/dev/null
    ok "dibuat"
  fi
}

ensure_service_account() {
  info "Memastikan service account runtime '$SA_NAME'"
  local created=0
  if gcloud iam service-accounts describe "$SA_EMAIL" --project "$PROJECT_ID" >/dev/null 2>&1; then
    ok "sudah ada"
  else
    gcloud iam service-accounts create "$SA_NAME" --project "$PROJECT_ID" \
      --display-name="Lungs backend (Cloud Run)" >/dev/null
    created=1; ok "dibuat"
  fi
  [[ $created -eq 1 ]] && { echo "    menunggu propagasi IAM..."; sleep 15; }

  # Izin runtime: baca/tulis Firestore dan baca secret
  for role in roles/datastore.user roles/secretmanager.secretAccessor; do
    gcloud projects add-iam-policy-binding "$PROJECT_ID" \
      --member="serviceAccount:${SA_EMAIL}" --role="$role" \
      --condition=None --quiet >/dev/null
  done
  ok "izin Firestore + Secret Manager terpasang"

  # Agar akun yang menjalankan skrip boleh memakai service account ini saat deploy
  if [[ "$ACCOUNT" == *"gserviceaccount.com" ]]; then member="serviceAccount:$ACCOUNT"; else member="user:$ACCOUNT"; fi
  gcloud iam service-accounts add-iam-policy-binding "$SA_EMAIL" --project "$PROJECT_ID" \
    --member="$member" --role="roles/iam.serviceAccountUser" --quiet >/dev/null
}

read_api_key() {
  # urutan: variabel GOOGLE_API_KEY -> berkas .env -> input tersembunyi (tanpa pernah mencetak nilai secret)
  local v="${GOOGLE_API_KEY:-}"
  if [[ -z "$v" ]]; then
    v="$(read_env_var_from_dotenv "GOOGLE_API_KEY")"
  fi
  if [[ -z "$v" ]]; then
    read -r -s -p "    Masukkan GOOGLE_API_KEY (Gemini): " v; echo >&2
  fi
  [[ -n "$v" ]] || die "GOOGLE_API_KEY kosong."
  printf '%s' "$v"
}

read_jwt_secret() {
  # urutan: variabel JWT_SECRET_KEY -> berkas .env -> input tersembunyi (tanpa pernah mencetak nilai secret)
  local v="${JWT_SECRET_KEY:-}"
  if [[ -z "$v" ]]; then
    v="$(read_env_var_from_dotenv "JWT_SECRET_KEY")"
  fi
  if [[ -z "$v" ]]; then
    read -r -s -p "    Masukkan JWT_SECRET_KEY (minimal 32 karakter): " v; echo >&2
  fi
  validate_jwt_secret_strength "$v"
  printf '%s' "$v"
}

read_maps_api_key() {
  # urutan: variabel GOOGLE_MAPS_API_KEY / GOOGLE_PLACES_API_KEY -> berkas .env -> input tersembunyi
  local v="${GOOGLE_MAPS_API_KEY:-${GOOGLE_PLACES_API_KEY:-}}"
  if [[ -z "$v" ]]; then
    v="$(read_env_var_from_dotenv "GOOGLE_MAPS_API_KEY")"
  fi
  if [[ -z "$v" ]]; then
    v="$(read_env_var_from_dotenv "GOOGLE_PLACES_API_KEY")"
  fi
  if [[ -z "$v" ]]; then
    read -r -s -p "    Masukkan GOOGLE_MAPS_API_KEY (Google Places API): " v; echo >&2
  fi
  [[ -n "$v" ]] || die "GOOGLE_MAPS_API_KEY kosong. Endpoint /api/hospitals/nearby membutuhkan kunci Google Places."
  printf '%s' "$v"
}

grant_secret_access() {
  local sec_name="$1"
  gcloud secrets add-iam-policy-binding "$sec_name" --project "$PROJECT_ID" \
    --member="serviceAccount:${SA_EMAIL}" --role="roles/secretmanager.secretAccessor" --quiet >/dev/null 2>&1 || true
}

ensure_secret() {
  local force="${1:-0}"
  info "Memastikan secret '$SECRET_NAME' (GOOGLE_API_KEY)"
  if gcloud secrets describe "$SECRET_NAME" --project "$PROJECT_ID" >/dev/null 2>&1; then
    if [[ "$force" == "1" ]]; then
      read_api_key | gcloud secrets versions add "$SECRET_NAME" --project "$PROJECT_ID" --data-file=- >/dev/null
      ok "versi baru ditambahkan"
    else
      ok "sudah ada (pakai './deploy.sh secret' untuk memperbarui isinya)"
    fi
  else
    read_api_key | gcloud secrets create "$SECRET_NAME" --project "$PROJECT_ID" \
      --replication-policy=automatic --data-file=- >/dev/null
    ok "dibuat"
  fi
  grant_secret_access "$SECRET_NAME"
}

ensure_jwt_secret() {
  local force="${1:-0}"
  info "Memastikan secret '$JWT_SECRET_NAME' (JWT_SECRET_KEY)"
  if gcloud secrets describe "$JWT_SECRET_NAME" --project "$PROJECT_ID" >/dev/null 2>&1; then
    if [[ "$force" == "1" ]]; then
      read_jwt_secret | gcloud secrets versions add "$JWT_SECRET_NAME" --project "$PROJECT_ID" --data-file=- >/dev/null
      ok "versi baru ditambahkan"
    else
      # Validasi nilai yang sudah ada di Secret Manager agar aplikasi tidak gagal startup di Cloud Run
      local existing_jwt
      existing_jwt="$(gcloud secrets versions access latest --secret="$JWT_SECRET_NAME" --project "$PROJECT_ID" 2>/dev/null || true)"
      if [[ -z "$existing_jwt" ]] || (( ${#existing_jwt} < 32 )); then
        warn "Secret '$JWT_SECRET_NAME' di Secret Manager kosong atau kurang dari 32 karakter; memperbarui..."
        read_jwt_secret | gcloud secrets versions add "$JWT_SECRET_NAME" --project "$PROJECT_ID" --data-file=- >/dev/null
        ok "versi valid baru ditambahkan"
      else
        validate_jwt_secret_strength "$existing_jwt"
        ok "sudah ada dan memenuhi syarat keamanan (pakai './deploy.sh jwt-secret' untuk memperbarui)"
      fi
    fi
  else
    read_jwt_secret | gcloud secrets create "$JWT_SECRET_NAME" --project "$PROJECT_ID" \
      --replication-policy=automatic --data-file=- >/dev/null
    ok "dibuat"
  fi
  grant_secret_access "$JWT_SECRET_NAME"
}

ensure_maps_secret() {
  local force="${1:-0}"
  info "Memastikan secret '$MAPS_SECRET_NAME' (GOOGLE_MAPS_API_KEY)"
  if gcloud secrets describe "$MAPS_SECRET_NAME" --project "$PROJECT_ID" >/dev/null 2>&1; then
    if [[ "$force" == "1" ]]; then
      read_maps_api_key | gcloud secrets versions add "$MAPS_SECRET_NAME" --project "$PROJECT_ID" --data-file=- >/dev/null
      ok "versi baru ditambahkan"
    else
      local existing_maps
      existing_maps="$(gcloud secrets versions access latest --secret="$MAPS_SECRET_NAME" --project "$PROJECT_ID" 2>/dev/null || true)"
      if [[ -z "$existing_maps" ]]; then
        warn "Secret '$MAPS_SECRET_NAME' kosong; memperbarui..."
        read_maps_api_key | gcloud secrets versions add "$MAPS_SECRET_NAME" --project "$PROJECT_ID" --data-file=- >/dev/null
        ok "versi baru ditambahkan"
      else
        ok "sudah ada (pakai './deploy.sh maps-secret' untuk memperbarui isinya)"
      fi
    fi
  else
    read_maps_api_key | gcloud secrets create "$MAPS_SECRET_NAME" --project "$PROJECT_ID" \
      --replication-policy=automatic --data-file=- >/dev/null
    ok "dibuat"
  fi
  grant_secret_access "$MAPS_SECRET_NAME"
}

find_firebase_json() {
  local f=""
  if [[ -f .env ]]; then
    f="$(read_env_var_from_dotenv "FIREBASE_PATH")"
  fi
  if [[ -n "$f" && -f "$f" ]]; then
    printf '%s' "$f"
    return
  fi
  for pattern in config/*firebase*.json config/*service*.json *firebase*.json; do
    if compgen -G "$pattern" > /dev/null 2>&1; then
      # shellcheck disable=SC2086
      set -- $pattern
      printf '%s' "$1"
      return
    fi
  done
}

ensure_firebase_secret() {
  local force="${1:-0}"
  info "Memastikan secret Firebase service account '$FIREBASE_SECRET_NAME'"
  local json_file
  json_file="$(find_firebase_json || true)"

  if gcloud secrets describe "$FIREBASE_SECRET_NAME" --project "$PROJECT_ID" >/dev/null 2>&1; then
    if [[ "$force" == "1" ]]; then
      [[ -n "$json_file" && -f "$json_file" ]] || die "File service account Firebase JSON tidak ditemukan."
      gcloud secrets versions add "$FIREBASE_SECRET_NAME" --project "$PROJECT_ID" --data-file="$json_file" >/dev/null
      ok "versi baru ditambahkan dari $json_file"
    else
      ok "sudah ada di Secret Manager"
    fi
  else
    [[ -n "$json_file" && -f "$json_file" ]] || die "File service account Firebase JSON tidak ditemukan di config/ atau .env."
    gcloud secrets create "$FIREBASE_SECRET_NAME" --project "$PROJECT_ID" \
      --replication-policy=automatic --data-file="$json_file" >/dev/null
    ok "dibuat dari $json_file"
  fi

  grant_secret_access "$FIREBASE_SECRET_NAME"
}

verify_required_secrets_ready() {
  info "Memverifikasi ketersediaan seluruh secret wajib sebelum build & deploy"
  local s
  for s in "$SECRET_NAME" "$JWT_SECRET_NAME" "$MAPS_SECRET_NAME" "$FIREBASE_SECRET_NAME"; do
    gcloud secrets describe "$s" --project "$PROJECT_ID" >/dev/null 2>&1 \
      || die "Secret wajib '$s' belum tersedia di Secret Manager."
  done
  ok "Seluruh secret wajib ($SECRET_NAME, $JWT_SECRET_NAME, $MAPS_SECRET_NAME, $FIREBASE_SECRET_NAME) siap"
}

build_image() {
  local sha tag
  sha="$(git rev-parse --short HEAD 2>/dev/null || echo nogit)"
  tag="$(date +%Y%m%d-%H%M%S)-${sha}"
  IMAGE="${IMAGE_BASE}:${tag}"
  info "Membangun image dengan Cloud Build"
  echo "    $IMAGE"
  if ! gcloud builds submit . --project "$PROJECT_ID" --tag "$IMAGE" --quiet; then
    warn "Build gagal. Jika pesannya soal izin, jalankan: ./deploy.sh fix-permissions"
    exit 1
  fi
  ok "image selesai dibuat"
}

deploy_service() {
  info "Men-deploy ke Cloud Run"
  gcloud run deploy "$SERVICE" \
    --project "$PROJECT_ID" --region "$REGION" --platform managed \
    --image "$IMAGE" \
    --service-account "$SA_EMAIL" \
    --allow-unauthenticated \
    --port 8080 \
    --cpu "$CPU" --memory "$MEMORY" \
    --min-instances "$MIN_INSTANCES" --max-instances "$MAX_INSTANCES" \
    --timeout "$TIMEOUT" \
    --no-cpu-throttling --cpu-boost \
    --set-secrets "GOOGLE_API_KEY=${SECRET_NAME}:latest,JWT_SECRET_KEY=${JWT_SECRET_NAME}:latest,GOOGLE_MAPS_API_KEY=${MAPS_SECRET_NAME}:latest,/secrets/firebase/key.json=${FIREBASE_SECRET_NAME}:latest" \
    --set-env-vars "GOOGLE_GENAI_USE_VERTEXAI=FALSE,FIREBASE_PATH=/secrets/firebase/key.json,GOOGLE_APPLICATION_CREDENTIALS=/secrets/firebase/key.json,FIREBASE_PROJECT_ID=flutter-agent-doctor" \
    --quiet
  ok "revisi baru aktif"
}

health_check() {
  local url; url="$(service_url)"
  [[ -n "$url" ]] || die "URL layanan tidak ditemukan."
  info "Health check"
  local i
  for i in 1 2 3 4 5; do
    if out="$(curl -fsS --max-time 60 "${url}/" 2>/dev/null)"; then
      ok "$out"
      break
    fi
    echo "    percobaan $i gagal, mencoba lagi..."; sleep 5
  done
  [[ -n "${out:-}" ]] || warn "Health check belum berhasil. Lihat log: ./deploy.sh logs"

  printf '\n\033[1;32mSelesai.\033[0m\n'
  echo "  URL layanan : $url"
  echo "  Dokumentasi : ${url}/docs"
  echo "  Di Flutter  : ubah baseUrl di lib/constants.dart menjadi ${url}"
}

refresh_service_secrets() {
  info "Memulai revisi baru agar secret terbaru terbaca"
  gcloud run services update "$SERVICE" --project "$PROJECT_ID" --region "$REGION" \
    --update-env-vars "SECRET_REFRESHED_AT=$(date +%s)" --quiet >/dev/null
  ok "selesai"
}

fix_permissions() {
  info "Memberi izin build ke service account Compute (dipakai Cloud Build)"
  local num sa
  num="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"
  sa="${num}-compute@developer.gserviceaccount.com"
  gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:${sa}" --role="roles/cloudbuild.builds.builder" \
    --condition=None --quiet >/dev/null
  ok "roles/cloudbuild.builds.builder -> $sa"
  echo "    Tunggu ±1 menit lalu jalankan ./deploy.sh lagi."
}

# --------------------------------- MAIN ------------------------------------
cmd="${1:-deploy}"
case "$cmd" in
  deploy)
    preflight
    enable_apis
    ensure_repo
    ensure_service_account
    ensure_secret 0
    ensure_jwt_secret 0
    ensure_maps_secret 0
    ensure_firebase_secret 0
    verify_required_secrets_ready
    build_image
    deploy_service
    health_check
    ;;
  secret)
    preflight
    ensure_secret 1
    refresh_service_secrets
    ;;
  jwt-secret)
    preflight
    ensure_jwt_secret 1
    refresh_service_secrets
    ;;
  maps-secret)
    preflight
    ensure_maps_secret 1
    refresh_service_secrets
    ;;
  firebase-secret)
    preflight
    ensure_firebase_secret 1
    refresh_service_secrets
    ;;
  url)
    preflight >/dev/null
    service_url
    ;;
  logs)
    preflight >/dev/null
    gcloud run services logs read "$SERVICE" --project "$PROJECT_ID" --region "$REGION" --limit 100
    ;;
  rollback)
    preflight >/dev/null
    if [[ -z "${2:-}" ]]; then
      gcloud run revisions list --service "$SERVICE" --project "$PROJECT_ID" --region "$REGION" --limit 5
      echo; echo "Pilih revisi lalu jalankan: ./deploy.sh rollback NAMA_REVISI"
    else
      gcloud run services update-traffic "$SERVICE" --project "$PROJECT_ID" --region "$REGION" \
        --to-revisions "$2=100" --quiet
      ok "trafik dikembalikan ke $2"
    fi
    ;;
  fix-permissions)
    preflight >/dev/null
    fix_permissions
    ;;
  help|-h|--help)
    sed -n '2,24p' "$0"
    ;;
  *)
    die "Perintah tidak dikenal: $cmd  (coba: ./deploy.sh help)"
    ;;
esac
