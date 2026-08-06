# SCP V104.74 — Benchmark Tool (tách biệt khỏi SCP data)

## Summary
- Tạo benchmark tool tách biệt hoàn toàn khỏi SCP internals
- Input: CSV questions (MMLU + TruthfulQA sample)
- Output: CSV/JSON/LOG riêng (không ghi vào v13.db)
- Tạo câu hỏi biến thể (3-5 paraphrases) bằng rule + LLM

## New Files
- benchmark/generate_variants.py (270 lines) — tạo paraphrases
- benchmark/run_benchmark.py (250 lines) — chạy SCP + thu results
- benchmark/question_sets/mmlu_sample.csv (50 questions)
- benchmark/question_sets/truthfulqa_sample.csv (25 questions)
- benchmark/README.md — hướng dẫn đầy đủ

## Tách biệt
- benchmark/ không import SCP internals
- Chỉ gọi scp_simple.Verifier (1 entry point)
- Output ra results/*.csv (KHÔNG vào v13.db)
- Input từ question_sets/*.csv (định dạng đơn giản)

## Lệnh chạy
```bash
cd benchmark
# Tạo biến thể
python generate_variants.py -i question_sets/mmlu_sample.csv -o question_sets/mmlu_with_variants.csv -n 5
# Chạy benchmark
python run_benchmark.py -i question_sets/mmlu_with_variants.csv
# Kết quả ở: results/benchmark_YYYYMMDD_HHMMSS.csv
```

## Verification
- 6 static tools: 0 errors
- 363 tests pass
- Benchmark tool test: 5 questions → CSV + JSON + LOG output OK
