# tools.py
from services.rag_service import search_journal

def search_medical_journal(query: str) -> str:
    """
    Gunakan tool ini WAJIB untuk mencari literatur medis, jurnal, asma, efek rokok dari database lokal.
    """
    return search_journal(query)