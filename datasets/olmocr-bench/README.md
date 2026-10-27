---
license: odc-by
tags:
- text
configs:
- config_name: olmocr-bench
  data_files:
  - split: arxiv_math
    path:
    - bench_data/arxiv_math.jsonl
  - split: headers_footers
    path:
    - bench_data/headers_footers.jsonl
  - split: long_tiny_text
    path:
    - bench_data/long_tiny_text.jsonl
  - split: multi_column
    path:
    - bench_data/multi_column.jsonl
  - split: old_scans
    path:
    - bench_data/old_scans.jsonl
  - split: old_scans_math
    path:
    - bench_data/old_scans_math.jsonl
  - split: table_tests
    path:
    - bench_data/table_tests.jsonl
language:
- en
pretty_name: olmOCR-bench
size_categories:
- 1K<n<10K
---

# olmOCR-bench

olmOCR-bench is a dataset of 1,403 PDF files, plus 7,010 unit test cases that capture properties of the output that a good OCR system should have. 
This benchmark evaluates the ability of OCR systems to accurately convert PDF documents to markdown format while preserving critical textual and structural information.

Quick links:
- 📃 [Paper](https://huggingface.co/papers/2502.18443)
- 🛠️ [Code](https://github.com/allenai/olmocr)
- 🎮 [Demo](https://olmocr.allenai.org/)

## Table 1. Distribution of Test Classes by Document Source

| Document Source | Text Present | Text Absent | Reading Order | Table | Math | Total |
|----------------|--------------|-------------|---------------|-------|------|-------|
| arXiv Math     | -            | -           | -             | -     | 2,927| 2,927 |
| Headers Footers| -            | 753         | -             | -     | -    | 753   |
| Long Tiny Text | 442          | -           | -             | -     | -    | 442   |
| Multi Column   | -            | -           | 884           | -     | -    | 884   |
| Old Scans      | 279          | 70          | 177           | -     | -    | 526   |
| Old Scans Math | -            | -           | -             | -     | 458  | 458   |
| Table Tests    | -            | -           | -             | 1,020 | -    | 1,020 |
| **Total**      | 721          | 823         | 1,061         | 1,020 | 3,385| 7,010 |


## Table 2. Document source category breakdown

| **Category** | **PDFs** | **Tests** | **Source** | **Extraction Method** |
|--------------|----------|-----------|------------|------------------------|
| arXiv_math | 522 | 2,927 | arXiv | Dynamic programming alignment |
| old_scans_math | 36 | 458 | Internet Archive | Script-generated + manual rules |
| tables_tests | 188 | 1,020 | Internal repository | `gemini-flash-2.0` |
| old_scans | 98 | 526 | Library of Congress | Manual rules |
| headers_footers | 266 | 753 | Internal repository | DocLayout-YOLO + `gemini-flash-2.0` |
| multi_column | 231 | 884 | Internal repository | `claude-sonnet-3.7` + HTML rendering |
| long_tiny_text | 62 | 442 | Internet Archive | `gemini-flash-2.0` |
| **Total** | 1,403 | 7,010 | Multiple sources | |


## Evaluation Criteria
- Text Presence: Checks if a short text segment (1–3 sentences) is correctly identified in the OCR output. Supports fuzzy matching and positional constraints (e.g., must appear in the first/last N characters). Case-sensitive by default.
- Text Absence: Ensures specified text (e.g., headers, footers, page numbers) is excluded. Supports fuzzy matching and positional constraints. Not case-sensitive.
- Natural Reading Order: Verifies the relative order of two text spans (e.g., headline before paragraph). Soft matching enabled; case-sensitive by default.
- Table Accuracy: Confirms that specific cell values exist in tables with correct neighboring relationships (e.g., value above/below another). Supports Markdown and HTML, though complex structures require HTML.
- Math Formula Accuracy: Detects the presence of a target equation by matching symbol layout (e.g., $\int$ to the left of $x$). Based on rendered bounding boxes and relative positioning.


### 📊 Benchmark Results by Document Source

| **Model**                  | ArXiv | Base | Hdr/Ftr | TinyTxt | MultCol | OldScan | OldMath | Tables | Overall     |
|---------------------------|:-----:|:----:|:-------:|:-------:|:-------:|:-------:|:-------:|:------:|:-----------:|
| GOT OCR                   | 52.7  | 94.0 | 93.6    | 29.9    | 42.0    | 22.1    | 52.0    | 0.2    | 48.3 ± 1.1  |
| Marker v1.6.2             | 24.3  | **99.5** | 87.1    | 76.9    | 71.0    | 24.3    | 22.1    | 69.8   | 59.4 ± 1.1  |
| MinerU v1.3.10            | 75.4  | 96.6 | **96.6**| 39.1    | 59.0    | 17.3    | 47.4    | 60.9   | 61.5 ± 1.1  |
| Mistral OCR API           | **77.2** | 99.4 | 93.6    | 77.1    | 71.3    | 29.3    | 67.5    | 60.6   | 72.0 ± 1.1  |
| GPT-4o (Anchored)         | 53.5  | 96.8 | 93.8    | 60.6    | 69.3    | 40.7    | 74.5    | 70.0   | 69.9 ± 1.1  |
| GPT-4o (No Anchor)        | 51.5  | 96.7 | 94.2    | 54.1    | 68.9    | 40.9    | **75.5**| 69.1   | 68.9 ± 1.1  |
| Gemini Flash 2 (Anchored) | 54.5  | 95.6 | 64.7    | 71.5    | 61.5    | 34.2    | 56.1    | **72.1**| 63.8 ± 1.2  |
| Gemini Flash 2 (No Anchor)| 32.1  | 94.0 | 48.0    | **84.4**| 58.7    | 27.8    | 56.3    | 61.4   | 57.8 ± 1.1  |
| Qwen 2 VL (No Anchor)     | 19.7  | 55.5 | 88.9    | 6.8     | 8.3     | 17.1    | 31.7    | 24.2   | 31.5 ± 0.9  |
| Qwen 2.5 VL (No Anchor)   | 63.1  | 98.3 | 73.6    | 49.1    | 68.3    | 38.6    | 65.7    | 67.3   | 65.5 ± 1.2  |
| **Ours (No Anchor)**      | 72.1  | 98.1 | 91.6    | 80.5    | 78.5    | 43.7    | 74.7    | 71.5   | 76.3 ± 1.1  |
| **Ours (Anchored)**       | 75.6  | 99.0 | 93.4    | 81.7    | **79.4**| **44.5**| 75.1    | 70.2   | **77.4 ± 1.0** |


### License
This dataset is licensed under ODC-BY-1.0. It is intended for research and educational use in accordance with AI2's [Responsible Use Guidelines](https://allenai.org/responsible-use).