# ARC Prompt Engineering Part 1

This is a runnable protocol package, not evidence that new LLM trials were completed.
`SIMULATED_calls.jsonl` is a software fixture. Never cite it as model output.
The previous pilot's 59 real calls do not satisfy this checkpoint's new-model execution requirement.

## What changed in v2.0 (2026-10-28) — read this first

- **Bigger, harder candidate pool (113 cases):** the original 45 GSM8K/MATH cases, plus **40 generated Simpson's paradox cases** (`generate_simpson.py`, seeded worked priors, three question types: pooled / standardized / counterfactual, 2–4 categories), plus **28 AIME 2025 problems** (natural arm only; II-5 and II-6 dropped because they need the diagram). The v1 pool is kept as `cases_candidates_v1_original.json`.
- **Natural-error arm:** calibration asks each model every question 5 times cold. `select` saves one wrong and one right cold answer per eligible model as `natural_wrong` / `natural_correct` priors, so the study also tests self-correction of the model's *own* mistakes. `targeted_fix` is skipped on natural priors because there is no known error location to hint at. The seeded arm is unchanged.
- **Per-model calibration:** a case is eligible for a model when *that model* scores 30–70% cold (`--band` to change, record it in `deviations.csv`). Cases only run for models they are eligible for. `select` prints each model's mean cold accuracy per source, which is the ceiling-effect check.
- **Resume:** if a run crashes or you stop it, run the exact same command with the same output folder and it continues where it stopped. Changing code, config or cases makes it refuse, so runs are never mixed.
- **Speed:** 4 requests in parallel by default (`--workers`). Lower it if ARC returns many 429s.
- **max_tokens per model** in the config (8000 for both ARC models, which is ARC's non-streaming cap; 4096 Qwen) so reasoning models don't run out before writing FINAL.
- **Simpson's scoring tolerance:** Simpson's answers count as correct within ±0.05 percentage points (rounding intermediate rates is not a reasoning error). `select` and `analyze` rescore every saved response with the current rules, so scoring changes never need a re-run.
- `models_arc_only.json` = `models.json` without Qwen, for running before the Qwen GPU server exists.
- Calibration on 113 cases × 2 ARC models × 5 = 1,130 calls. Full-study size depends on how many cases calibration keeps.

## Files and remaining gates

- `cases_candidates.json`: 45 sourced candidate questions, checked numeric reference extraction and original benchmark solutions. All 45 have newly prepared worked correct/wrong priors (11 early, 21 middle and 13 final-step errors); independent team review is pending. Candidate tiers are provisional and require zero-shot calibration.
- `models.json`: three distinct families; two documented ARC endpoints and one 7B Qwen server. No new model has been live-confirmed here.
- `arc_study.py`: preflight, calibration, case selection, balanced-prior smoke/full run, scoring, raw logging, audit export and analysis.
- `deviations.csv`: record dated changes after submission. Freeze hashes only after pending steps are resolved or explicitly documented as blocked.

## Local setup (Python 3.11+)

Eligible team members run ARC requests using their own keys. Do not put a key in any file, report, screenshot, shared command or Git repository. On macOS/Linux:

```bash
read -s -p "Personal ARC key: " ARC_API_KEY; export ARC_API_KEY; echo
```

Configure and deploy Qwen on an authorized ARC GPU using the official ARC vLLM/Open OnDemand instructions. Pin the actual model revision and server version in `models.json`. If using vLLM, a typical server command is below; confirm it fits your allocated GPU and installed vLLM version.

```bash
vllm serve Qwen/Qwen2.5-7B-Instruct --revision <verified-commit> --host 127.0.0.1 --port 8000 --api-key "$QWEN_API_KEY"
```

Use an SSH tunnel from the execution computer to the assigned GPU node, then set `QWEN_API_KEY` locally. The placeholder revision is deliberately not a runnable default. No GPU allocation or serving process is started by this package.

## Run in order

From this directory:

```bash
python3 test_pipeline.py
python3 arc_study.py run --phase preflight --out runs/preflight
python3 arc_study.py run --phase calibrate --out runs/calibration
python3 arc_study.py select --log runs/calibration/calls.jsonl --out cases_selected.json
python3 arc_study.py run --phase smoke --out runs/checkpoint
python3 arc_study.py audit --log runs/checkpoint/calls.jsonl --out audit_50.csv
python3 arc_study.py analyze --log runs/checkpoint/calls.jsonl --out checkpoint_analysis.json
```

The smoke run uses the three prepared cases, all models, both prior conditions, all five strategies and **two corrective calls per chain** (180 successful calls). Round two is forced for this checkpoint to demonstrate history preservation; full-study chains stop at verified success. Preflight adds three calls. Five independent calibration runs on 45 candidates and three models add 675 calls. Expansion of the candidate pool may be needed; do not silently relax the 30–70% rule if the shared intersection is too small.

The smoke run can be made before calibration for infrastructure testing; it does not prove the selected cases meet the difficulty criterion. Review the worked errors for every final case; set `seed_ready`, `team_reviewed`, and `calibration_selected` only with evidence. Select 40–50 cases with declared tier/type quotas. Use only scalar numeric answers under this parser; symbolic/set answers need an extension, audit and deviation entry.

For the full study:

```bash
python3 arc_study.py run --phase study --cases cases_selected.json --out runs/full
```

A 45-case design has 4,050 first-round calls and at most 8,100 corrective calls when wrong-prior chains continue to three rounds and correct-prior chains end after one. Calibration, preflight, checkpoint and technical retries are additional. Actual usage is logged; the study is exploratory, not powered by this count alone.

## Scoring and human work

Numeric equality uses exact rational equivalence, with absolute tolerance 0.00005 only for decimal responses, except tasks explicitly requesting another rounding precision; those use the saved reference_decimal_places. A percentage in a percentage-point task is interpreted as the reported percentage; elsewhere it is divided by 100. Missing/unsupported extraction is unscorable, never silently labeled numerically wrong. Report end-to-end success and confirmed-wrong/unscorable rates separately. Bold FINAL, spaces, fractions and percentages are supported; approximation text, sets and expressions require manual adjudication. Raw logs are immutable; corrections go in the audit CSV.

Audit a seeded random sample of 50 **live** responses. Two raters independently score the same first 30: explanation = correct/minor/major/unavailable; error diagnosis = accurate/partial/incorrect/unavailable. Correct-prior trials also score whether a nonexistent error was alleged. Compute Cohen's kappa before reconciliation. Run `python3 arc_study.py kappa --audit audit_50.csv --out kappa.json`. After rubric clarification, split remaining responses and retain a 20% overlap sample. Human audit and kappa are not completed in this package.

The analysis command implements case-cluster bootstrap CIs and exact McNemar tests using predeclared repeat 1, with Holm adjustment per model/prior family. Repeats 2–3 inform case-averaged rates; they are not independent McNemar pairs. RQ2's strategy-by-model-by-type comparisons remain exploratory; sparse cells prohibit a stable saturated regression. Inspect calibration and missingness before analysis. RQ3 must use the original wrong-prior denominator and carried-forward successes; report incremental total tokens per additional corrected chain (undefined if no gain), plus latency and call counts. Hidden-token components remain null when the provider does not expose them. Never infer private reasoning from visible text or double-count reasoning tokens already contained in completion tokens.

## Submission

Upload the PDF and link your actual team repository/shared folder containing this code, candidates/final cases, configuration, live logs, audit scores and deviations. A downloadable ZIP in ChatGPT is not a shared-repository link. Team contribution assignments in the plan are proposed; replace them with actual completed work. Include the AI-use statement. Do not claim calibration, model preflight, a 50-response human audit, or live repair rounds occurred until the corresponding evidence exists.
