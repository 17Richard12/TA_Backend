from services.rag_service import ingest_pdfs_from_directory, enrich_existing_collection_metadata

if __name__ == "__main__":
    print("Memulai proses ekstraksi dan pembaruan metadata jurnal PDF...")
    summary = ingest_pdfs_from_directory(directory_path="./data")
    print(
        f"Ringkasan ingest: {len(summary['processed'])} diproses, "
        f"{len(summary['corrupted_or_unreadable'])} rusak/tidak terbaca, "
        f"{len(summary['skipped_empty'])} kosong, "
        f"{len(summary.get('obsolete_chunks_removed', []))} chunk usang dihapus."
    )
    print("Proses Ingest Selesai! Database vektor siap digunakan.")
