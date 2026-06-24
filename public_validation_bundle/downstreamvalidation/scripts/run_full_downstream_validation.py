from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

try:
    from scipy import stats
except Exception:  # pragma: no cover
    stats = None


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_VALIDATION_RESULTS = ROOT / "outputs" / "stream_loss_downstream24_top2500"
DEFAULT_RESULT_ROOT = ROOT / "outputs" / "full_downstream"
DEFAULT_ASSETS_ROOT = ROOT / "inputs" / "assets"
DEFAULT_FASTA = DEFAULT_ASSETS_ROOT / "human_reviewed_UP000005640_uniprot_20260425.fasta"
DEFAULT_TARGET_DECOY_FASTA = DEFAULT_ASSETS_ROOT / "human_reviewed_UP000005640_uniprot_20260425.target_decoy.fasta"
DEFAULT_MSFRAGGER_PARAMS = DEFAULT_ASSETS_ROOT / "closed_dda_target_decoy_fragger.params"
DEFAULT_TRACKCODEC_ROOT = Path(os.environ.get("TRACKCODEC_ROOT", str(ROOT.parent.parent)))
DEFAULT_TRACKCODEC_PARENT = Path(os.environ.get("TRACKCODEC_PARENT", str(DEFAULT_TRACKCODEC_ROOT.parent)))


def _default_mscodec_root(trackcodec_root: Path) -> Path:
    env_root = os.environ.get("MSCODEC_ROOT")
    if env_root:
        return Path(env_root)
    for candidate in (trackcodec_root.parent, *trackcodec_root.parents):
        if (candidate / "benchmark").exists():
            return candidate
    return trackcodec_root.parent


DEFAULT_MSCODEC_ROOT = _default_mscodec_root(DEFAULT_TRACKCODEC_ROOT)
DEFAULT_SHORT_DIANN_INPUT_ROOT = Path(
    os.environ.get("TRACKCODEC_SHORT_DIANN_INPUT_ROOT", str(ROOT / "outputs" / "diann_short_inputs"))
)
EXTRA_TOOL_PATHS = [
    Path(item)
    for item in os.environ.get("TRACKCODEC_EXTRA_TOOL_PATHS", "").split(os.pathsep)
    if item
]
plt = None


def _plt():
    global plt
    if plt is None:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as _pyplot

        plt = _pyplot
    return plt


def _safe_name(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", text.replace(".mzML", "")).strip("._") or "item"


def _resolve_public_path(value: Any) -> Path | None:
    text = str(value or "").strip()
    if not text:
        return None
    replacements = {
        "<MSCODEC_ROOT>": DEFAULT_MSCODEC_ROOT,
        "<TRACKCODEC_PARENT>": DEFAULT_TRACKCODEC_PARENT,
        "<TRACKCODEC_ROOT>": DEFAULT_TRACKCODEC_ROOT,
        "<TRACKCODEC_PUBLIC_RELEASE>": DEFAULT_TRACKCODEC_ROOT,
    }
    for token, root in replacements.items():
        if token in text:
            text = text.replace(token, str(root))
    text = os.path.expandvars(text)
    return Path(text).expanduser()


def _short_label(file_name: str) -> str:
    stem = file_name.replace(".mzML", "")
    replacements = [
        ("01625b_GA1-TUM_first_pool_1_01_01-", "TUM "),
        ("QC_", ""),
        ("File", "F"),
        ("uncompressed", "uncomp"),
        ("true_uncompressed", "true"),
        ("_293T_", " "),
        ("_Human_01", ""),
    ]
    for old, new in replacements:
        stem = stem.replace(old, new)
    return stem[:34] + "..." if len(stem) > 37 else stem


def _json_safe(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    return value


def _exit_code_ok(value: Any) -> bool:
    try:
        return int(str(value).strip()) == 0
    except Exception:
        return False


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_json_safe(payload), indent=2), encoding="utf-8")


def _write_csv(rows: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: _csv_value(row.get(key, "")) for key in fields})


def _csv_value(value: Any) -> Any:
    value = _json_safe(value)
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=True, sort_keys=True)
    return value


def _read_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        raise FileNotFoundError(path)
    with path.open("r", newline="", encoding="utf-8") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _ensure_path_for_tools() -> None:
    extra = [str(path) for path in EXTRA_TOOL_PATHS if path.exists()]
    if not extra:
        return
    old = os.environ.get("PATH", "")
    parts = old.split(os.pathsep) if old else []
    prefix = [p for p in extra if p not in parts]
    if prefix:
        os.environ["PATH"] = os.pathsep.join(prefix + parts)


def _which(name: str, *, required: bool = False) -> str:
    _ensure_path_for_tools()
    path = shutil.which(name)
    if not path and required:
        raise SystemExit(f"Required executable not found in PATH: {name}")
    return path or ""


def _run(cmd: list[str], log_path: Path, *, cwd: Path | None = None, timeout_sec: int | None = None) -> tuple[int, float]:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    start = time.perf_counter()
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(f"$ {' '.join(cmd)}\n")
        handle.flush()
        try:
            proc = subprocess.run(
                cmd,
                cwd=str(cwd) if cwd else None,
                stdout=handle,
                stderr=subprocess.STDOUT,
                check=False,
                timeout=timeout_sec,
            )
            code = int(proc.returncode)
        except subprocess.TimeoutExpired:
            code = 124
            handle.write(f"\nexit_code=124 timeout_sec={timeout_sec}\n")
        handle.write(f"\nexit_code={code}\n")
    return code, float(time.perf_counter() - start)


def _save(fig: Any, stem: Path) -> list[str]:
    stem.parent.mkdir(parents=True, exist_ok=True)
    paths = []
    for suffix in [".png", ".pdf"]:
        path = stem.with_suffix(suffix)
        fig.savefig(path, dpi=220, bbox_inches="tight", pad_inches=0.18)
        paths.append(str(path))
    _plt().close(fig)
    return paths


def _pearson(x: np.ndarray, y: np.ndarray) -> tuple[float, float]:
    mask = np.isfinite(x) & np.isfinite(y)
    if int(mask.sum()) < 3:
        return float("nan"), float("nan")
    x = x[mask]
    y = y[mask]
    if float(np.std(x)) == 0.0 or float(np.std(y)) == 0.0:
        return float("nan"), float("nan")
    if stats is not None:
        res = stats.pearsonr(x, y)
        return float(res.statistic), float(res.pvalue)
    return float(np.corrcoef(x, y)[0, 1]), float("nan")


def _spearman(x: np.ndarray, y: np.ndarray) -> tuple[float, float]:
    mask = np.isfinite(x) & np.isfinite(y)
    if int(mask.sum()) < 3:
        return float("nan"), float("nan")
    if stats is not None:
        res = stats.spearmanr(x[mask], y[mask])
        return float(res.statistic), float(res.pvalue)
    xr = pd.Series(x[mask]).rank(method="average").to_numpy(dtype=float)
    yr = pd.Series(y[mask]).rank(method="average").to_numpy(dtype=float)
    return _pearson(xr, yr)


def _jaccard(left: set[str], right: set[str]) -> float:
    union = left | right
    return 1.0 if not union else float(len(left & right) / len(union))


def _infer_modality(file_name: str) -> str:
    upper = file_name.upper()
    if "ETD" in upper:
        return "ETD"
    if "DIA" in upper or "AIF" in upper:
        return "DIA"
    if "DDA" in upper:
        return "DDA"
    return "unknown"


def _is_human_like(file_name: str) -> bool:
    upper = file_name.upper()
    return any(token in upper for token in ["293T", "HUMAN", "GA1-TUM", "TUM"])


def _default_diann_candidate(row: dict[str, Any], scope: str, include_aif: bool) -> bool:
    name = str(row["file_name"])
    upper = name.upper()
    if "AIF" in upper:
        return bool(include_aif and _is_human_like(name))
    if "DIA" not in upper:
        return False
    return scope == "all" or _is_human_like(name)


def _default_dda_candidate(row: dict[str, Any], scope: str) -> bool:
    name = str(row["file_name"])
    upper = name.upper()
    if "DDA" not in upper or "ETD" in upper:
        return False
    if "CENTROIDED" in upper:
        return False
    if scope == "all":
        return True
    if scope == "reference_full8":
        return any(token in name for token in ["GA1-TUM", "QC_E4804", "QC_E4805_240328"])
    return _is_human_like(name)


def discover_pairs(validation_results: Path, *, scope: str, include_aif_diann: bool) -> list[dict[str, Any]]:
    manifest = _read_csv_rows(validation_results / "tables" / "pair_manifest.csv")
    decode_status_path = validation_results / "tables" / "decode_status.csv"
    xic_summary_path = validation_results / "tables" / "xic_summary.csv"
    decoded = _read_csv_rows(decode_status_path) if decode_status_path.exists() else []
    if not decoded and xic_summary_path.exists():
        decoded = _read_csv_rows(xic_summary_path)
    decoded_by_index = {int(row["index"]): row for row in decoded if str(row.get("index", "")).strip()}
    pairs: list[dict[str, Any]] = []
    for row in manifest:
        idx = int(row["index"])
        decoded_row = decoded_by_index.get(idx, {})
        name = row["file_name"]
        original_path = _resolve_public_path(row.get("original_path", ""))
        reconstructed_path = _resolve_public_path(decoded_row.get("reconstructed_path", ""))
        out = {
            "index": idx,
            "dataset": row.get("dataset", ""),
            "file_name": name,
            "modality": _infer_modality(name),
            "original_path": str(original_path) if original_path is not None else "",
            "reconstructed_path": str(reconstructed_path) if reconstructed_path is not None else "",
            "original_exists": bool(original_path is not None and original_path.exists()),
            "reconstructed_exists": bool(reconstructed_path is not None and reconstructed_path.exists()),
        }
        out["diann_selected"] = bool(
            out["original_exists"]
            and out["reconstructed_exists"]
            and _default_diann_candidate(out, scope, include_aif_diann)
        )
        out["dda_selected"] = bool(
            out["original_exists"]
            and out["reconstructed_exists"]
            and _default_dda_candidate(out, scope)
        )
        pairs.append(out)
    return pairs


def _filter_pairs(rows: list[dict[str, Any]], only_terms: list[str]) -> list[dict[str, Any]]:
    if not only_terms:
        return rows
    terms = [term.lower() for term in only_terms]
    return [row for row in rows if any(term in str(row["file_name"]).lower() for term in terms)]


def write_manifest(
    pairs: list[dict[str, Any]],
    result_root: Path,
    *,
    assets: dict[str, Path],
    tools: dict[str, str],
) -> dict[str, Any]:
    rows = []
    for row in pairs:
        rows.append(
            {
                "index": row["index"],
                "dataset": row["dataset"],
                "file_name": row["file_name"],
                "modality": row["modality"],
                "original_path": row["original_path"],
                "reconstructed_path": row["reconstructed_path"],
                "original_exists": row["original_exists"],
                "reconstructed_exists": row["reconstructed_exists"],
                "diann_selected": row["diann_selected"],
                "dda_selected": row["dda_selected"],
            }
        )
    tables = result_root / "tables"
    _write_csv(rows, tables / "full_downstream_manifest.csv")
    tool_rows = [{"tool": key, "path": value, "available": bool(value)} for key, value in tools.items()]
    asset_rows = [{"asset": key, "path": str(value), "exists": value.exists(), "bytes": value.stat().st_size if value.exists() else ""} for key, value in assets.items()]
    _write_csv(tool_rows, tables / "full_downstream_tool_availability.csv")
    _write_csv(asset_rows, tables / "full_downstream_asset_availability.csv")
    plan = {
        "result_root": str(result_root),
        "total_pairs": len(pairs),
        "diann_selected_count": int(sum(bool(row["diann_selected"]) for row in pairs)),
        "dda_selected_count": int(sum(bool(row["dda_selected"]) for row in pairs)),
        "tools": tools,
        "assets": assets,
        "manifest": str(tables / "full_downstream_manifest.csv"),
    }
    _write_json(result_root / "full_downstream_plan.json", plan)
    return plan


def _resolve_predicted_library(base_path: Path) -> Path | None:
    candidates = [
        base_path,
        base_path.with_suffix(".predicted.speclib"),
        base_path.parent / f"{base_path.name}.predicted.speclib",
    ]
    for candidate in candidates:
        if candidate.exists() and candidate.stat().st_size > 0:
            return candidate
    found = sorted(base_path.parent.glob(f"{base_path.name}*.predicted.speclib"))
    return found[0] if found else None


def ensure_predicted_library(
    *,
    fasta: Path,
    result_root: Path,
    threads: int,
    explicit_lib: Path | None,
    dry_run: bool,
) -> tuple[Path | None, bool, float]:
    if explicit_lib:
        resolved = _resolve_predicted_library(explicit_lib)
        if not resolved:
            raise FileNotFoundError(f"Predicted DIA-NN library not found: {explicit_lib}")
        return resolved, True, 0.0

    out_base = result_root / "assets" / "human_reviewed_20260425"
    resolved = _resolve_predicted_library(out_base)
    if resolved:
        return resolved, True, 0.0
    if dry_run:
        return out_base.with_suffix(".predicted.speclib"), False, 0.0

    if not fasta.exists():
        raise FileNotFoundError(f"FASTA missing: {fasta}")
    diann = _which("diann", required=True)
    out_base.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        diann,
        "--fasta",
        str(fasta),
        "--fasta-search",
        "--predictor",
        "--out-lib",
        str(out_base),
        "--threads",
        str(max(1, int(threads))),
        "--cut",
        "K*,R*,!*P",
        "--missed-cleavages",
        "1",
        "--fixed-mod",
        "UniMod:4,57.021464,C",
        "--var-mod",
        "UniMod:35,15.994915,M",
        "--var-mod",
        "UniMod:1,42.010565,*n",
        "--met-excision",
    ]
    code, elapsed = _run(cmd, result_root / "logs" / "generate_predicted_library.log", cwd=result_root)
    if code != 0:
        raise RuntimeError(f"DIA-NN predicted library generation failed with exit code {code}")
    resolved = _resolve_predicted_library(out_base)
    if not resolved:
        raise RuntimeError("DIA-NN predicted library was not generated.")
    return resolved, False, elapsed


def _detect_output_file(run_dir: Path, suffix: str) -> Path:
    exact = run_dir / suffix
    if exact.exists() and exact.stat().st_size > 0:
        return exact
    found = sorted(path for path in run_dir.glob(f"*{suffix}") if path.stat().st_size > 0)
    if found:
        return found[0]
    raise FileNotFoundError(f"Could not find output suffix {suffix!r} under {run_dir}")


def _resolve_matrix_run_column(df: pd.DataFrame, run_name: str) -> str:
    candidates: list[str] = []
    for col in df.columns:
        col_name = Path(str(col)).name
        if str(col) == run_name or col_name == run_name:
            return str(col)
        if run_name in str(col) or run_name in col_name:
            candidates.append(str(col))
    if len(candidates) == 1:
        return candidates[0]
    if candidates:
        candidates.sort(key=lambda item: (len(item), item))
        return candidates[0]
    raise KeyError(f"Could not resolve run column {run_name!r} from {list(df.columns)}")


def _detect_merge_key(df: pd.DataFrame, kind: str) -> str:
    if kind == "precursor":
        candidates = ["Precursor.Id", "PrecursorID", "Precursor"]
    else:
        candidates = ["Protein.Group", "Protein.Groups", "Protein.Ids", "Protein.Ids.Group", "Protein.Names", "Genes"]
    for col in candidates:
        if col in df.columns:
            return col
    return str(df.columns[0])


def _load_diann_quant_single(matrix_path: Path, *, run_name: str, kind: str) -> tuple[pd.DataFrame, str, str]:
    df = pd.read_csv(matrix_path, sep="\t", low_memory=False)
    qty_col = _resolve_matrix_run_column(df, run_name)
    key_col = _detect_merge_key(df, kind)
    out = df[[key_col, qty_col]].copy()
    out.columns = ["key", "qty"]
    out["qty"] = pd.to_numeric(out["qty"], errors="coerce")
    out = out.replace({0: np.nan}).dropna(subset=["qty"])
    return out, key_col, qty_col


def _load_diann_stats(path: Path) -> dict[str, float]:
    df = pd.read_csv(path, sep="\t", low_memory=False)
    if df.empty:
        return {}
    return {str(key): value for key, value in df.iloc[0].to_dict().items()}


def _prepare_diann_input(
    source: Path,
    *,
    result_root: Path,
    slug: str,
    variant: str,
    rewrite_original: bool,
    rewrite_reconstructed: bool,
) -> tuple[Path, bool, float]:
    should_rewrite = (variant == "original" and rewrite_original) or (variant == "reconstructed" and rewrite_reconstructed)
    if not should_rewrite:
        return source, True, 0.0
    short_id = slug.split("_", 1)[0]
    suffix = "original.diann_input.mzML" if variant == "original" else "diann_input.mzML"
    out = DEFAULT_SHORT_DIANN_INPUT_ROOT / f"{short_id}.{suffix}"
    if out.exists() and out.stat().st_size > 0:
        return out, True, 0.0
    out.parent.mkdir(parents=True, exist_ok=True)
    file_filter = _which("FileFilter", required=True)
    cmd = [
        file_filter,
        "-in",
        str(source),
        "-out",
        str(out),
        "-peak_options:indexed_file",
        "true",
        "-peak_options:zlib_compression",
        "true",
    ]
    code, elapsed = _run(cmd, result_root / "logs" / f"{slug}.{variant}.diann_input_rewrite.log")
    if code != 0:
        raise RuntimeError(f"FileFilter rewrite failed for {source} with exit code {code}")
    return out, False, elapsed


def _diann_run_cache_valid(run_dir: Path) -> bool:
    required = ["report.stats.tsv", "report.parquet", "report.pr_matrix.tsv", "report.pg_matrix.tsv"]
    if not all((run_dir / name).exists() and (run_dir / name).stat().st_size > 0 for name in required):
        return False
    log_path = run_dir / "diann.log"
    if not log_path.exists() or log_path.stat().st_size <= 0:
        return False
    text = log_path.read_text(encoding="utf-8", errors="replace")
    bad_markers = [
        "ERROR: cannot save the .quant file",
        "ERROR: cannot read the.quant file",
        "ERROR: empty .quant file",
    ]
    return not any(marker in text for marker in bad_markers)


def _plot_diann_scatter(title: str, merged: pd.DataFrame, path: Path) -> None:
    plt = _plt()
    fig, ax = plt.subplots(figsize=(5.7, 5.2))
    if merged.empty:
        ax.text(0.5, 0.5, "No shared quantified entries", ha="center", va="center", transform=ax.transAxes)
    else:
        sample = merged
        if len(sample) > 25000:
            sample = sample.sample(n=25000, random_state=42)
        x = np.log2(sample["orig_qty"].to_numpy(dtype=float) + 1.0)
        y = np.log2(sample["recon_qty"].to_numpy(dtype=float) + 1.0)
        ax.scatter(x, y, s=6, alpha=0.20, linewidths=0, color="#526D9D")
        lo = float(min(np.nanmin(x), np.nanmin(y)))
        hi = float(max(np.nanmax(x), np.nanmax(y)))
        pad = max((hi - lo) * 0.04, 0.25)
        ax.plot([lo - pad, hi + pad], [lo - pad, hi + pad], color="black", linestyle="--", linewidth=1.0)
        ax.set_xlim(lo - pad, hi + pad)
        ax.set_ylim(lo - pad, hi + pad)
    ax.set_title(title, fontsize=9)
    ax.set_xlabel("Original DIA-NN log2(quantity + 1)")
    ax.set_ylabel("Reconstructed DIA-NN log2(quantity + 1)")
    fig.tight_layout()
    _save(fig, path)


def run_diann(
    pairs: list[dict[str, Any]],
    result_root: Path,
    *,
    fasta: Path,
    predicted_lib: Path | None,
    threads: int,
    rewrite_original: bool,
    rewrite_reconstructed: bool,
    dry_run: bool,
    max_files: int,
    skip_plots: bool,
) -> dict[str, Any]:
    selected = [row for row in pairs if row["diann_selected"]]
    if max_files > 0:
        selected = selected[:max_files]
    out_root = result_root / "diann_single_run"
    (out_root / "tables").mkdir(parents=True, exist_ok=True)
    (out_root / "plots").mkdir(parents=True, exist_ok=True)

    lib, lib_cached, lib_elapsed = ensure_predicted_library(
        fasta=fasta,
        result_root=out_root,
        threads=threads,
        explicit_lib=predicted_lib,
        dry_run=dry_run,
    )
    run_rows: list[dict[str, Any]] = []
    summary_rows: list[dict[str, Any]] = []
    if dry_run:
        for row in selected:
            for variant, source in [("original", row["original_path"]), ("reconstructed", row["reconstructed_path"])]:
                run_rows.append(
                    {
                        "file": row["file_name"],
                        "index": row["index"],
                        "variant": variant,
                        "input_path": source,
                        "planned": True,
                    }
                )
        _write_csv(run_rows, out_root / "tables" / "trackcodec_diann_single_run_run_log.csv")
        return {"stage": "diann", "dry_run": True, "file_count": len(selected), "predicted_lib": str(lib)}

    diann = _which("diann", required=True)
    for row in selected:
        slug = f"{int(row['index']):02d}_{_safe_name(str(row['file_name']))}"
        artifacts: dict[str, dict[str, Any]] = {}
        for variant, source_text in [("original", row["original_path"]), ("reconstructed", row["reconstructed_path"])]:
            source = Path(str(source_text))
            prepared, input_cached, input_elapsed = _prepare_diann_input(
                source,
                result_root=out_root,
                slug=slug,
                variant=variant,
                rewrite_original=rewrite_original,
                rewrite_reconstructed=rewrite_reconstructed,
            )
            run_dir = out_root / "runs" / slug / variant
            run_dir.mkdir(parents=True, exist_ok=True)
            report_path = run_dir / "report.tsv"
            temp_dir = run_dir / "temp"
            temp_dir.mkdir(parents=True, exist_ok=True)
            cached = _diann_run_cache_valid(run_dir)
            elapsed = 0.0
            if not cached:
                if run_dir.exists():
                    shutil.rmtree(run_dir)
                run_dir.mkdir(parents=True, exist_ok=True)
                temp_dir = run_dir / "temp"
                temp_dir.mkdir(parents=True, exist_ok=True)
                cmd = [
                    diann,
                    "--lib",
                    str(lib),
                    "--fasta",
                    str(fasta),
                    "--threads",
                    str(max(1, int(threads))),
                    "--verbose",
                    "2",
                    "--qvalue",
                    "0.01",
                    "--matrices",
                    "--matrix-qvalue",
                    "0.01",
                    "--temp",
                    str(temp_dir),
                    "--out",
                    str(report_path),
                    "--f",
                    str(prepared),
                ]
                code, elapsed = _run(cmd, run_dir / "diann.log", cwd=run_dir)
                if code != 0:
                    run_rows.append(
                        {
                            "file": row["file_name"],
                            "index": row["index"],
                            "variant": variant,
                            "step": "diann_single_run",
                            "status": "failed",
                            "exit_code": code,
                            "elapsed_s": elapsed,
                            "input_path": str(prepared),
                            "log": str(run_dir / "diann.log"),
                        }
                    )
                    continue
            try:
                artifacts[variant] = {
                    "input_path": str(prepared),
                    "pr_matrix_path": str(_detect_output_file(run_dir, "pr_matrix.tsv")),
                    "pg_matrix_path": str(_detect_output_file(run_dir, "pg_matrix.tsv")),
                    "stats_path": str(_detect_output_file(run_dir, "report.stats.tsv")),
                    "report_path": str(_detect_output_file(run_dir, "report.parquet")),
                }
                status = "ok"
            except FileNotFoundError as exc:
                artifacts[variant] = {"input_path": str(prepared), "error": str(exc)}
                status = "missing_outputs"
            run_rows.extend(
                [
                    {
                        "file": row["file_name"],
                        "index": row["index"],
                        "variant": variant,
                        "step": "prepare_input",
                        "status": "cached" if input_cached else "ok",
                        "elapsed_s": input_elapsed,
                        "path": str(prepared),
                    },
                    {
                        "file": row["file_name"],
                        "index": row["index"],
                        "variant": variant,
                        "step": "diann_single_run",
                        "status": "cached" if cached and status == "ok" else status,
                        "elapsed_s": elapsed,
                        "path": str(run_dir),
                    },
                ]
            )
        if not {"original", "reconstructed"}.issubset(artifacts):
            continue
        if "error" in artifacts["original"] or "error" in artifacts["reconstructed"]:
            continue
        summary_rows.append(_summarize_diann_pair(row, artifacts, out_root, skip_plots=skip_plots))

    _write_csv(run_rows, out_root / "tables" / "trackcodec_diann_single_run_run_log.csv")
    _write_csv(summary_rows, out_root / "tables" / "trackcodec_diann_single_run_summary_per_file.csv")
    if not skip_plots:
        _plot_diann_summary(pd.DataFrame(summary_rows), out_root / "plots" / "summary")
    summary = {
        "stage": "diann",
        "dry_run": False,
        "file_count": len(selected),
        "completed_summary_count": len(summary_rows),
        "predicted_lib": str(lib),
        "predicted_lib_cached": lib_cached,
        "predicted_lib_elapsed_s": lib_elapsed,
        "summary_table": str(out_root / "tables" / "trackcodec_diann_single_run_summary_per_file.csv"),
        "run_log": str(out_root / "tables" / "trackcodec_diann_single_run_run_log.csv"),
    }
    _write_json(out_root / "summary.json", summary)
    return summary


def _summarize_diann_pair(row: dict[str, Any], artifacts: dict[str, dict[str, Any]], out_root: Path, *, skip_plots: bool = False) -> dict[str, Any]:
    orig = artifacts["original"]
    recon = artifacts["reconstructed"]
    orig_run = Path(str(orig["input_path"])).name
    recon_run = Path(str(recon["input_path"])).name
    orig_stats = _load_diann_stats(Path(str(orig["stats_path"])))
    recon_stats = _load_diann_stats(Path(str(recon["stats_path"])))

    orig_pr, _, orig_pr_col = _load_diann_quant_single(Path(str(orig["pr_matrix_path"])), run_name=orig_run, kind="precursor")
    recon_pr, _, recon_pr_col = _load_diann_quant_single(Path(str(recon["pr_matrix_path"])), run_name=recon_run, kind="precursor")
    orig_pg, _, orig_pg_col = _load_diann_quant_single(Path(str(orig["pg_matrix_path"])), run_name=orig_run, kind="protein")
    recon_pg, _, recon_pg_col = _load_diann_quant_single(Path(str(recon["pg_matrix_path"])), run_name=recon_run, kind="protein")

    pr = orig_pr.merge(recon_pr, on="key", how="inner", suffixes=("_orig", "_recon"))
    pr.columns = ["key", "orig_qty", "recon_qty"]
    pg = orig_pg.merge(recon_pg, on="key", how="inner", suffixes=("_orig", "_recon"))
    pg.columns = ["key", "orig_qty", "recon_qty"]
    slug = f"{int(row['index']):02d}_{_safe_name(str(row['file_name']))}"
    pair_dir = out_root / "paired_tables"
    pair_dir.mkdir(parents=True, exist_ok=True)
    pr.to_csv(pair_dir / f"{slug}.precursor.diann_pairwise.tsv", sep="\t", index=False)
    pg.to_csv(pair_dir / f"{slug}.protein_group.diann_pairwise.tsv", sep="\t", index=False)
    if not skip_plots:
        _plot_diann_scatter(
            f"{_short_label(str(row['file_name']))} precursor",
            pr,
            out_root / "plots" / "scatter" / f"{slug}.precursor_quant_scatter",
        )
        _plot_diann_scatter(
            f"{_short_label(str(row['file_name']))} protein group",
            pg,
            out_root / "plots" / "scatter" / f"{slug}.protein_group_quant_scatter",
        )

    orig_pr_keys = set(orig_pr["key"].astype(str))
    recon_pr_keys = set(recon_pr["key"].astype(str))
    orig_pg_keys = set(orig_pg["key"].astype(str))
    recon_pg_keys = set(recon_pg["key"].astype(str))
    pr_x = np.log2(pr["orig_qty"].to_numpy(dtype=float) + 1.0) if not pr.empty else np.asarray([])
    pr_y = np.log2(pr["recon_qty"].to_numpy(dtype=float) + 1.0) if not pr.empty else np.asarray([])
    pg_x = np.log2(pg["orig_qty"].to_numpy(dtype=float) + 1.0) if not pg.empty else np.asarray([])
    pg_y = np.log2(pg["recon_qty"].to_numpy(dtype=float) + 1.0) if not pg.empty else np.asarray([])
    pr_pearson, pr_pearson_p = _pearson(pr_x, pr_y)
    pr_spearman, pr_spearman_p = _spearman(pr_x, pr_y)
    pg_pearson, pg_pearson_p = _pearson(pg_x, pg_y)
    pg_spearman, pg_spearman_p = _spearman(pg_x, pg_y)
    pr_delta = np.abs(pr_y - pr_x)
    pg_delta = np.abs(pg_y - pg_x)
    return {
        "index": row["index"],
        "file": row["file_name"],
        "original_precursors_identified": int(float(orig_stats.get("Precursors.Identified", 0) or 0)),
        "reconstructed_precursors_identified": int(float(recon_stats.get("Precursors.Identified", 0) or 0)),
        "original_proteins_identified": int(float(orig_stats.get("Proteins.Identified", 0) or 0)),
        "reconstructed_proteins_identified": int(float(recon_stats.get("Proteins.Identified", 0) or 0)),
        "original_quantified_precursors": len(orig_pr_keys),
        "reconstructed_quantified_precursors": len(recon_pr_keys),
        "shared_quantified_precursors": len(orig_pr_keys & recon_pr_keys),
        "quantified_precursor_jaccard": _jaccard(orig_pr_keys, recon_pr_keys),
        "original_quantified_protein_groups": len(orig_pg_keys),
        "reconstructed_quantified_protein_groups": len(recon_pg_keys),
        "shared_quantified_protein_groups": len(orig_pg_keys & recon_pg_keys),
        "quantified_protein_group_jaccard": _jaccard(orig_pg_keys, recon_pg_keys),
        "shared_precursor_quant_entries": len(pr),
        "precursor_quant_log2_pearson": pr_pearson,
        "precursor_quant_log2_pearson_p": pr_pearson_p,
        "precursor_quant_log2_spearman": pr_spearman,
        "precursor_quant_log2_spearman_p": pr_spearman_p,
        "precursor_abs_log2_fc_median": float(np.nanmedian(pr_delta)) if len(pr_delta) else float("nan"),
        "precursor_abs_log2_fc_p95": float(np.nanpercentile(pr_delta, 95)) if len(pr_delta) else float("nan"),
        "shared_protein_quant_entries": len(pg),
        "protein_quant_log2_pearson": pg_pearson,
        "protein_quant_log2_pearson_p": pg_pearson_p,
        "protein_quant_log2_spearman": pg_spearman,
        "protein_quant_log2_spearman_p": pg_spearman_p,
        "protein_abs_log2_fc_median": float(np.nanmedian(pg_delta)) if len(pg_delta) else float("nan"),
        "protein_abs_log2_fc_p95": float(np.nanpercentile(pg_delta, 95)) if len(pg_delta) else float("nan"),
        "original_pr_matrix_column": orig_pr_col,
        "reconstructed_pr_matrix_column": recon_pr_col,
        "original_pg_matrix_column": orig_pg_col,
        "reconstructed_pg_matrix_column": recon_pg_col,
    }


def _plot_diann_summary(summary: pd.DataFrame, out_dir: Path) -> None:
    if summary.empty:
        return
    plt = _plt()
    labels = [_short_label(str(v)) for v in summary["file"]]
    x = np.arange(len(summary))
    fig, ax = plt.subplots(figsize=(10.5, 4.8))
    ax.plot(x, summary["quantified_precursor_jaccard"], marker="o", label="precursor Jaccard", color="#526D9D")
    ax.plot(x, summary["quantified_protein_group_jaccard"], marker="s", label="protein-group Jaccard", color="#2F5D62")
    ax.plot(x, summary["precursor_quant_log2_pearson"], marker="^", label="precursor Pearson", color="#B86B25")
    ax.set_ylim(max(0.0, float(np.nanmin(summary[["quantified_precursor_jaccard", "quantified_protein_group_jaccard", "precursor_quant_log2_pearson"]].to_numpy())) - 0.02), 1.0005)
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=35, ha="right", fontsize=8)
    ax.set_ylabel("Agreement")
    ax.set_title("DIA-NN Single-Run Agreement")
    ax.grid(alpha=0.24, axis="y")
    ax.legend(loc="lower left", fontsize=8)
    fig.tight_layout()
    _save(fig, out_dir / "trackcodec_diann_agreement_summary")


def _build_msfragger_params(template: Path, output: Path, *, database: Path, threads: int) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    replacements = {
        "database_name": str(database),
        "num_threads": str(max(1, int(threads))),
        "data_type": "0",
        "decoy_prefix": "rev_",
        "output_format": "tsv_pepxml",
        "write_calibrated_mzml": "0",
        "write_uncalibrated_mzml": "0",
        "calibrate_mass": "2",
        "activation_types": "all",
        "analyzer_types": "all",
        "labile_fragment_ion_series": "b,y",
    }
    out_lines: list[str] = []
    written: set[str] = set()
    for line in template.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            out_lines.append(line)
            continue
        key = line.split("=", 1)[0].strip()
        if key in replacements:
            comment = ""
            if "#" in line:
                comment = "\t\t\t#" + line.split("#", 1)[1]
            out_lines.append(f"{key} = {replacements[key]}{comment}")
            written.add(key)
        else:
            out_lines.append(line)
    for key, value in replacements.items():
        if key not in written:
            out_lines.append(f"{key} = {value}")
    output.write_text("\n".join(out_lines) + "\n", encoding="utf-8")


def _link_or_copy(source: Path, target: Path, *, force: bool = False) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if not force and target.stat().st_size == source.stat().st_size:
            return
        target.unlink()
    try:
        os.link(source, target)
    except OSError:
        shutil.copy2(source, target)


def _find_table(work_dir: Path, name: str) -> Path | None:
    candidates = [work_dir / name, work_dir / "combined" / name]
    candidates.extend(sorted(work_dir.rglob(name)))
    for path in candidates:
        if path.exists() and path.stat().st_size > 0:
            return path
    return None


def _find_msfragger_file(work_dir: Path, suffix: str) -> Path | None:
    for path in sorted(work_dir.rglob(f"*{suffix}")):
        if path.exists() and path.stat().st_size > 0:
            return path
    return None


def _spectrum_for_existing_msfragger(work_dir: Path, existing_pepxml: Path | None, fallback: Path) -> Path:
    if existing_pepxml is not None:
        candidate = existing_pepxml.with_suffix(".mzML")
        if candidate.exists() and candidate.stat().st_size > 0:
            return candidate
    return fallback


def _msfragger_needs_peak_picking(log_path: Path) -> bool:
    if not log_path.exists():
        return False
    text = log_path.read_text(encoding="utf-8", errors="replace").lower()
    markers = [
        "non-centroid ms2 spectra",
        "please prepare the spectral file with peak picking",
    ]
    return any(marker in text for marker in markers)


def _peak_pick_for_msfragger(
    source_mzml: Path,
    result_root: Path,
    slug: str,
    condition: str,
    *,
    force: bool,
    timeout_sec: int | None,
) -> tuple[Path, str, float, int]:
    peak_picker = _which("PeakPickerHiRes", required=True)
    prepared_dir = result_root / "prepared_mzml" / slug / condition
    prepared_dir.mkdir(parents=True, exist_ok=True)
    stem = source_mzml.name[:-5] if source_mzml.name.lower().endswith(".mzml") else source_mzml.stem
    output = prepared_dir / f"{stem}.peakpicked.mzML"
    log_path = result_root / "logs" / f"{slug}.{condition}.peakpicker_hires.log"
    if output.exists() and output.stat().st_size > 0 and not force:
        return output, str(log_path), 0.0, 0
    cmd = [
        peak_picker,
        "-in",
        str(source_mzml),
        "-out",
        str(output),
        "-threads",
        "1",
    ]
    code, elapsed = _run(cmd, log_path, timeout_sec=timeout_sec)
    return output, str(log_path), elapsed, code


def _run_msfragger_condition(
    msfragger_bin: str,
    run: dict[str, Any],
    condition: str,
    result_root: Path,
    params_path: Path,
    *,
    force: bool,
    timeout_sec: int | None,
) -> dict[str, Any]:
    slug = str(run["slug"])
    work_dir = result_root / "runs" / slug / condition / "msfragger"
    spectra_dir = work_dir / "spectra"
    log_path = result_root / "logs" / f"{slug}.{condition}.msfragger.log"
    source_mzml = Path(str(run[f"{condition}_mzml"]))
    if force and work_dir.exists():
        shutil.rmtree(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    spectra_dir.mkdir(parents=True, exist_ok=True)
    existing_pepxml = _find_msfragger_file(work_dir, ".pepXML")
    existing_tsv = _find_msfragger_file(work_dir, ".tsv")
    if existing_pepxml and not force:
        spectrum_path = _spectrum_for_existing_msfragger(work_dir, existing_pepxml, spectra_dir / source_mzml.name)
        return {
            "file": run["file"],
            "index": run["index"],
            "slug": slug,
            "condition": condition,
            "status": "cached",
            "exit_code": 0,
            "elapsed_sec": 0.0,
            "source_mzml": str(source_mzml),
            "spectrum_path": str(spectrum_path),
            "peak_picked_for_msfragger": "peakpicked" in spectrum_path.name.lower(),
            "peak_picker_exit_code": "",
            "peak_picker_elapsed_sec": 0.0,
            "peak_picker_log": "",
            "pepxml": str(existing_pepxml),
            "tsv": str(existing_tsv) if existing_tsv else "",
            "work_dir": str(work_dir),
            "log": str(log_path),
        }
    search_mzml = source_mzml
    spectrum_path = spectra_dir / search_mzml.name
    _link_or_copy(search_mzml, spectrum_path, force=force)
    code, elapsed = _run([msfragger_bin, str(params_path), str(spectrum_path)], log_path, cwd=work_dir, timeout_sec=timeout_sec)
    peak_picked = False
    peak_picker_log = ""
    peak_picker_elapsed = 0.0
    peak_picker_exit_code = ""
    if code != 0 and _msfragger_needs_peak_picking(log_path):
        picked_mzml, peak_picker_log, peak_picker_elapsed, pp_code = _peak_pick_for_msfragger(
            source_mzml,
            result_root,
            slug,
            condition,
            force=force,
            timeout_sec=timeout_sec,
        )
        peak_picker_exit_code = pp_code
        if pp_code == 0 and picked_mzml.exists() and picked_mzml.stat().st_size > 0:
            peak_picked = True
            search_mzml = picked_mzml
            spectrum_path = spectra_dir / search_mzml.name
            _link_or_copy(search_mzml, spectrum_path, force=force)
            retry_log = result_root / "logs" / f"{slug}.{condition}.msfragger.peakpicked_retry.log"
            code, retry_elapsed = _run([msfragger_bin, str(params_path), str(spectrum_path)], retry_log, cwd=work_dir, timeout_sec=timeout_sec)
            elapsed += peak_picker_elapsed + retry_elapsed
            log_path = retry_log
        else:
            elapsed += peak_picker_elapsed
    pepxml = _find_msfragger_file(work_dir, ".pepXML")
    tsv = _find_msfragger_file(work_dir, ".tsv")
    return {
        "file": run["file"],
        "index": run["index"],
        "slug": slug,
        "condition": condition,
        "status": "ok" if code == 0 and pepxml else "failed",
        "exit_code": code,
        "elapsed_sec": elapsed,
        "source_mzml": str(source_mzml),
        "spectrum_path": str(spectrum_path),
        "peak_picked_for_msfragger": peak_picked,
        "peak_picker_exit_code": peak_picker_exit_code,
        "peak_picker_elapsed_sec": peak_picker_elapsed,
        "peak_picker_log": peak_picker_log,
        "pepxml": str(pepxml) if pepxml else "",
        "tsv": str(tsv) if tsv else "",
        "work_dir": str(work_dir),
        "log": str(log_path),
    }


def _run_philosopher_condition(
    philosopher_bin: str,
    run: dict[str, Any],
    condition: str,
    result_root: Path,
    fasta: Path,
    tmp_root: Path,
    msfragger_row: dict[str, Any],
    *,
    force: bool,
    timeout_sec: int | None,
) -> dict[str, Any]:
    slug = str(run["slug"])
    work_dir = result_root / "runs" / slug / condition / "philosopher"
    tmp_dir = tmp_root / slug / condition
    log_path = result_root / "logs" / f"{slug}.{condition}.philosopher.log"
    existing = [_find_table(work_dir, name) for name in ["psm.tsv", "peptide.tsv", "protein.tsv"]]
    if all(existing) and not force:
        return {
            "file": run["file"],
            "index": run["index"],
            "slug": slug,
            "condition": condition,
            "status": "cached",
            "exit_code": 0,
            "elapsed_sec": 0.0,
            "psm_tsv": str(existing[0]),
            "peptide_tsv": str(existing[1]),
            "protein_tsv": str(existing[2]),
            "spectrum_path": str(_find_philosopher_spectrum_path(work_dir, msfragger_row, run, condition)),
            "source_mzml": str(msfragger_row.get("source_mzml", "")),
            "peak_picked_for_msfragger": msfragger_row.get("peak_picked_for_msfragger", ""),
            "work_dir": str(work_dir),
            "tmp_dir": str(tmp_dir),
            "log": str(log_path),
        }
    if force and work_dir.exists():
        shutil.rmtree(work_dir)
    if force and tmp_dir.exists():
        shutil.rmtree(tmp_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    tmp_dir.mkdir(parents=True, exist_ok=True)
    pepxml = Path(str(msfragger_row.get("pepxml") or ""))
    mzml = Path(str(run[f"{condition}_mzml"]))
    if not pepxml.exists():
        return {
            "file": run["file"],
            "index": run["index"],
            "slug": slug,
            "condition": condition,
            "status": "missing_pepxml",
            "exit_code": 1,
            "elapsed_sec": 0.0,
            "spectrum_path": str(msfragger_row.get("spectrum_path", "")),
            "source_mzml": str(msfragger_row.get("source_mzml", "")),
            "peak_picked_for_msfragger": msfragger_row.get("peak_picked_for_msfragger", ""),
            "work_dir": str(work_dir),
            "tmp_dir": str(tmp_dir),
            "log": str(log_path),
        }
    _link_or_copy(pepxml, tmp_dir / "input.pepXML", force=force)
    search_mzml = Path(str(msfragger_row.get("spectrum_path") or mzml))
    if search_mzml.exists():
        _link_or_copy(search_mzml, tmp_dir / search_mzml.name, force=force)
    else:
        _link_or_copy(mzml, tmp_dir / mzml.name, force=force)
    commands = [
        [philosopher_bin, "workspace", "--clean"],
        [philosopher_bin, "workspace", "--init"],
        [philosopher_bin, "database", "--annotate", str(fasta), "--prefix", "rev_"],
        [
            philosopher_bin,
            "peptideprophet",
            "--database",
            str(fasta),
            "--decoy",
            "rev_",
            "--ppm",
            "--accmass",
            "--nonparam",
            "input.pepXML",
        ],
        [philosopher_bin, "proteinprophet", "interact-input.pep.xml"],
        [
            philosopher_bin,
            "filter",
            "--sequential",
            "--razor",
            "--mapmods",
            "--tag",
            "rev_",
            "--pepxml",
            "interact-input.pep.xml",
            "--protxml",
            "interact.prot.xml",
        ],
        [philosopher_bin, "report"],
    ]
    exit_code = 0
    elapsed_total = 0.0
    for cmd in commands:
        code, elapsed = _run(cmd, log_path, cwd=tmp_dir, timeout_sec=timeout_sec)
        elapsed_total += elapsed
        if code != 0:
            exit_code = code
            break
    for name in ["psm.tsv", "ion.tsv", "peptide.tsv", "protein.tsv", "protein.fas", "interact-input.pep.xml", "interact.prot.xml"]:
        source = tmp_dir / name
        if source.exists() and source.stat().st_size > 0:
            shutil.copy2(source, work_dir / name)
    if (work_dir / "interact-input.pep.xml").exists() and not (work_dir / "interact.pep.xml").exists():
        shutil.copy2(work_dir / "interact-input.pep.xml", work_dir / "interact.pep.xml")
    psm = _find_table(work_dir, "psm.tsv")
    peptide = _find_table(work_dir, "peptide.tsv")
    protein = _find_table(work_dir, "protein.tsv")
    return {
        "file": run["file"],
        "index": run["index"],
        "slug": slug,
        "condition": condition,
        "status": "ok" if exit_code == 0 and psm and peptide and protein else "failed",
        "exit_code": exit_code,
        "elapsed_sec": elapsed_total,
        "psm_tsv": str(psm) if psm else "",
        "peptide_tsv": str(peptide) if peptide else "",
        "protein_tsv": str(protein) if protein else "",
        "spectrum_path": str(search_mzml if search_mzml.exists() else mzml),
        "source_mzml": str(msfragger_row.get("source_mzml", "")),
        "peak_picked_for_msfragger": msfragger_row.get("peak_picked_for_msfragger", ""),
        "work_dir": str(work_dir),
        "tmp_dir": str(tmp_dir),
        "log": str(log_path),
    }


def _find_philosopher_spectrum_path(work_dir: Path, msfragger_row: dict[str, Any], run: dict[str, Any], condition: str) -> Path:
    spectrum_path = Path(str(msfragger_row.get("spectrum_path") or ""))
    if spectrum_path.exists():
        return spectrum_path
    expected_name = ""
    try:
        psm = _find_table(work_dir, "psm.tsv")
        if psm:
            with psm.open("r", encoding="utf-8", errors="replace") as handle:
                header = handle.readline().rstrip("\n").split("\t")
                first = handle.readline().rstrip("\n").split("\t")
            if "Spectrum" in header and len(first) >= len(header):
                spectrum = first[header.index("Spectrum")]
                parts = spectrum.split(".")
                if len(parts) >= 4:
                    expected_name = ".".join(parts[:-3]) + ".mzML"
    except Exception:
        expected_name = ""
    if expected_name:
        for candidate in sorted((work_dir.parent / "msfragger" / "spectra").glob(expected_name)):
            if candidate.exists() and candidate.stat().st_size > 0:
                return candidate
    return Path(str(run[f"{condition}_mzml"]))


def _collect_quant_tables(output_dir: Path) -> dict[str, Path]:
    tables: dict[str, Path] = {}
    for search_dir in [output_dir, output_dir / "combined"]:
        if not search_dir.exists():
            continue
        for path in sorted(search_dir.glob("*.tsv")):
            if path.stat().st_size <= 0:
                continue
            name = path.name.lower()
            if name == "protein.tsv" or name.startswith("combined_protein"):
                tables["protein"] = path
            elif name == "peptide.tsv" or name.startswith("combined_peptide"):
                tables["peptide"] = path
            elif name == "ion.tsv" or name.startswith("combined_ion"):
                tables["precursor"] = path
    return tables


def _ionquant_cache_complete(output_dir: Path, log_path: Path) -> bool:
    tables = _collect_quant_tables(output_dir)
    if not {"protein", "peptide", "precursor"}.issubset(tables):
        return False
    if not log_path.exists() or log_path.stat().st_size <= 0:
        return False
    text = log_path.read_text(encoding="utf-8", errors="replace")
    return "Done!" in text and "exit_code=0" in text


def _run_ionquant_condition(
    ionquant_bin: str,
    run: dict[str, Any],
    condition: str,
    result_root: Path,
    philosopher_row: dict[str, Any],
    *,
    threads: int,
    force: bool,
    timeout_sec: int | None,
) -> dict[str, Any]:
    slug = str(run["slug"])
    run_root = result_root / "runs" / slug / condition
    out_dir = run_root / "ionquant_workspace"
    spec_dir = out_dir / "specdir"
    log_path = result_root / "logs" / f"{slug}.{condition}.ionquant.log"
    if out_dir.exists() and _ionquant_cache_complete(out_dir, log_path) and not force:
        return {
            "file": run["file"],
            "index": run["index"],
            "slug": slug,
            "condition": condition,
            "status": "cached",
            "exit_code": 0,
            "elapsed_sec": 0.0,
            "output_dir": str(out_dir),
            "log": str(log_path),
        }
    if force and out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    mzml = Path(str(philosopher_row.get("spectrum_path") or run[f"{condition}_mzml"]))
    if not mzml.exists():
        mzml = _find_philosopher_spectrum_path(Path(str(philosopher_row.get("work_dir", ""))), {}, run, condition)
    _link_or_copy(mzml, spec_dir / mzml.name, force=force)
    psm = Path(str(philosopher_row.get("psm_tsv") or ""))
    if not psm.exists():
        return {
            "file": run["file"],
            "index": run["index"],
            "slug": slug,
            "condition": condition,
            "status": "missing_psm",
            "exit_code": 1,
            "elapsed_sec": 0.0,
            "output_dir": str(out_dir),
            "log": str(log_path),
        }
    for name in ["psm.tsv", "ion.tsv", "peptide.tsv", "protein.tsv", "protein.fas", "interact-input.pep.xml", "interact.pep.xml", "interact.prot.xml"]:
        source = psm.parent / name
        if source.exists() and source.stat().st_size > 0:
            shutil.copy2(source, out_dir / name)
    cmd = [
        ionquant_bin,
        "--threads",
        str(max(1, int(threads))),
        "--ionmobility",
        "0",
        "--mbr",
        "0",
        "--normalization",
        "0",
        "--site-reports",
        "0",
        "--maxlfq",
        "0",
        "--msstats",
        "1",
        "--specdir",
        str(spec_dir),
        "--psm",
        str(out_dir / "psm.tsv"),
    ]
    code, elapsed = _run(cmd, log_path, cwd=out_dir, timeout_sec=timeout_sec)
    return {
        "file": run["file"],
        "index": run["index"],
        "slug": slug,
        "condition": condition,
        "status": "ok" if code == 0 else "failed",
        "exit_code": code,
        "elapsed_sec": elapsed,
        "output_dir": str(out_dir),
        "log": str(log_path),
    }


def _pick_intensity_col(df: pd.DataFrame) -> str:
    for col in ["philosopher Intensity", "Intensity"]:
        if col in df.columns:
            return col
    matches = [col for col in df.columns if "intensity" in col.lower()]
    if matches:
        return str(matches[0])
    raise ValueError(f"No intensity column in table with columns: {list(df.columns)}")


def _load_ionquant(path: Path, level: str, condition: str) -> pd.DataFrame:
    df = pd.read_csv(path, sep="\t", low_memory=False)
    intensity_col = _pick_intensity_col(df)
    if level == "protein":
        key_cols = [col for col in ["Protein ID", "Protein"] if col in df.columns]
    elif level == "peptide":
        key_cols = [col for col in ["Protein ID", "Peptide", "Peptide Sequence", "Modified Sequence", "Charges", "Charge"] if col in df.columns]
    else:
        key_cols = [col for col in ["Protein ID", "Modified Sequence", "Peptide Sequence", "Charge", "M/Z"] if col in df.columns]
    if not key_cols:
        key_cols = [str(df.columns[0])]
    out = pd.DataFrame(
        {
            "id": df[key_cols].astype(str).agg("|".join, axis=1),
            f"{condition}_quantity": pd.to_numeric(df[intensity_col], errors="coerce").fillna(0.0),
        }
    )
    return out.groupby("id", as_index=False)[f"{condition}_quantity"].sum()


def _compare_ionquant_pair(original_table: Path, reconstructed_table: Path, file_name: str, index: int, level: str, result_root: Path) -> dict[str, Any]:
    original = _load_ionquant(original_table, level, "original")
    reconstructed = _load_ionquant(reconstructed_table, level, "reconstructed")
    merged = original.merge(reconstructed, on="id", how="outer").fillna(0.0)
    original_set = set(merged.loc[merged["original_quantity"] > 0, "id"].astype(str))
    reconstructed_set = set(merged.loc[merged["reconstructed_quantity"] > 0, "id"].astype(str))
    shared = merged[(merged["original_quantity"] > 0) & (merged["reconstructed_quantity"] > 0)].copy()
    x = np.log2(shared["original_quantity"].to_numpy(dtype=float) + 1.0)
    y = np.log2(shared["reconstructed_quantity"].to_numpy(dtype=float) + 1.0)
    abs_delta = np.abs(y - x)
    pearson_r, pearson_p = _pearson(x, y)
    spearman_r, spearman_p = _spearman(x, y)
    slug = f"{int(index):02d}_{_safe_name(file_name)}"
    paired_dir = result_root / "paired_tables"
    paired_dir.mkdir(parents=True, exist_ok=True)
    merged.to_csv(paired_dir / f"{slug}.{level}.ionquant_pairwise.tsv", sep="\t", index=False)
    if not getattr(_compare_ionquant_pair, "skip_plots", False):
        _plot_ionquant_scatter(shared, file_name, level, pearson_r, spearman_r, result_root / "plots" / "scatter" / level / f"{slug}.{level}.ionquant_pairwise_scatter")
    return {
        "index": index,
        "file": file_name,
        "level": level,
        "original_table": str(original_table),
        "reconstructed_table": str(reconstructed_table),
        "original_nonzero_count": len(original_set),
        "reconstructed_nonzero_count": len(reconstructed_set),
        "shared_nonzero_count": len(original_set & reconstructed_set),
        "original_only_count": len(original_set - reconstructed_set),
        "reconstructed_only_count": len(reconstructed_set - original_set),
        "jaccard": _jaccard(original_set, reconstructed_set),
        "shared_log2_pearson": pearson_r,
        "shared_log2_pearson_p": pearson_p,
        "shared_log2_spearman": spearman_r,
        "shared_log2_spearman_p": spearman_p,
        "median_abs_log2_delta": float(np.nanmedian(abs_delta)) if len(abs_delta) else float("nan"),
        "p95_abs_log2_delta": float(np.nanpercentile(abs_delta, 95)) if len(abs_delta) else float("nan"),
        "max_abs_log2_delta": float(np.nanmax(abs_delta)) if len(abs_delta) else float("nan"),
    }


def _plot_ionquant_scatter(
    shared: pd.DataFrame,
    file_name: str,
    level: str,
    pearson_r: float,
    spearman_r: float,
    stem: Path,
) -> None:
    if shared.empty:
        return
    plt = _plt()
    x = np.log2(shared["original_quantity"].to_numpy(dtype=float) + 1.0)
    y = np.log2(shared["reconstructed_quantity"].to_numpy(dtype=float) + 1.0)
    lo = float(np.nanmin([np.nanmin(x), np.nanmin(y)]))
    hi = float(np.nanmax([np.nanmax(x), np.nanmax(y)]))
    pad = max((hi - lo) * 0.04, 0.25)
    fig, ax = plt.subplots(figsize=(6.2, 5.6))
    hb = ax.hexbin(x, y, gridsize=55, mincnt=1, bins="log", cmap="viridis")
    ax.plot([lo - pad, hi + pad], [lo - pad, hi + pad], color="black", linestyle="--", linewidth=1.0)
    ax.set_xlim(lo - pad, hi + pad)
    ax.set_ylim(lo - pad, hi + pad)
    ax.set_xlabel("Original IonQuant log2(quantity + 1)")
    ax.set_ylabel("Reconstructed IonQuant log2(quantity + 1)")
    ax.set_title(f"{_short_label(file_name)} {level}", fontsize=9)
    ax.text(
        0.04,
        0.96,
        f"n = {len(shared)}\nPearson r = {pearson_r:.6f}\nSpearman rho = {spearman_r:.6f}",
        transform=ax.transAxes,
        ha="left",
        va="top",
        fontsize=8,
        bbox={"boxstyle": "round", "facecolor": "white", "alpha": 0.86, "linewidth": 0.5},
    )
    fig.colorbar(hb, ax=ax, label="log10 point density")
    fig.tight_layout()
    _save(fig, stem)


def _plot_dda_summary(summary: pd.DataFrame, out_dir: Path) -> None:
    if summary.empty:
        return
    plt = _plt()
    for level, sub in summary.groupby("level", sort=False):
        sub = sub.sort_values("index").reset_index(drop=True)
        labels = [_short_label(str(v)) for v in sub["file"]]
        x = np.arange(len(sub))
        fig, ax = plt.subplots(figsize=(13.0, 4.9))
        ax.plot(x, sub["jaccard"], marker="o", label="quantified-set Jaccard", color="#526D9D")
        ax.plot(x, sub["shared_log2_pearson"], marker="s", label="shared log2 Pearson", color="#2F5D62")
        ax.plot(x, sub["shared_log2_spearman"], marker="^", label="shared log2 Spearman", color="#B86B25")
        ymin = float(np.nanmin(sub[["jaccard", "shared_log2_pearson", "shared_log2_spearman"]].to_numpy()))
        ax.set_ylim(max(0.0, ymin - 0.02), 1.0005)
        ax.set_ylabel("Agreement")
        ax.set_title(f"MSFragger-Philosopher-IonQuant {level} agreement")
        ax.set_xticks(x)
        ax.set_xticklabels(labels, rotation=38, ha="right", fontsize=8)
        ax.grid(axis="y", alpha=0.25)
        ax.legend(loc="lower left", fontsize=8)
        fig.tight_layout()
        _save(fig, out_dir / f"trackcodec_dda_ionquant_{level}_agreement")

        fig, ax = plt.subplots(figsize=(13.0, 4.9))
        ax.bar(x, sub["p95_abs_log2_delta"], color="#526D9D", width=0.72, label="p95 abs log2 delta")
        ax.scatter(x, sub["median_abs_log2_delta"], color="black", s=24, zorder=3, label="median abs log2 delta")
        ax.set_ylabel("abs(log2 reconstructed - original)")
        ax.set_title(f"MSFragger-Philosopher-IonQuant {level} quantity delta")
        ax.set_xticks(x)
        ax.set_xticklabels(labels, rotation=38, ha="right", fontsize=8)
        ax.grid(axis="y", alpha=0.25)
        ax.legend(fontsize=8)
        fig.tight_layout()
        _save(fig, out_dir / f"trackcodec_dda_ionquant_{level}_abs_log2_delta")


def run_dda(
    pairs: list[dict[str, Any]],
    result_root: Path,
    *,
    target_decoy_fasta: Path,
    msfragger_params_template: Path,
    threads: int,
    dry_run: bool,
    max_files: int,
    skip_msfragger: bool,
    skip_philosopher: bool,
    skip_ionquant: bool,
    force: bool,
    timeout_sec: int | None,
    skip_plots: bool,
) -> dict[str, Any]:
    selected = [row for row in pairs if row["dda_selected"]]
    if max_files > 0:
        selected = selected[:max_files]
    out_root = result_root / "dda_msfragger_ionquant"
    (out_root / "tables").mkdir(parents=True, exist_ok=True)
    (out_root / "plots").mkdir(parents=True, exist_ok=True)
    params_path = out_root / "assets" / "closed_dda_target_decoy_fragger.params"
    if not dry_run:
        _build_msfragger_params(msfragger_params_template, params_path, database=target_decoy_fasta, threads=threads)
    runs = [
        {
            "index": row["index"],
            "file": row["file_name"],
            "slug": f"{int(row['index']):02d}_{_safe_name(str(row['file_name']))}",
            "original_mzml": row["original_path"],
            "reconstructed_mzml": row["reconstructed_path"],
        }
        for row in selected
    ]
    if dry_run:
        rows = []
        for run in runs:
            for condition in ["original", "reconstructed"]:
                rows.append(
                    {
                        "index": run["index"],
                        "file": run["file"],
                        "condition": condition,
                        "input_mzml": run[f"{condition}_mzml"],
                        "planned_msfragger": not skip_msfragger,
                        "planned_philosopher": not skip_philosopher,
                        "planned_ionquant": not skip_ionquant,
                    }
                )
        _write_csv(rows, out_root / "tables" / "trackcodec_dda_pipeline_plan.csv")
        return {"stage": "dda", "dry_run": True, "file_count": len(runs), "params_path": str(params_path)}

    if not target_decoy_fasta.exists():
        raise FileNotFoundError(f"Target-decoy FASTA missing: {target_decoy_fasta}")
    msfragger_bin = _which("msfragger", required=not skip_msfragger)
    philosopher_bin = _which("philosopher", required=not skip_philosopher)
    ionquant_bin = _which("ionquant", required=not skip_ionquant)
    tmp_root = out_root / "tmp"
    ms_rows: list[dict[str, Any]] = []
    ph_rows: list[dict[str, Any]] = []
    iq_rows: list[dict[str, Any]] = []
    for run in runs:
        for condition in ["original", "reconstructed"]:
            if skip_msfragger:
                ms_row = {
                    "file": run["file"],
                    "index": run["index"],
                    "slug": run["slug"],
                    "condition": condition,
                    "status": "skipped",
                    "exit_code": 0,
                    "elapsed_sec": 0.0,
                    "source_mzml": run[f"{condition}_mzml"],
                    "pepxml": "",
                    "peak_picked_for_msfragger": False,
                    "peak_picker_exit_code": "",
                    "peak_picker_elapsed_sec": 0.0,
                    "peak_picker_log": "",
                }
            else:
                ms_row = _run_msfragger_condition(
                    msfragger_bin,
                    run,
                    condition,
                    out_root,
                    params_path,
                    force=force,
                    timeout_sec=timeout_sec,
                )
            ms_rows.append(ms_row)
            if skip_philosopher or not _exit_code_ok(ms_row.get("exit_code")):
                continue
            ph_row = _run_philosopher_condition(
                philosopher_bin,
                run,
                condition,
                out_root,
                target_decoy_fasta,
                tmp_root,
                ms_row,
                force=force,
                timeout_sec=timeout_sec,
            )
            ph_rows.append(ph_row)
            if skip_ionquant or not _exit_code_ok(ph_row.get("exit_code")):
                continue
            iq_row = _run_ionquant_condition(
                ionquant_bin,
                run,
                condition,
                out_root,
                ph_row,
                threads=threads,
                force=force,
                timeout_sec=timeout_sec,
            )
            iq_rows.append(iq_row)

    _write_csv(ms_rows, out_root / "tables" / "trackcodec_dda_msfragger_run_log.csv")
    _write_csv(ph_rows, out_root / "tables" / "trackcodec_dda_philosopher_run_log.csv")
    _write_csv(iq_rows, out_root / "tables" / "trackcodec_dda_ionquant_run_log.csv")
    old_skip = getattr(_compare_ionquant_pair, "skip_plots", False)
    setattr(_compare_ionquant_pair, "skip_plots", bool(skip_plots))
    try:
        summary_rows = _summarize_dda_pairs(runs, out_root)
    finally:
        setattr(_compare_ionquant_pair, "skip_plots", old_skip)
    _write_csv(summary_rows, out_root / "tables" / "trackcodec_dda_ionquant_pairwise_summary.csv")
    if not skip_plots:
        _plot_dda_summary(pd.DataFrame(summary_rows), out_root / "plots" / "summary")
    summary = {
        "stage": "dda",
        "dry_run": False,
        "file_count": len(runs),
        "msfragger_success_count": sum(1 for row in ms_rows if _exit_code_ok(row.get("exit_code"))),
        "msfragger_run_count": len(ms_rows),
        "philosopher_success_count": sum(1 for row in ph_rows if _exit_code_ok(row.get("exit_code"))),
        "philosopher_run_count": len(ph_rows),
        "ionquant_success_count": sum(1 for row in iq_rows if _exit_code_ok(row.get("exit_code"))),
        "ionquant_run_count": len(iq_rows),
        "pairwise_summary_count": len(summary_rows),
        "pairwise_summary": str(out_root / "tables" / "trackcodec_dda_ionquant_pairwise_summary.csv"),
    }
    _write_json(out_root / "summary.json", summary)
    return summary


def _summarize_dda_pairs(runs: list[dict[str, Any]], out_root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for run in runs:
        slug = str(run["slug"])
        original_tables = _collect_quant_tables(out_root / "runs" / slug / "original" / "ionquant_workspace")
        reconstructed_tables = _collect_quant_tables(out_root / "runs" / slug / "reconstructed" / "ionquant_workspace")
        for level in ["protein", "peptide", "precursor"]:
            if level in original_tables and level in reconstructed_tables:
                rows.append(
                    _compare_ionquant_pair(
                        original_tables[level],
                        reconstructed_tables[level],
                        str(run["file"]),
                        int(run["index"]),
                        level,
                        out_root,
                    )
                )
    return rows


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="TrackCodec full downstream validation for current decoded outputs.")
    parser.add_argument("--validation-results", type=Path, default=DEFAULT_VALIDATION_RESULTS)
    parser.add_argument("--result-root", type=Path, default=DEFAULT_RESULT_ROOT)
    parser.add_argument("--stages", nargs="+", default=["manifest"], choices=["manifest", "diann", "dda", "all"])
    parser.add_argument("--dry-run", action="store_true", help="Write manifests and planned rows without launching external pipelines.")
    parser.add_argument("--scope", choices=["human", "reference_full8", "all"], default="human")
    parser.add_argument("--include-aif-diann", action="store_true")
    parser.add_argument("--only", action="append", default=[], help="Restrict file names by substring; repeatable.")
    parser.add_argument("--force-diann-selected", action="store_true", help="Force DIA-NN selection for the filtered input rows.")
    parser.add_argument("--force-dda-selected", action="store_true", help="Force DDA selection for the filtered input rows.")
    parser.add_argument("--max-diann-files", type=int, default=0)
    parser.add_argument("--max-dda-files", type=int, default=0)
    parser.add_argument("--threads", type=int, default=8)
    parser.add_argument("--diann-threads", type=int, default=0)
    parser.add_argument("--dda-threads", type=int, default=0)
    parser.add_argument("--fasta", type=Path, default=DEFAULT_FASTA)
    parser.add_argument("--target-decoy-fasta", type=Path, default=DEFAULT_TARGET_DECOY_FASTA)
    parser.add_argument("--predicted-lib", type=Path, default=None)
    parser.add_argument("--msfragger-params", type=Path, default=DEFAULT_MSFRAGGER_PARAMS)
    parser.add_argument("--rewrite-original-diann-input", action="store_true")
    parser.add_argument("--rewrite-reconstructed-diann-input", action="store_true")
    parser.add_argument("--skip-msfragger", action="store_true")
    parser.add_argument("--skip-philosopher", action="store_true")
    parser.add_argument("--skip-ionquant", action="store_true")
    parser.add_argument("--skip-plots", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--timeout-sec", type=int, default=0)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    stages = list(args.stages)
    if "all" in stages:
        stages = ["manifest", "diann", "dda"]
    if args.dry_run and "manifest" not in stages:
        stages = ["manifest", *stages]

    args.result_root.mkdir(parents=True, exist_ok=True)
    (args.result_root / "tables").mkdir(parents=True, exist_ok=True)
    (args.result_root / "logs").mkdir(parents=True, exist_ok=True)
    pairs = discover_pairs(args.validation_results, scope=args.scope, include_aif_diann=args.include_aif_diann)
    pairs = _filter_pairs(pairs, args.only)
    if args.force_diann_selected:
        for row in pairs:
            row["diann_selected"] = bool(row["original_exists"] and row["reconstructed_exists"])
    if args.force_dda_selected:
        for row in pairs:
            row["dda_selected"] = bool(row["original_exists"] and row["reconstructed_exists"])
    tools = {
        "diann": _which("diann"),
        "msfragger": _which("msfragger"),
        "philosopher": _which("philosopher"),
        "ionquant": _which("ionquant"),
        "FileFilter": _which("FileFilter"),
    }
    assets = {
        "fasta": args.fasta,
        "target_decoy_fasta": args.target_decoy_fasta,
        "msfragger_params_template": args.msfragger_params,
    }
    results: dict[str, Any] = {
        "result_root": str(args.result_root),
        "stages": stages,
        "dry_run": bool(args.dry_run),
        "scope": args.scope,
    }
    if "manifest" in stages:
        results["manifest"] = write_manifest(pairs, args.result_root, assets=assets, tools=tools)
    if "diann" in stages:
        results["diann"] = run_diann(
            pairs,
            args.result_root,
            fasta=args.fasta,
            predicted_lib=args.predicted_lib,
            threads=int(args.diann_threads or args.threads),
            rewrite_original=bool(args.rewrite_original_diann_input),
            rewrite_reconstructed=bool(args.rewrite_reconstructed_diann_input),
            dry_run=bool(args.dry_run),
            max_files=int(args.max_diann_files),
            skip_plots=bool(args.skip_plots),
        )
    if "dda" in stages:
        results["dda"] = run_dda(
            pairs,
            args.result_root,
            target_decoy_fasta=args.target_decoy_fasta,
            msfragger_params_template=args.msfragger_params,
            threads=int(args.dda_threads or args.threads),
            dry_run=bool(args.dry_run),
            max_files=int(args.max_dda_files),
            skip_msfragger=bool(args.skip_msfragger),
            skip_philosopher=bool(args.skip_philosopher),
            skip_ionquant=bool(args.skip_ionquant),
            force=bool(args.force),
            timeout_sec=int(args.timeout_sec) if int(args.timeout_sec) > 0 else None,
            skip_plots=bool(args.skip_plots),
        )
    _write_json(args.result_root / "full_downstream_summary.json", results)
    print(json.dumps(_json_safe(results), indent=2), flush=True)


if __name__ == "__main__":
    main()
