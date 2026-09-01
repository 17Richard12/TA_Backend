from services.rag_service import ingest_pdfs_from_directory

if __name__ == "__main__":
    print("Memulai proses ekstraksi jurnal PDF...")
    # Sesuaikan path ini dengan letak folder PDF Anda
    # Jika Anda menaruhnya di folder "data", gunakan "./data"
    ingest_pdfs_from_directory(directory_path="./data") 
    print("Proses Ingest Selesai! Database vektor siap digunakan.")