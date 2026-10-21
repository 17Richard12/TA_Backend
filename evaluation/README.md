# Toolkit Evaluasi: ROUGE + LLM-as-Judge

Menguji kualitas output sistem multi-agen (Monitor-Evaluator-Counselor) sesuai metodologi proposal TA.

## Isi
| File | Fungsi |
|---|---|
| `scenarios.json` | 10 skenario klinis (DRAFT) + kolom `reference_answer` untuk jawaban dokter |
| `run_eval.py` | Menjalankan agen, menghitung ROUGE, menilai dengan LLM-as-Judge, membuat ringkasan |
| `make_report.py` | Membuat draf laporan Word (4 tabel + narasi + lampiran) |
| `requirements-eval.txt` | Dependensi tambahan |

## Langkah
Jalankan semua perintah dari **root folder TA_Backend** (karena ChromaDB memakai path `./data/vector_db`).

```bash
# 0. salin folder evaluation/ ke root TA_Backend, lalu:
pip install -r evaluation/requirements-eval.txt

# 1. UJI ALUR TANPA API (data palsu, hanya memastikan kode jalan)
python evaluation/run_eval.py --dry-run --outdir evaluation/results_dryrun

# 2. PILOT (memakai API key di .env, referensi dokter boleh masih kosong)
python evaluation/run_eval.py --repeats 1 --outdir evaluation/results_pilot

# 3. FINAL (setelah semua reference_answer diisi dokter)
python evaluation/run_eval.py --repeats 3

# 4. Laporan Word
python evaluation/make_report.py --results evaluation/results --out Hasil_Evaluasi.docx --appendix
```

`.env` di root backend harus berisi `GOOGLE_API_KEY`. Hasil: `generations.jsonl` (respons mentah + log agen),
`scores.csv`/`scores.jsonl` (skor per run), `summary.json` (rekap). Eksekusi bisa dilanjutkan (resume) bila terputus.

## Cara kerja singkat
- **Skenario** dikirim ke agen yang sama dengan backend (`InMemoryRunner`, `root_agent`). Karena agen selalu mengakhiri
  jawaban dengan pertanyaan, skenario 1 pesan otomatis diikuti pesan lanjutan ("itu semua informasi saya... berikan analisis akhir").
  Yang dinilai adalah respons akhir.
- **ROUGE-1/2/L**: teks diubah ke huruf kecil, simbol markdown/emoji/tanda baca dibuang, tanpa stemming. Dilaporkan precision/recall/F1;
  rekap memakai `--rouge-metric` (default F1). Tentukan satu metrik dan tulis di buku.
- **LLM-as-Judge**: 4 dimensi (keakuratan medis, keamanan, relevansi konteks, konsistensi orkestrasi), skala 1-5, temperature 0,
  model default `gemini-3.5-flash` (ubah dengan `--judge-model`).
- **Konsistensi orkestrasi** juga dicek otomatis dari log pemanggilan agen oleh koordinator (agen yang diharapkan vs yang dipanggil).
- **Rujukan darurat** pada skenario `wajib_rujukan` dicek dengan kata kunci (IGD / rumah sakit / gawat darurat).
- **Ambang** proposal: ROUGE-1 >= 0,50; ROUGE-L >= 0,45; juri >= 4,0 (`--th-r1`, `--th-rl`, `--th-judge`).

## Catatan metodologi
1. `reference_answer` HARUS ditulis dokter spesialis (bukan AI). Skenario di `scenarios.json` adalah draf; minta dokter memvalidasi dan merevisinya.
2. Tanpa referensi, ROUGE dilewati dan skor juri hanya berstatus pilot.
3. Hasil di bawah ambang adalah temuan yang sah. Jangan mengubah ambang diam-diam; bila ingin mengubahnya, diskusikan dengan dosen pembimbing.
4. Juri dan sistem berasal dari keluarga model yang sama (potensi bias menyukai gaya jawaban sendiri). Validasi juri dengan membandingkan skor juri vs rubrik dokter (skala 1-5 pada proposal).
