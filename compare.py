import json
import argparse
from pathlib import Path
import sys
import statistics

import matplotlib.pyplot as plt
from matplotlib import colors

N_GRAM_SIZE = 5
DEFAULT_CALIBRATION_FILENAME = "threshold_calibration.json"
DEFAULT_HIGH_DOMINANCE_GAP_THRESHOLD = 0.0


def flatten_ast(node, token_list):
    if not node:
        return
    token_list.append(node.get("type"))
    for child in node.get("children", []):
        flatten_ast(child, token_list)


def load_fingerprint(filepath):
    with open(filepath, "r") as f:
        data = json.load(f)
        project_tokens = []
        if data.get("tool") == "PARANOIA_NESTED":
            for file_entry in data.get("files", []):
                ast_tree = file_entry.get("ast")
                flatten_ast(ast_tree, project_tokens)
            return project_tokens
        if "structure" in data:
            return data.get("structure", [])
        raise ValueError(f"{filepath} is not a valid PARANOIA fingerprint")


def get_ngrams(tokens, n=N_GRAM_SIZE):
    ngrams = set()
    if len(tokens) < n:
        return {tuple(tokens)}
    for i in range(len(tokens) - n + 1):
        chunk = tuple(tokens[i:i+n])
        ngrams.add(chunk)
    return ngrams


def calculate_jaccard_similarity(set_a, set_b):
    if not set_a and not set_b:
        return 1.0
    intersection = len(set_a.intersection(set_b))
    union = len(set_a.union(set_b))
    if union == 0:
        return 0.0
    return intersection / union


def discover_root_jsons(root_dir):
    return sorted(
        path
        for path in root_dir.glob("*.json")
        if path.is_file() and path.name != DEFAULT_CALIBRATION_FILENAME
    )


def load_ngrams_for_files(filepaths):
    ngrams_by_file = {}
    for path in filepaths:
        try:
            tokens = load_fingerprint(path)
            if tokens:
                ngrams_by_file[path.name] = get_ngrams(tokens)
            else:
                print(f"Skipping {path.name}: no structural tokens.")
        except Exception as e:
            print(f"Skipping {path.name}: {e}")
    return ngrams_by_file


def clean_label(filename):
    name = filename
    for prefix in ("paranoia_fingerprint - ", "paranoia_fingerprint- ", "paranoia_fingerprint "):
        if name.startswith(prefix):
            name = name[len(prefix):]
            break
    if name.endswith(".json"):
        name = name[:-5]
    return name.strip()


def build_similarity_matrix(ngrams_by_file):
    filenames = sorted(ngrams_by_file.keys())
    matrix = []
    for file_a in filenames:
        row = []
        for file_b in filenames:
            score = calculate_jaccard_similarity(ngrams_by_file[file_a], ngrams_by_file[file_b])
            row.append(score * 100)
        matrix.append(row)
    return filenames, matrix


def median_absolute_deviation(values):
    if not values:
        return 0.0
    med = statistics.median(values)
    abs_deviations = [abs(v - med) for v in values]
    return statistics.median(abs_deviations)


def load_calibration(calibration_path=None):
    if calibration_path:
        path = Path(calibration_path)
    else:
        path = Path(__file__).resolve().parent / DEFAULT_CALIBRATION_FILENAME

    if not path.exists():
        raise FileNotFoundError(
            f"Calibration file not found: {path}. "
            "Create it from tuned artifacts (for example COL106 outputs) and pass --calibration if needed."
        )

    with open(path, "r") as f:
        data = json.load(f)

    required_keys = ["slight_suspicion_min", "high_suspicion_min"]
    missing_keys = [k for k in required_keys if k not in data]
    if missing_keys:
        raise ValueError(f"Calibration file is missing required keys: {', '.join(missing_keys)}")

    slight = float(data["slight_suspicion_min"])
    high = float(data["high_suspicion_min"])
    dominance = float(data.get("high_dominance_gap_min", DEFAULT_HIGH_DOMINANCE_GAP_THRESHOLD))

    if not (0.0 <= slight <= 100.0 and 0.0 <= high <= 100.0):
        raise ValueError("Calibration thresholds must be between 0 and 100.")
    if high < slight:
        raise ValueError("Calibration invalid: high_suspicion_min must be >= slight_suspicion_min.")

    return {
        "slight_suspicion_min": slight,
        "high_suspicion_min": high,
        "high_dominance_gap_min": dominance,
        "source": str(path),
        "note": data.get("note", ""),
    }


def compute_thresholds(off_diagonal_scores, calibration):
    summary = {
        "median": 0.0,
        "mad": 0.0,
        "q90": 0.0,
        "q97": 0.0,
        "q99": 0.0,
    }

    if off_diagonal_scores:
        scores = sorted(off_diagonal_scores)
        n = len(scores)
        summary["median"] = statistics.median(scores)
        summary["mad"] = median_absolute_deviation(scores)

        def quantile(p):
            if n == 1:
                return scores[0]
            idx = int(round((n - 1) * p))
            return scores[max(0, min(n - 1, idx))]

        summary["q90"] = quantile(0.90)
        summary["q97"] = quantile(0.97)
        summary["q99"] = quantile(0.99)

    return {
        "no_suspicion_max": calibration["slight_suspicion_min"],
        "slight_suspicion_min": calibration["slight_suspicion_min"],
        "high_suspicion_min": calibration["high_suspicion_min"],
        "high_dominance_gap_min": calibration["high_dominance_gap_min"],
        "calibration_source": calibration["source"],
        "calibration_note": calibration["note"],
        "policy": "fixed_calibrated_from_file",
        **summary,
    }


def build_pair_records(filenames, matrix):
    # Top-2 neighbors for each submission let us measure whether a pair stands out
    # from each student's typical similarities.
    top_neighbors = {}
    for i, name in enumerate(filenames):
        candidates = []
        for j, other in enumerate(filenames):
            if i == j:
                continue
            candidates.append((matrix[i][j], other))
        candidates.sort(reverse=True)
        top_neighbors[name] = candidates[:2]

    pairs = []
    off_diag_scores = []
    for i in range(len(filenames)):
        for j in range(i + 1, len(filenames)):
            a = filenames[i]
            b = filenames[j]
            score = matrix[i][j]
            off_diag_scores.append(score)

            a_top = top_neighbors[a][0][1] if top_neighbors[a] else None
            b_top = top_neighbors[b][0][1] if top_neighbors[b] else None
            reciprocal_top1 = a_top == b and b_top == a

            a_second = top_neighbors[a][1][0] if len(top_neighbors[a]) > 1 else 0.0
            b_second = top_neighbors[b][1][0] if len(top_neighbors[b]) > 1 else 0.0
            dominance_gap = min(score - a_second, score - b_second)

            pairs.append({
                "a": clean_label(a),
                "b": clean_label(b),
                "score": score,
                "reciprocal_top1": reciprocal_top1,
                "dominance_gap": dominance_gap,
            })
    return pairs, off_diag_scores


def categorize_pairs(pairs, thresholds):
    slight_min = thresholds["slight_suspicion_min"]
    high_min = thresholds["high_suspicion_min"]

    for rec in pairs:
        score = rec["score"]
        reciprocal = rec["reciprocal_top1"]
        dominance = rec["dominance_gap"]

        if score >= high_min and (reciprocal or dominance >= thresholds["high_dominance_gap_min"]):
            rec["category"] = "highly_suspicious"
        elif score >= slight_min:
            rec["category"] = "slightly_suspicious"
        else:
            rec["category"] = "no_suspicion"
    return pairs


def categorize_single_score(score, thresholds):
    if score >= thresholds["high_suspicion_min"]:
        return "highly_suspicious"
    if score >= thresholds["slight_suspicion_min"]:
        return "slightly_suspicious"
    return "no_suspicion"


def save_suspicion_report(pairs, thresholds, output_path="suspicion_report.txt"):
    ordered = sorted(pairs, key=lambda x: x["score"], reverse=True)
    counts = {"highly_suspicious": 0, "slightly_suspicious": 0, "no_suspicion": 0}
    for rec in ordered:
        counts[rec["category"]] += 1

    with open(output_path, "w") as f:
        f.write("PARANOIA Pairwise Suspicion Report\n")
        f.write("================================\n\n")
        f.write("Threshold policy: fixed calibrated cutoffs loaded from file (not re-fit per run).\n")
        f.write(f"Calibration source: {thresholds['calibration_source']}\n")
        if thresholds["calibration_note"]:
            f.write(f"Calibration note: {thresholds['calibration_note']}\n")
        f.write("Threshold values:\n")
        f.write(f"- slightly suspicious >= {thresholds['slight_suspicion_min']:.2f}%\n")
        f.write(
            f"- highly suspicious >= {thresholds['high_suspicion_min']:.2f}% plus reciprocity/top-gap signal "
            f"(dominance_gap >= {thresholds['high_dominance_gap_min']:.2f})\n"
        )
        f.write("\nCohort statistics (context only, not used to set thresholds):\n")
        f.write(f"- median off-diagonal similarity: {thresholds['median']:.2f}%\n")
        f.write(f"- MAD: {thresholds['mad']:.2f}\n")
        f.write(f"- q90: {thresholds['q90']:.2f}%\n")
        f.write(f"- q97: {thresholds['q97']:.2f}%\n")
        f.write(f"- q99: {thresholds['q99']:.2f}%\n")
        f.write("\n")

        f.write("Category Counts:\n")
        f.write(f"- highly_suspicious: {counts['highly_suspicious']}\n")
        f.write(f"- slightly_suspicious: {counts['slightly_suspicious']}\n")
        f.write(f"- no_suspicion: {counts['no_suspicion']}\n\n")

        f.write("Top 100 suspicious pairs:\n")
        for rec in ordered[:100]:
            marker = ""
            if rec["reciprocal_top1"]:
                marker = " [reciprocal-top1]"
            f.write(
                f"- {rec['a']} <-> {rec['b']}: {rec['score']:.2f}% | {rec['category']} | "
                f"dominance_gap={rec['dominance_gap']:.2f}{marker}\n"
            )

    return output_path


def save_suspicion_csv(pairs, output_path="suspicion_pairs.csv"):
    ordered = sorted(pairs, key=lambda x: x["score"], reverse=True)
    with open(output_path, "w") as f:
        f.write("student_a,student_b,similarity_percent,category,reciprocal_top1,dominance_gap\n")
        for rec in ordered:
            f.write(
                f"{rec['a']},{rec['b']},{rec['score']:.2f},{rec['category']},"
                f"{str(rec['reciprocal_top1']).lower()},{rec['dominance_gap']:.2f}\n"
            )
    return output_path


def save_similarity_heatmap(
    filenames,
    matrix,
    pair_records=None,
    thresholds=None,
    output_path="similarity_heatmap.png",
):
    labels = [clean_label(name) for name in filenames]

    category_level = {
        "no_suspicion": 0,
        "slightly_suspicious": 1,
        "highly_suspicious": 2,
    }

    category_matrix = [[0 for _ in labels] for _ in labels]
    cell_details = [["" for _ in labels] for _ in labels]

    if pair_records and thresholds:
        categorized = categorize_pairs([dict(rec) for rec in pair_records], thresholds)
        index_by_label = {clean_label(name): idx for idx, name in enumerate(filenames)}
        for rec in categorized:
            i = index_by_label.get(rec["a"])
            j = index_by_label.get(rec["b"])
            if i is None or j is None:
                continue
            level = category_level.get(rec["category"], 0)
            category_matrix[i][j] = level
            category_matrix[j][i] = level
            cell_details[i][j] = (
                f"{rec['score']:.2f}%\n"
                f"g={rec['dominance_gap']:.2f}"
                f"{'\nR' if rec['reciprocal_top1'] else ''}"
            )
            cell_details[j][i] = cell_details[i][j]

    # Keep diagonal visually neutral.
    for idx in range(len(labels)):
        category_matrix[idx][idx] = -1

    cmap = colors.ListedColormap([
        "#d9d9d9",  # diagonal / self
        "#b9f6ca",  # no_suspicion
        "#ffe082",  # slightly_suspicious
        "#ef9a9a",  # highly_suspicious
    ])
    norm = colors.BoundaryNorm(boundaries=[-1.5, -0.5, 0.5, 1.5, 2.5], ncolors=cmap.N)

    fig, ax = plt.subplots(figsize=(10, 8))
    heatmap = ax.imshow(category_matrix, cmap=cmap, norm=norm)

    ax.set_xticks(range(len(labels)))
    ax.set_yticks(range(len(labels)))
    ax.set_xticklabels(labels, rotation=30, ha="right")
    ax.set_yticklabels(labels)
    ax.set_title("PARANOIA Suspicion Heatmap (Category-Based)")

    for i in range(len(matrix)):
        for j in range(len(matrix[i])):
            value = matrix[i][j]
            if i == j:
                ax.text(j, i, f"{value:.2f}", ha="center", va="center", color="black", fontsize=8)
                continue
            details = cell_details[i][j]
            if details:
                ax.text(j, i, details, ha="center", va="center", color="black", fontsize=8)
            else:
                ax.text(j, i, f"{value:.2f}%", ha="center", va="center", color="black", fontsize=8)

    cbar = fig.colorbar(heatmap, ax=ax)
    cbar.set_ticks([-1, 0, 1, 2])
    cbar.set_ticklabels(["self", "no", "slight", "high"])
    cbar.set_label("Suspicion Category")
    fig.tight_layout()
    fig.savefig(output_path, dpi=220)
    plt.close(fig)
    return output_path


def print_similarity_matrix(ngrams_by_file, calibration):
    filenames, matrix = build_similarity_matrix(ngrams_by_file)
    if len(filenames) < 2:
        print("Need at least two valid JSON fingerprints at root to compare.")
        return

    labels = [clean_label(name) for name in filenames]
    scores = {}
    for i, row in enumerate(labels):
        for j, col in enumerate(labels):
            scores[(row, col)] = f"{matrix[i][j]:.2f}"

    row_label_width = max(len(name) for name in labels)
    col_widths = {}
    for col in labels:
        max_score_width = max(len(scores[(row, col)]) for row in labels)
        col_widths[col] = max(len(col), max_score_width)

    if len(labels) <= 80:
        header = " " * (row_label_width + 2) + " ".join(col.rjust(col_widths[col]) for col in labels)
        print("Similarity Matrix (%):")
        print(header)

        for row in labels:
            row_values = " ".join(scores[(row, col)].rjust(col_widths[col]) for col in labels)
            print(f"{row.ljust(row_label_width)}  {row_values}")
    else:
        print(f"Similarity Matrix (%): {len(labels)} submissions. Full matrix omitted from stdout; use report files.")

    pairs, off_diag_scores = build_pair_records(filenames, matrix)
    thresholds = compute_thresholds(off_diagonal_scores=off_diag_scores, calibration=calibration)
    categorized_pairs = categorize_pairs(pairs, thresholds)

    image_path = save_similarity_heatmap(
        filenames,
        matrix,
        pair_records=pairs,
        thresholds=thresholds,
    )
    print(f"\nSaved heatmap to: {image_path}")

    report_path = save_suspicion_report(categorized_pairs, thresholds)
    csv_path = save_suspicion_csv(categorized_pairs)

    counts = {
        "highly_suspicious": sum(1 for x in categorized_pairs if x["category"] == "highly_suspicious"),
        "slightly_suspicious": sum(1 for x in categorized_pairs if x["category"] == "slightly_suspicious"),
        "no_suspicion": sum(1 for x in categorized_pairs if x["category"] == "no_suspicion"),
    }
    print("\nSuspicion categorization:")
    print(f"  highly_suspicious: {counts['highly_suspicious']}")
    print(f"  slightly_suspicious: {counts['slightly_suspicious']}")
    print(f"  no_suspicion: {counts['no_suspicion']}")
    print(f"Calibration source: {thresholds['calibration_source']}")
    print(f"Saved suspicion report to: {report_path}")
    print(f"Saved suspicion CSV to: {csv_path}")


def compare_pair(file1, file2, calibration):
    tokens1 = load_fingerprint(file1)
    tokens2 = load_fingerprint(file2)
    if not tokens1 or not tokens2:
        print("No structural tokens to compare.")
        sys.exit(1)
    ngrams1 = get_ngrams(tokens1)
    ngrams2 = get_ngrams(tokens2)
    similarity_score = calculate_jaccard_similarity(ngrams1, ngrams2)
    similarity_percent = round(similarity_score * 100, 2)
    print(f"Similarity: {similarity_percent}%")
    thresholds = compute_thresholds([], calibration)
    category = categorize_single_score(similarity_percent, thresholds)
    print(
        f"Category (calibrated thresholds): {category} "
        f"[slight>={thresholds['slight_suspicion_min']:.2f}%, high>={thresholds['high_suspicion_min']:.2f}%]"
    )
    print(f"Calibration source: {thresholds['calibration_source']}")
    if category == "highly_suspicious":
        print("Note: pair-only mode cannot evaluate reciprocal top-1 or dominance-gap cohort signals.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Compare PARANOIA fingerprints with calibrated suspicion thresholds.")
    parser.add_argument("file1", nargs="?", help="First fingerprint JSON file for pairwise mode")
    parser.add_argument("file2", nargs="?", help="Second fingerprint JSON file for pairwise mode")
    parser.add_argument(
        "--calibration",
        default=None,
        help=(
            "Path to calibration JSON file. "
            f"Defaults to {DEFAULT_CALIBRATION_FILENAME} next to compare.py"
        ),
    )
    args = parser.parse_args()

    if (args.file1 is None) != (args.file2 is None):
        parser.error("Provide both file1 and file2 for pairwise mode, or neither for cohort mode.")

    try:
        calibration = load_calibration(args.calibration)
    except Exception as e:
        print(f"Calibration error: {e}")
        sys.exit(2)

    if args.file1 and args.file2:
        compare_pair(args.file1, args.file2, calibration)
    else:
        root = Path.cwd()
        json_files = discover_root_jsons(root)
        if not json_files:
            print("No JSON files found at workspace root.")
            sys.exit(1)
        ngrams_by_file = load_ngrams_for_files(json_files)
        print_similarity_matrix(ngrams_by_file, calibration)