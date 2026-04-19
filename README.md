# PARANOIA: Privacy-Aware Representation Analyzer that NOtifies Identical ASTs

## Intent

PARANOIA enables trustless code similarity checking.

Its main intent is to prevent accidental code similarity in coding assignments while preserving submission privacy.

Instead of sharing source files, each participant runs a local extractor that converts their code into a structural representation (AST-derived fingerprint). Only this fingerprint is shared. Similarity checks are then performed on fingerprints, not on raw code.

This makes PARANOIA useful for batches that want to detect accidental overlap or suspicious similarity while not sharing any actual code.

## Core Idea

- Keep code private on the author's machine.
- Share structural fingerprints instead of raw source text.
- Detect similarity from syntax structure, not from formatting or variable names.

PARANOIA is built on the assumption that AST-level data is significantly safer to exchange than full source code. However, the current format is structural metadata, not a cryptographic one-way digest.

## How It Works

1. Each user runs PARANOIA locally on their project directory.
2. PARANOIA recursively scans supported source files.
3. It generates a single fingerprint file with no raw source code.
4. Fingerprints are collected and compared locally by a trusted person (me for now, and hopefully forever)

## Supported File Types

- `.cpp`
- `.h`
- `.hpp`
- `.c`
- `.cc`
- `.py`

## Usage

Save extract.py at root of project and run:

# macOS/Linux
```bash
python3 -m venv venv
source venv/bin/activate 
pip install -U pip tree-sitter tree-sitter-cpp tree-sitter-python tree-sitter-ocaml
python3 extract.py .
```
# Windows (PowerShell)
```powershell
python -m venv venv; .\venv\Scripts\Activate.ps1;
pip install -U pip tree-sitter tree-sitter-cpp tree-sitter-python tree-sitter-ocaml;
python extract.py .
```
## Output

PARANOIA generates one file in the current directory:

`paranoia_fingerprint.json`

This file is designed to be safe to share because it contains no raw source text. 

## Comparison And Classification

`compare.py` reads only fingerprint JSON files and ranks pairwise similarity across submissions. It does not need source code.

The main score is the Jaccard similarity of 5-grams built from the flattened AST token stream. The score is reported as a percentage.

Classification uses fixed calibrated thresholds loaded from a calibration file, then applies extra structural signals to reduce false positives.

Final pair category is computed as:

1. `highly_suspicious` if `similarity >= high_suspicion_min` AND (`reciprocal_top1` is true OR `dominance_gap >= high_dominance_gap_min`)
2. `slightly_suspicious` if not high, but `similarity >= slight_suspicion_min`
3. `no_suspicion` otherwise

### Classification Bands

- `no_suspicion`: similarity below the suspicious floor.
- `slightly_suspicious`: similarity at or above the suspicious floor, but not strong enough to be a likely plagiarism match on its own.
- `highly_suspicious`: similarity above the high-risk threshold and supported by a structural signal such as reciprocal top-match behavior.

The current script uses fixed thresholds from `threshold_calibration.json` so behavior stays stable across runs, including small runs with only a few fingerprints. Cohort stats are still printed for context, but they do not change thresholds.

### Metrics Used For Classification

- `pairwise Jaccard similarity (%)` over 5-grams of flattened AST tokens.
- `reciprocal top-1` check, which marks pairs that are each other’s strongest match.
- `dominance gap`, which measures how much stronger a pair is than each submission’s second-best match.

`dominance_gap` is computed per pair as:

- `min(score - second_best_for_a, score - second_best_for_b)`

This makes a pair “dominant” only if it stands out for both sides.

For reporting context only (not thresholding):

- `median off-diagonal similarity` across the cohort.
- `MAD` (median absolute deviation) of the off-diagonal similarity values.
- `q90`, `q97`, and `q99` percentiles of the off-diagonal similarity values.

### Current Threshold Logic

The default calibration file in this repository is `threshold_calibration.json`, seeded from tuned COL106 outputs in `Z/COL216-A2/COL106`:

- `slightly_suspicious` is `>= 31.38%` (minimum score in `slightly_suspicious_pairs.csv`).
- `highly_suspicious` starts at `>= 36.47%` (minimum score in `highly_suspicious_pairs.csv`).
- For non-reciprocal pairs, a pair can still become `highly_suspicious` if `dominance_gap >= 2.25` (set just above the max slight-pair dominance gap `2.24`).

These thresholds are fixed and do not shift between runs. For large batches, best practice is still to review the generated `suspicion_report.txt` and `suspicion_pairs.csv` rather than rely only on category labels.

### Heatmap Coloring

`similarity_heatmap.png` is category-colored using the same final classification logic above (not raw similarity gradient).

- diagonal cells: neutral gray (`self`)
- `no_suspicion`: green
- `slightly_suspicious`: amber
- `highly_suspicious`: red

Off-diagonal cell text includes:

- similarity percent
- `g=` dominance gap
- `R` marker when reciprocal top-1 is true

So the heatmap color now reflects both similarity and dominance/reciprocity conditions exactly like the CSV/report categories.

If you want to use a different tuned cohort, provide a custom calibration file:

```bash
python compare.py --calibration path/to/threshold_calibration.json
python compare.py fileA.json fileB.json --calibration path/to/threshold_calibration.json
```


## Next Steps
We will try this for COL216 - Assignment 2 first. From this, we will get a nice idea of how well it works, and use that to help us in the upcoming assignments.

Fill up https://docs.google.com/forms/d/e/1FAIpQLSfCSlBWkaphrwuDcAvVydmKhlurRJzoWs9RtlPopamkdCI2vQ/viewform
I will perform similarity checks on the collected fingerprints and share results with participants. 
Your responses will be of great help in improving the tool!

## Contribute

Contact me if you want to contribute/become collaborator or have suggestions for improvement! 

## Can someone reverse engineer your code from the fingerprint?

- Realistically, no.
- Reconstructing the exact original source is very difficult.
- The fingerprint stores structural syntax patterns, not raw code text.
- Details like comments, formatting, punctuation tokens, and original identifiers are not preserved in this representation.
- Any attempted reconstruction would usually be incomplete/approximate and highly
obsolete/obfuscated and would not be a faithful reproduction of the original source.
- Want to make this more secure? Collaborate!
- Fingerprints are collected by a trusted reviewer for comparison.
- Fingerprints are not intended to be broadly public by default.
