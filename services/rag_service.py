import os
import chromadb
from PyPDF2 import PdfReader
from chromadb.utils import embedding_functions

# Kita gunakan Default Embedding dari Chroma (all-MiniLM-L6-v2)
# Model ini sangat ringan, cepat, dan berjalan 100% secara lokal di komputer Anda.
default_ef = embedding_functions.DefaultEmbeddingFunction()

# Inisialisasi Vector DB (Chroma) secara lokal
chroma_client = chromadb.PersistentClient(path="./data/vector_db")

# Buat atau ambil koleksi jurnal menggunakan model embedding lokal
collection = chroma_client.get_or_create_collection(
    name="medical_journals",
    embedding_function=default_ef
)

def ingest_pdfs_from_directory(directory_path: str = "./data"):
    """Membaca PDF dan menyimpannya ke Vector DB"""
    for filename in os.listdir(directory_path):
        if filename.endswith(".pdf"):
            filepath = os.path.join(directory_path, filename)
            
            try:
                reader = PdfReader(filepath)
                text_chunks = []
                ids = []
                
                # Ekstrak per halaman dan jadikan chunk
                for i, page in enumerate(reader.pages):
                    text = page.extract_text()
                    # Bersihkan teks yang kosong atau terlalu pendek
                    if text and len(text.strip()) > 50: 
                        text_chunks.append(text)
                        ids.append(f"{filename}_page_{i}")
                
                # Masukkan ke ChromaDB
                if text_chunks:
                    collection.add(
                        documents=text_chunks,
                        ids=ids,
                        metadatas=[{"source": filename} for _ in text_chunks]
                    )
                    print(f"Berhasil memproses jurnal: {filename}")
            except Exception as e:
                print(f"Gagal memproses {filename}. Error: {str(e)}")

def search_journal(query: str, n_results: int = 2) -> str:
    """Fungsi ini akan dipanggil oleh AI sebagai Tool untuk mencari info medis"""
    results = collection.query(
        query_texts=[query],
        n_results=n_results
    )
    
    if not results['documents'] or not results['documents'][0]:
        return "Tidak ditemukan referensi medis yang relevan di jurnal lokal."
    
    # Gabungkan hasil pencarian menjadi satu string konteks
    context = "\n\n---\n\n".join(results['documents'][0])
    return f"REFERENSI JURNAL MEDIS:\n{context}"