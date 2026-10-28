# Human rating rubric v1.0

Use only the returned visible explanation. Do not rate private reasoning or assume that a short answer had no internal reasoning.

## Explanation

- `correct`: material reasoning, assumptions and calculations are valid.
- `minor`: wording or an isolated detail is imprecise, but the mathematical justification remains valid.
- `major`: a stated assumption, concept or calculation materially undermines the solution, even if FINAL is correct.
- `unavailable`: answer-only output or insufficient assessable explanation.

Examples from the original pilot: describing without-replacement draws as independent is a major conceptual error. Calling sensitivity 0.8 the Bayes numerator is an incorrect diagnosis. A final-only answer is unavailable, with numerical correctness scored separately.

## Diagnosis

- `accurate`: identifies the actual seeded faulty step and its nature.
- `partial`: identifies the right region or issue without a precise correct diagnosis.
- `incorrect`: identifies a different or nonexistent error, or incorrectly explains the mistake.
- `unavailable`: no diagnosis is given.

On correct-prior trials, mark `invented_error_on_correct_prior` yes/no/unavailable. A correct statement that no error occurred is accurate; a repair that introduces a false fault is incorrect. For wrong-prior trials this additional flag is not applicable.

## Other fields and procedure

`visible_verification` is yes/no/unavailable: yes requires a stated distinct check that can be assessed, not the phrase 'verified' alone. Record the independently found final answer, correctness, reviewer IDs and notes. Do not overwrite raw JSON or original parser fields.

Select 50 live outputs at random using seed 20261011 for the parser audit. The same first 30 are rated independently by both raters before discussion. Calculate unweighted Cohen's kappa using `arc_study.py kappa`; `unavailable` remains a category. Report undefined kappa if both raters use a single identical category. Reconcile afterwards, keep both original scores, and log any rubric change. Split remaining scoring with 20% overlapping ratings. An exported blank CSV is a template, not a completed audit.
