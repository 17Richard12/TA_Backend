FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Kompiler dibutuhkan bila ada paket tanpa wheel (mis. hnswlib milik ChromaDB)
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential \
 && rm -rf /var/lib/apt/lists/*

# Salin daftar dependensi lebih dulu supaya layer ini ter-cache
# selama Requirements.txt / constraints.txt tidak berubah.
COPY Requirements.txt constraints.txt ./

# sentence-transformers tidak diimpor di kode (menarik PyTorch berukuran GB),
# jadi dilewati agar build dan cold start jauh lebih cepat.
RUN sed '/^sentence-transformers/Id' Requirements.txt > requirements.deploy.txt \
 && pip install -r requirements.deploy.txt -c constraints.txt

# Unduh model embedding bawaan ChromaDB (all-MiniLM-L6-v2, ONNX) saat build,
# bukan saat instance pertama kali menerima request.
RUN python -c "from chromadb.utils import embedding_functions as ef; ef.DefaultEmbeddingFunction()(['warmup'])"

# Kode aplikasi (termasuk data/vector_db yang sudah ada di repo)
COPY . .

EXPOSE 8080

# Cloud Run mengirim nomor port lewat variabel $PORT
CMD exec uvicorn main:app --host 0.0.0.0 --port ${PORT:-8080}
