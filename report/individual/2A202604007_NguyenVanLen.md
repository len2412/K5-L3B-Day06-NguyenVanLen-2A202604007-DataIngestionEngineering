# Individual Report — Day 06 Data Ingestion Engineering

## Student and submission

- Name: Nguyen Van Len (inferred from project folder; please confirm spelling/diacritics)
- Student ID: 2A202604007 (inferred from project folder)
- Class: L3B (provided by student)
- Corpus: Pro Git, second edition, chapters 1–3; 95 useful printed pages
- Repository: Not provided yet; currently assessed from the local project folder
- Submission commit: Not provided yet

## Data and pipeline

The corpus is the English-language *Pro Git* second edition by Scott Chacon and Ben Straub, version 2.1.450. The manifest identifies the upstream book site and GitHub project, the selected printed pages (10–104 across chapters 1–3), physical PDF page mapping, extraction character counts, and the uploaded PDF SHA-256. The manifest counts 95 useful pages after excluding front matter and chapters outside the selected scope. The source declares CC BY-NC-SA 3.0 Unported. Attribution is included here; redistribution/adaptation must retain attribution and the same license, and commercial use is not allowed. Third-party material in the source was not separately reviewed.

The loader uses pypdf, Unicode NFKC normalization, whitespace cleanup, and manifest-based page mapping. It raises on a missing PDF, page-count mismatch, invalid mapping, or selected page with no extracted text instead of silently dropping it. The latest run extracted all 95 declared pages. The benchmark validator compared each gold quote against normalized text on its claimed printed page and completed successfully for all 20 questions. This validates quote presence/page mapping, not the quality of every expected answer; the benchmark remains small and its answers should be reviewed by the submitter.

Embedding: local `BAAI/bge-large-en-v1.5`, 1,024 dimensions, CPU, Sentence Transformers; document text is embedded without a prefix and queries use the BGE-recommended retrieval instruction. Cost recorded as $0. Store: PostgreSQL 16 with pgvector 0.8.7, cosine distance, exact ordered search. Metadata includes document ID, source, printed page, page ID, physical PDF page, chapter number/title, chunk method/index and character offsets. `chapter_number` is the benchmark filter field and is copied from the manifest. The compose file uses a local-only port binding and the sample password is for local lab use only.

## Experimental design

The benchmark has 20 questions: q01–q15 dev and q16–q20 test, with six chapter filters (three dev and three test). The split is in the benchmark file. It includes direct, paraphrase and conditional questions. The pipeline rejects any quote not found on its gold page and any unsupported filter value. All three saved runs passed this validation.

| Strategy | Chunking and retrieval |
|---|---|
| A | Fixed 500-character windows with 50-character overlap; no filter |
| B | Paragraph-aware chunks, maximum 1,200 characters; no filter |
| C | Reuses B's exact chunks and vectors; applies only question-provided chapter filters |

A–B changes chunking. B–C changes only query-time filtering. The code uses the same corpus, model, benchmark and vector store across strategies. The experiment files do not establish that a selection rule was recorded before viewing the test results, and two runs already contain test metrics. Treat test numbers as descriptive, not as an untouched holdout. A sound next run would freeze a dev-based choice before evaluating a new test set.

## Retrieval results

Latest saved run: `artifacts/run_20261114T054151456688Z/`. Hit@1 and Evidence Hit@3 count a question as a hit only when the retrieved chunk is on the gold source/page and contains the verified evidence quote.

| Strategy | Split | Hit@1 | Evidence Hit@3 | Chunks | Embedding time (s) | Upsert time (s) |
|---|---|---:|---:|---:|---:|---:|
| A | Dev | 8/15 | 8/15 | 375 | 84.37 | 0.35 |
| A | Test | 3/5 | 5/5 | 375 | 84.37 | 0.35 |
| B | Dev | 9/15 | 11/15 | 177 | 77.38 | 0.19 |
| B | Test | 0/5 | 4/5 | 177 | 77.38 | 0.19 |
| C | Dev | 9/15 | 11/15 | 177 | 77.38 (shared with B) | 0.19 |
| C | Test | 0/5 | 5/5 | 177 | 77.38 (shared with B) | 0.19 |

The A–B dev comparison favors B by 3 more Evidence Hit@3 cases while using 198 fewer chunks; test behavior is mixed, with A ahead at Hit@1 and B missing one top-3 evidence case. B–C is identical on dev; on test, C retrieves the gold evidence for one more question, q17, after applying its chapter filter. The sample is too small to claim general superiority. A selection based on dev alone would favor B/C over A for top-3 evidence; C adds useful scope control with no extra document embeddings.

## Manual review, interpreted with AI assistance

The saved review forms are in `manual_test_review.jsonl` and `coherence_review.csv`. They contain a first-pass AI-assisted review, not an independent human audit. The course rubric calls for the submitter to inspect the cited chunks and confirm or revise these labels before final submission. Metadata was compared with the source/page/chapter values carried by the loader and trace. In the 45 test top-3 records (5 questions × 3 strategies × 3 hits), the source and page fields are present and consistent with the ingested manifest: 45/45. C returned chapter 3 for all three filtered test questions and all nine corresponding hits: 9/9 filter-compliant.

Grounding review of the five test questions, using 0 = no usable evidence, 1 = incomplete context, 2 = sufficient evidence and traceable source:

| Strategy | Scores by q16–q20 | Mean /2 | Notes |
|---|---|---:|---|
| A | 2, 2, 2, 2, 2 | 2.0 | Each top-3 includes a sufficient source passage, although q16 evidence is ranked second. |
| B | 2, 0, 2, 2, 2 | 1.6 | q17 is the failure: the top-3 misses the detached-HEAD evidence. |
| C | 2, 2, 2, 2, 2 | 2.0 | Chapter filtering brings the relevant q17 evidence into the top-3. |

Coherence was rated at ten fixed source positions (the midpoint of each of the first ten ordered pages in the selected corpus), using the rubric’s 0–2 scale. AI-assisted first-pass results are A: 12/20 (1.2/2), B: 17/20 (1.7/2), C: 17/20 (1.7/2). Fixed windows often begin mid-word or mid-thought; paragraph-aware chunks more often preserve a complete explanation. Per-sample scores and reasons are in `coherence_review.csv`. The submitter should confirm or revise the labels before submission.

## Errors, limits, and conclusion

Examples of observed retrieval failures: B misses q17 (the expected evidence concerns both ordinary and detached HEAD state); A and B rank a non-gold passage first on q16 even though a gold passage appears in the top-3; B and C rank a non-gold passage first for q20 while retrieving the gold fast-forward explanation second. The test set has only five questions, and it has already been inspected in saved runs; these observations are exploratory. Gold evidence is currently a single quote/page per question, so equivalent evidence elsewhere may be undercounted. PDF text extraction can mishandle figures or layout, and third-party license content has not been separately checked.

Reproduce from `starter/` after installing requirements, starting Docker Compose and confirming the local model is cached: `python -m src.run`. The runner writes a timestamped artifact directory and replaces each strategy's rows for the source document on upsert, avoiding duplicate chunks on rerun. Exact dependency and database image versions are in `requirements.txt` and `docker-compose.yml`; execution time varies by hardware. AI assistance was used to draft and inspect this report; the submitter should verify the labels, code behavior, and all statements and be prepared to explain the work.

## Before submission

1. Confirm the AI-assisted coherence, test grounding and metadata labels; revise any labels that do not match your own inspection.
2. Add the repository URL and final submitted commit, complete the checklist in `SUBMISSION.md`, and confirm that the target repository is accessible to the grader.
