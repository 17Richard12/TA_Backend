import os
import re
from typing import Dict, List, Optional, Tuple
import chromadb
from PyPDF2 import PdfReader
from chromadb.utils import embedding_functions

# Base directory of TA_Backend so paths work regardless of current working directory
_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DEFAULT_DATA_DIR = os.path.join(_BACKEND_DIR, "data")
_DEFAULT_VECTOR_DB_DIR = os.path.join(_DEFAULT_DATA_DIR, "vector_db")

# Default Embedding dari Chroma (all-MiniLM-L6-v2)
default_ef = embedding_functions.DefaultEmbeddingFunction()

# Inisialisasi Vector DB (Chroma) secara lokal
chroma_client = chromadb.PersistentClient(path=_DEFAULT_VECTOR_DB_DIR)

# Buat atau ambil koleksi jurnal menggunakan model embedding lokal
collection = chroma_client.get_or_create_collection(
    name="medical_journals",
    embedding_function=default_ef,
)

# Regex ekstraksi DOI standar (10.xxxx/...)
_DOI_RE = re.compile(r"\b(10\.\d{4,9}/[-._;()/:A-Za-z0-9]+)\b")
_HTTP_URL_RE = re.compile(r"(https?://[^\s<>\"'\)\]]+)")

# Mapping DOI/URL terverifikasi berdasarkan sitasi jurnal di dalam PDF untuk file yang
# tidak memuat string DOI eksplisit di body teks (atau memiliki font encoding rusak seperti 58.pdf)
KNOWN_SOURCE_CITATIONS: Dict[str, Dict[str, str]] = {
    "23.pdf": {
        "title": "Detection of Chronic Respiratory Bronchiolitis in Oxidant-Exposed Populations: Analogy to Tobacco Smoke Exposure",
        "doi": "10.1289/ehp.856185",
        "url": "https://doi.org/10.1289/ehp.856185",
    },
    "36.pdf": {
        "title": "Long-Term Oxygen Therapy for Patients With Chronic Obstructive Pulmonary Disease (COPD): An Evidence-Based Analysis",
        "url": "https://www.hqontario.ca/en/mas/tech/pdfs/2012/rev_LTOT_March.pdf",
    },
    "37.pdf": {
        "title": "Pulmonary Rehabilitation for Patients With Chronic Pulmonary Disease (COPD): An Evidence-Based Analysis",
        "url": "https://www.hqontario.ca/en/mas/mas_ohtas_mn.html",
    },
    "38.pdf": {
        "title": "Noninvasive Positive Pressure Ventilation for Chronic Respiratory Failure Patients With Stable Chronic Obstructive Pulmonary Disease (COPD)",
        "url": "https://www.hqontario.ca/en/mas/mas_ohtas_mn.html",
    },
    "39.pdf": {
        "title": "Smoking Cessation for Patients With Chronic Obstructive Pulmonary Disease (COPD): An Evidence-Based Analysis",
        "url": "https://www.hqontario.ca/en/mas/mas_ohtas_mn.html",
    },
    "40.pdf": {
        "title": "Cost-Effectiveness of Interventions for Chronic Obstructive Pulmonary Disease (COPD) Using an Ontario Policy Model",
        "url": "https://www.hqontario.ca/en/mas/tech/pdfs/2012/rev_COPD_Economic_March.pdf",
    },
    "53.pdf": {
        "title": "Relationship between angiogenic squamous dysplasia and bronchogenic carcinoma in patients undergoing white light bronchoscopy",
        "doi": "10.1155/2012/423626",
        "url": "https://doi.org/10.1155/2012/423626",
    },
    "55.pdf": {
        "title": "Coexisting Bronchogenic Carcinoma and Pulmonary Tuberculosis in the Same Lobe: Radiologic Findings and Clinical Significance",
        "doi": "10.3348/kjr.2001.2.3.138",
        "url": "https://doi.org/10.3348/kjr.2001.2.3.138",
    },
    "58.pdf": {
        "title": "Effect of tobacco smoking on the risk of developing community acquired pneumonia: A systematic review and meta-analysis",
        "doi": "10.1371/journal.pone.0220204",
        "url": "https://doi.org/10.1371/journal.pone.0220204",
    },
    "59.pdf": {
        "title": "Cigarette Smoking and Invasive Pneumococcal Disease",
        "doi": "10.1056/NEJM200003093421002",
        "url": "https://doi.org/10.1056/NEJM200003093421002",
    },
}


def is_garbled_text(text: str, max_noise_ratio: float = 0.20) -> bool:
    """
    Mendeteksi apakah teks hasil ekstraksi PDF rusak/garbled (misal akibat font encoding map rusak).
    Mengembalikan True jika proporsi karakter simbol aneh/non-standar melebihi batas.
    """
    if not text or not text.strip():
        return True
    cleaned = "".join(ch for ch in text if not ch.isspace())
    if len(cleaned) < 20:
        return True

    allowed_punct = set(".,;:!?()-/[]'\"%+<>=$#&*@")
    weird_count = 0
    alpha_count = 0
    cipher_symbol_count = 0
    for ch in cleaned:
        if ch.isalpha():
            alpha_count += 1
        if ch in "{}~`|^_\\€" or ord(ch) > 127 or ch == "\ufffd":
            cipher_symbol_count += 1
        if ord(ch) > 127 or ch == "\ufffd" or (not ch.isalnum() and ch not in allowed_punct):
            weird_count += 1

    noise_ratio = weird_count / len(cleaned)
    if noise_ratio > max_noise_ratio or (cipher_symbol_count / len(cleaned)) > 0.08:
        return True
    if alpha_count / len(cleaned) < 0.45:
        return True
    return False


def extract_doi_and_url(text: str, filename: str = "", pdf_metadata: Optional[dict] = None) -> Tuple[str, str, str]:
    """
    Mengekstrak (doi, url, title) dari teks PDF, metadata PDF, atau daftar sitasi terverifikasi.
    Tidak pernah membuat URL pencarian PubMed generik sebagai tautan artikel.
    """
    known = KNOWN_SOURCE_CITATIONS.get(filename, {})
    doi = known.get("doi", "")
    url = known.get("url", "")
    title = known.get("title", "")

    if pdf_metadata and not title:
        raw_title = str(pdf_metadata.get("/Title") or "").strip()
        if raw_title and not raw_title.lower().startswith(("microsoft word", "untitled")) and not raw_title.endswith(".indd"):
            title = raw_title

    meta_text = " ".join(str(v) for v in (pdf_metadata or {}).values() if v)
    combined = f"{meta_text}\n{text or ''}"

    if not doi:
        m = _DOI_RE.search(combined)
        if m:
            doi = m.group(1).rstrip(".,;)/")
            url = f"https://doi.org/{doi}"

    if not url:
        u = _HTTP_URL_RE.search(combined)
        if u:
            candidate = u.group(1).rstrip(".,;)/")
            if len(candidate) > 15:
                url = candidate

    return doi, url, title


def ingest_pdfs_from_directory(
    directory_path: Optional[str] = None,
    target_collection=None,
) -> Dict[str, List[str]]:
    """
    Membaca PDF dari directory_path dan menyimpannya ke Vector DB secara aman:
    - Menggunakan `collection.upsert` agar tidak menimbulkan duplikasi chunk dan aman untuk re-ingestion.
    - Menyimpan `source`, `page` (1-indexed), `doi`, `url`, dan `title` pada metadata.
    - Mendeteksi PDF yang tidak valid, tidak terbaca, atau memiliki teks garbled (seperti font rusak).
    """
    dir_path = directory_path or _DEFAULT_DATA_DIR
    col = target_collection if target_collection is not None else collection

    summary: Dict[str, List[str]] = {
        "processed": [],
        "corrupted_or_unreadable": [],
        "skipped_empty": [],
    }

    if not os.path.isdir(dir_path):
        return summary

    for filename in sorted(os.listdir(dir_path)):
        if not filename.lower().endswith(".pdf"):
            continue
        filepath = os.path.join(dir_path, filename)

        try:
            reader = PdfReader(filepath)
            pdf_meta = dict(reader.metadata or {})
            sample_pages_text = "\n".join(
                (p.extract_text() or "") for p in reader.pages[:3]
            )
            if len(reader.pages) > 3:
                sample_pages_text += "\n" + (reader.pages[-1].extract_text() or "")

            doc_doi, doc_url, doc_title = extract_doi_and_url(
                sample_pages_text, filename=filename, pdf_metadata=pdf_meta
            )

            text_chunks: List[str] = []
            ids: List[str] = []
            metadatas: List[dict] = []
            garbled_pages = 0
            non_empty_pages = 0

            for i, page in enumerate(reader.pages):
                page_num = i + 1  # 1-indexed page number
                text = page.extract_text() or ""
                if len(text.strip()) <= 50:
                    continue
                non_empty_pages += 1

                if is_garbled_text(text):
                    garbled_pages += 1
                    continue

                page_doi, page_url, _ = extract_doi_and_url(text, filename=filename, pdf_metadata=pdf_meta)
                final_doi = page_doi or doc_doi
                final_url = page_url or doc_url

                text_chunks.append(text)
                ids.append(f"{filename}_page_{i}")
                metadatas.append({
                    "source": filename,
                    "page": page_num,
                    "doi": final_doi,
                    "url": final_url,
                    "title": doc_title or filename,
                })

            # Jika seluruh halaman di PDF mengalami kerusakan encoding (misal 58.pdf),
            # catat sebagai corrupted/garbled dan gunakan metadata judul/abstrak yang bersih bila tersedia
            if not text_chunks and garbled_pages > 0:
                summary["corrupted_or_unreadable"].append(filename)
                author = str(pdf_meta.get("/Author") or "").strip()
                if doc_title:
                    fallback_text = (
                        f"Title: {doc_title}\n"
                        f"Authors: {author}\n"
                        f"Source Document: {filename}\n"
                        f"DOI: {doc_doi or '-'}\n"
                        f"URL: {doc_url or '-'}\n"
                        "Note: Body text extraction skipped due to corrupted PDF font encoding."
                    )
                    col.upsert(
                        documents=[fallback_text],
                        ids=[f"{filename}_page_0"],
                        metadatas=[{
                            "source": filename,
                            "page": 1,
                            "doi": doc_doi,
                            "url": doc_url,
                            "title": doc_title,
                            "corrupted_font": True,
                        }],
                    )
                print(f"[PERINGATAN] PDF {filename} memiliki encoding teks rusak ({garbled_pages} halaman garbled).")
                continue

            if not text_chunks:
                summary["skipped_empty"].append(filename)
                print(f"[PERINGATAN] PDF {filename} tidak memiliki teks yang dapat dibaca.")
                continue

            col.upsert(
                documents=text_chunks,
                ids=ids,
                metadatas=metadatas,
            )
            summary["processed"].append(filename)
            print(f"Berhasil memproses jurnal: {filename} ({len(text_chunks)} halaman valid)")
        except Exception as e:
            summary["corrupted_or_unreadable"].append(filename)
            print(f"Gagal memproses {filename}. Error: {str(e)}")

    return summary


def enrich_existing_collection_metadata(
    directory_path: Optional[str] = None,
    target_collection=None,
) -> dict:
    """
    Memperbarui metadata pada koleksi ChromaDB yang sudah ada (tanpa menghapus PDF maupun vector DB)
    agar setiap chunk memiliki `page` (1-indexed), `doi`, `url`, dan `title`, serta menandai chunk
    dari PDF yang font-nya rusak.
    """
    dir_path = directory_path or _DEFAULT_DATA_DIR
    col = target_collection if target_collection is not None else collection

    existing = col.get(include=["metadatas", "documents"])
    ids = existing.get("ids") or []
    metas = existing.get("metadatas") or []
    docs = existing.get("documents") or []

    if not ids:
        return {"updated": 0}

    # Pre-scan PDFs for document-level DOI/URL/Title
    pdf_info_cache: Dict[str, Tuple[str, str, str]] = {}
    if os.path.isdir(dir_path):
        for fname in os.listdir(dir_path):
            if not fname.lower().endswith(".pdf"):
                continue
            fpath = os.path.join(dir_path, fname)
            try:
                reader = PdfReader(fpath)
                pmeta = dict(reader.metadata or {})
                sample_text = "\n".join((p.extract_text() or "") for p in reader.pages[:3])
                if len(reader.pages) > 3:
                    sample_text += "\n" + (reader.pages[-1].extract_text() or "")
                pdf_info_cache[fname] = extract_doi_and_url(sample_text, filename=fname, pdf_metadata=pmeta)
            except Exception:
                pdf_info_cache[fname] = extract_doi_and_url("", filename=fname)

    new_metas = []
    for doc_id, meta, doc_text in zip(ids, metas, docs):
        meta_dict = dict(meta or {})
        source = meta_dict.get("source") or doc_id.split("_page_")[0]

        # Extract 1-indexed page number from chunk ID `{filename}_page_{0_based_idx}`
        page_num = meta_dict.get("page")
        if page_num is None and "_page_" in doc_id:
            try:
                zero_idx = int(doc_id.rsplit("_page_", 1)[-1])
                page_num = zero_idx + 1
            except ValueError:
                page_num = 1
        elif page_num is None:
            page_num = 1

        doc_doi, doc_url, doc_title = pdf_info_cache.get(
            source, extract_doi_and_url(doc_text or "", filename=source)
        )
        chunk_doi, chunk_url, _ = extract_doi_and_url(doc_text or "", filename=source)

        final_doi = chunk_doi or doc_doi or meta_dict.get("doi", "")
        final_url = chunk_url or doc_url or meta_dict.get("url", "")
        final_title = doc_title or meta_dict.get("title", "") or source
        garbled = is_garbled_text(doc_text or "")

        updated_meta = {
            "source": source,
            "page": int(page_num),
            "doi": str(final_doi),
            "url": str(final_url),
            "title": str(final_title),
            "is_garbled": bool(garbled),
        }
        new_metas.append(updated_meta)

    # Update metadata in batches
    batch_size = 250
    for start in range(0, len(ids), batch_size):
        end = start + batch_size
        col.update(
            ids=ids[start:end],
            metadatas=new_metas[start:end],
        )

    return {"updated": len(ids)}


def search_journal(query: str, n_results: int = 3, max_distance: float = 1.65) -> str:
    """
    Fungsi ini dipanggil oleh AI sebagai Tool untuk mencari referensi medis dari jurnal lokal.
    - Menangani query kosong dan hasil pencarian kosong/tidak relevan.
    - Menyertakan nama dokumen sumber, nomor halaman (page), dan tautan DOI/artikel asli jika tersedia.
    - Tidak pernah memalsukan URL atau menampilkan URL pencarian PubMed generik sebagai tautan artikel.
    """
    if not query or not str(query).strip():
        return "Tidak ditemukan referensi medis yang relevan di jurnal lokal."

    cleaned_query = str(query).strip()
    fetch_k = max(n_results * 2, 4)

    try:
        results = collection.query(
            query_texts=[cleaned_query],
            n_results=fetch_k,
            include=["documents", "metadatas", "distances"],
        )
    except Exception as e:
        print(f"[RAG] Error querying ChromaDB: {e}")
        return "Tidak ditemukan referensi medis yang relevan di jurnal lokal."

    docs_list = (results.get("documents") or [[]])[0]
    if not docs_list:
        return "Tidak ditemukan referensi medis yang relevan di jurnal lokal."

    metas_list = (results.get("metadatas") or [[]])[0] or [{}] * len(docs_list)
    distances_list = (results.get("distances") or [[]])[0] or [0.0] * len(docs_list)

    formatted_chunks = []
    seen_chunks = set()

    for idx, (doc, meta, dist) in enumerate(zip(docs_list, metas_list, distances_list)):
        if not doc or not str(doc).strip():
            continue
        if dist is not None and float(dist) > max_distance:
            continue

        meta_dict = meta if isinstance(meta, dict) else {}
        if meta_dict.get("is_garbled") is True or is_garbled_text(doc):
            continue

        source = meta_dict.get("source", "Jurnal Medis")
        page = meta_dict.get("page")

        # Jika metadata belum memiliki page/url, ekstrak secara dinamis
        doi = meta_dict.get("doi", "")
        url = meta_dict.get("url", "")
        title = meta_dict.get("title", "")
        if not url and not doi:
            doi, url, extracted_title = extract_doi_and_url(doc, filename=source)
            if not title:
                title = extracted_title

        dedup_key = (source, page, doc[:120])
        if dedup_key in seen_chunks:
            continue
        seen_chunks.add(dedup_key)

        page_info = f" (Halaman {page})" if page else ""
        title_info = f" — {title}" if title and title != source else ""
        if url:
            link_info = f"Tautan Artikel Asli (DOI/URL): {url}"
        elif doi:
            link_info = f"Tautan Artikel Asli (DOI): https://doi.org/{doi}"
        else:
            link_info = "Tautan Artikel Asli: Tautan DOI langsung tidak tercantum pada dokumen lokal ini"

        formatted_chunks.append(
            f"[Sumber Jurnal / Dokumen: {source}{page_info}{title_info} | {link_info}]\n{doc}"
        )

        if len(formatted_chunks) >= n_results:
            break

    if not formatted_chunks:
        return "Tidak ditemukan referensi medis yang relevan di jurnal lokal."

    context = "\n\n---\n\n".join(formatted_chunks)
    return f"REFERENSI JURNAL MEDIS:\n{context}"
