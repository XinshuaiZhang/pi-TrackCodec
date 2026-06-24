"""Standalone plotting utility for TrackCodec benchmark CSV artifacts."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np


STRICT_COLOR = '#4C78A8'
NEAR_COLOR = '#F58518'


def read_csv_rows(path: str | Path):
    with open(path, newline='') as handle:
        return list(csv.DictReader(handle))


def _fallback_color(row):
    family = row.get('family', '')
    return STRICT_COLOR if family in {'strict', 'strict_q6'} else row.get('color_hex') or NEAR_COLOR


def plot_compression_bar(bar_rows, output_png: str | Path, title='Mean Compression Ratio By Mode'):
    if not bar_rows:
        raise ValueError('compression bar rows are empty')
    labels = [row['display_name'] for row in bar_rows]
    values = [float(row['mean_cr']) for row in bar_rows]
    colors = [row.get('color_hex') or _fallback_color(row) for row in bar_rows]
    annotations = [row.get('bar_annotation', f"{val:.2f}x") for row, val in zip(bar_rows, values)]

    title_fs = 20
    label_fs = 16
    tick_fs = 13
    legend_fs = 12
    annot_fs = 13

    fig, ax = plt.subplots(figsize=(14, 11))
    bars = ax.bar(range(len(labels)), values, color=colors)
    ax.set_title(title, fontsize=title_fs)
    ax.set_ylabel('Compression Ratio', fontsize=label_fs)
    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels, rotation=68, ha='right', fontsize=tick_fs)
    ax.tick_params(axis='y', labelsize=tick_fs)
    ax.grid(axis='y', alpha=0.3)

    families = {row.get('family', '') for row in bar_rows}
    handles = []
    if 'strict' in families:
        handles.append(plt.Rectangle((0, 0), 1, 1, color=STRICT_COLOR, label='Strict lossless'))
    if 'strict_q6' in families:
        handles.append(plt.Rectangle((0, 0), 1, 1, color=STRICT_COLOR, label='Exact intensity + q6 m/z'))
    if 'near' in families or 'near_lossless' in ''.join(row.get('fidelity_class', '') for row in bar_rows):
        handles.append(plt.Rectangle((0, 0), 1, 1, color=NEAR_COLOR, label='Near-lossless'))
    if handles:
        ax.legend(handles=handles, fontsize=legend_fs, loc='upper right')

    max_val = max(values) if values else 1.0
    ax.set_ylim(0, max_val * 1.18 if max_val > 0 else 1.0)
    for bar, text in zip(bars, annotations):
        ax.text(
            bar.get_x() + bar.get_width() / 2.0,
            bar.get_height() + max_val * 0.015,
            text,
            ha='center',
            va='bottom',
            fontsize=annot_fs,
        )
    plt.tight_layout()
    plt.savefig(output_png, dpi=150)
    plt.close(fig)


def plot_compression_bar_custom(
    bar_rows,
    output_png: str | Path,
    *,
    title='Mean Compression Ratio By Mode',
    figsize=(14, 11),
    legend_loc='upper right',
    legend_bbox=None,
    annotation_fontsize=13,
    title_fontsize=20,
    label_fontsize=16,
    tick_fontsize=13,
    legend_fontsize=12,
):
    if not bar_rows:
        raise ValueError('compression bar rows are empty')
    labels = [row['display_name'] for row in bar_rows]
    values = [float(row['mean_cr']) for row in bar_rows]
    colors = [row.get('color_hex') or _fallback_color(row) for row in bar_rows]
    annotations = [row.get('bar_annotation', f"{val:.2f}x") for row, val in zip(bar_rows, values)]

    fig, ax = plt.subplots(figsize=figsize)
    bars = ax.bar(range(len(labels)), values, color=colors)
    ax.set_title(title, fontsize=title_fontsize)
    ax.set_ylabel('Compression Ratio', fontsize=label_fontsize)
    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels, rotation=68, ha='right', fontsize=tick_fontsize)
    ax.tick_params(axis='y', labelsize=tick_fontsize)
    ax.grid(axis='y', alpha=0.3)

    families = {row.get('family', '') for row in bar_rows}
    handles = []
    if 'strict' in families:
        handles.append(plt.Rectangle((0, 0), 1, 1, color=STRICT_COLOR, label='Strict lossless'))
    if 'strict_q6' in families:
        handles.append(plt.Rectangle((0, 0), 1, 1, color=STRICT_COLOR, label='Exact intensity + q6 m/z'))
    if 'near' in families or 'near_lossless' in ''.join(row.get('fidelity_class', '') for row in bar_rows):
        handles.append(plt.Rectangle((0, 0), 1, 1, color=NEAR_COLOR, label='Near-lossless'))
    if handles:
        legend_kwargs = {'fontsize': legend_fontsize, 'loc': legend_loc}
        if legend_bbox is not None:
            legend_kwargs['bbox_to_anchor'] = legend_bbox
        ax.legend(handles=handles, **legend_kwargs)

    max_val = max(values) if values else 1.0
    multi_line = max(text.count('\n') + 1 for text in annotations) if annotations else 1
    top_scale = 1.18 + 0.08 * max(multi_line - 1, 0)
    ax.set_ylim(0, max_val * top_scale if max_val > 0 else 1.0)
    for bar, text in zip(bars, annotations):
        ax.text(
            bar.get_x() + bar.get_width() / 2.0,
            bar.get_height() + max_val * 0.015,
            text,
            ha='center',
            va='bottom',
            fontsize=annotation_fontsize,
        )
    plt.tight_layout()
    plt.savefig(output_png, dpi=150)
    plt.close(fig)


def plot_compression_line(line_rows, output_png: str | Path, title='Per-file Compression Ratio For Top Modes'):
    if not line_rows:
        raise ValueError('compression line rows are empty')
    order = []
    by_label = {}
    file_names = []
    seen_files = set()
    for row in line_rows:
        label = row['label']
        if label not in by_label:
            by_label[label] = {'display_name': row['display_name'], 'points': []}
            order.append(label)
        by_label[label]['points'].append(row)
        file_label = row['file_label']
        if file_label not in seen_files:
            seen_files.add(file_label)
            file_names.append(file_label)

    title_fs = 20
    label_fs = 16
    tick_fs = 13
    legend_fs = 11
    annot_fs = 11

    fig, ax = plt.subplots(figsize=(14, 10))
    colors = plt.cm.tab10(np.linspace(0, 1, max(len(order), 3)))
    for idx, label in enumerate(order):
        point_map = {row['file_label']: float(row['compression_ratio']) for row in by_label[label]['points']}
        y = [point_map[name] for name in file_names]
        ax.plot(
            range(len(file_names)),
            y,
            marker='o',
            linewidth=2,
            color=colors[idx],
            label=by_label[label]['display_name'],
        )
        for x, val in enumerate(y):
            ax.text(
                x,
                val + max(0.015 * max(y), 0.02),
                f'{val:.2f}x',
                fontsize=annot_fs,
                ha='center',
                va='bottom',
                color=colors[idx],
            )
    ax.set_title(title, fontsize=title_fs)
    ax.set_ylabel('Compression Ratio', fontsize=label_fs)
    ax.set_xticks(range(len(file_names)))
    ax.set_xticklabels(file_names, rotation=45, ha='right', fontsize=tick_fs)
    ax.tick_params(axis='y', labelsize=tick_fs)
    ax.grid(alpha=0.3)
    ax.legend(fontsize=legend_fs)
    plt.tight_layout()
    plt.savefig(output_png, dpi=150)
    plt.close(fig)


def plot_compression_line_custom(
    line_rows,
    output_png: str | Path,
    *,
    title='Per-file Compression Ratio For Top Modes',
    figsize=(14, 10),
    legend_loc='best',
    legend_bbox=None,
    annotation_fontsize=11,
    title_fontsize=20,
    label_fontsize=16,
    tick_fontsize=13,
    legend_fontsize=11,
):
    if not line_rows:
        raise ValueError('compression line rows are empty')
    order = []
    by_label = {}
    file_names = []
    seen_files = set()
    for row in line_rows:
        label = row['label']
        if label not in by_label:
            by_label[label] = {'display_name': row['display_name'], 'points': []}
            order.append(label)
        by_label[label]['points'].append(row)
        file_label = row['file_label']
        if file_label not in seen_files:
            seen_files.add(file_label)
            file_names.append(file_label)

    fig, ax = plt.subplots(figsize=figsize)
    colors = plt.cm.tab10(np.linspace(0, 1, max(len(order), 3)))
    for idx, label in enumerate(order):
        point_map = {row['file_label']: float(row['compression_ratio']) for row in by_label[label]['points']}
        y = [point_map[name] for name in file_names]
        ax.plot(
            range(len(file_names)),
            y,
            marker='o',
            linewidth=2,
            color=colors[idx],
            label=by_label[label]['display_name'],
        )
        for x, val in enumerate(y):
            ax.text(
                x,
                val + max(0.015 * max(y), 0.02),
                f'{val:.2f}x',
                fontsize=annotation_fontsize,
                ha='center',
                va='bottom',
                color=colors[idx],
            )
    ax.set_title(title, fontsize=title_fontsize)
    ax.set_ylabel('Compression Ratio', fontsize=label_fontsize)
    ax.set_xticks(range(len(file_names)))
    ax.set_xticklabels(file_names, rotation=45, ha='right', fontsize=tick_fontsize)
    ax.tick_params(axis='y', labelsize=tick_fontsize)
    ax.grid(alpha=0.3)
    legend_kwargs = {'fontsize': legend_fontsize, 'loc': legend_loc}
    if legend_bbox is not None:
        legend_kwargs['bbox_to_anchor'] = legend_bbox
    ax.legend(**legend_kwargs)
    plt.tight_layout()
    plt.savefig(output_png, dpi=150, bbox_inches='tight')
    plt.close(fig)


def plot_speed_bar(speed_rows, output_png: str | Path, title_encode='Mean Encode Time By Mode', title_decode='Mean Decode Time By Mode'):
    if not speed_rows:
        raise ValueError('speed rows are empty')
    labels = [row['display_name'] for row in speed_rows]
    def _optional_float(value):
        if value is None or value == '':
            return None
        return float(value)
    encode_raw = [_optional_float(row.get('mean_encode_time_s')) for row in speed_rows]
    decode_raw = [_optional_float(row.get('mean_decode_time_s')) for row in speed_rows]
    encode_values = [0.0 if value is None else value for value in encode_raw]
    decode_values = [0.0 if value is None else value for value in decode_raw]
    colors = [row.get('color_hex') or _fallback_color(row) for row in speed_rows]

    title_fs = 19
    label_fs = 15
    tick_fs = 12
    annot_fs = 10

    x = np.arange(len(labels))
    fig, axes = plt.subplots(2, 1, figsize=(18, 14), sharex=True)
    axes[0].bar(x, encode_values, color=colors)
    axes[0].set_title(title_encode, fontsize=title_fs)
    axes[0].set_ylabel('Seconds', fontsize=label_fs)
    axes[0].tick_params(axis='y', labelsize=tick_fs)
    axes[0].grid(axis='y', alpha=0.3)
    max_encode = max(encode_values) if encode_values else 0.0
    for idx, val in enumerate(encode_values):
        label = 'NA' if encode_raw[idx] is None else f'{val:.2f}'
        axes[0].text(idx, val + max_encode * 0.015 if max_encode else val, label, ha='center', va='bottom', fontsize=annot_fs)

    axes[1].bar(x, decode_values, color=colors)
    axes[1].set_title(title_decode, fontsize=title_fs)
    axes[1].set_ylabel('Seconds', fontsize=label_fs)
    axes[1].set_xticks(x)
    axes[1].set_xticklabels(labels, rotation=68, ha='right', fontsize=tick_fs)
    axes[1].tick_params(axis='y', labelsize=tick_fs)
    axes[1].grid(axis='y', alpha=0.3)
    max_decode = max(decode_values) if decode_values else 0.0
    for idx, val in enumerate(decode_values):
        label = 'NA' if decode_raw[idx] is None else f'{val:.2f}'
        axes[1].text(idx, val + max_decode * 0.03 if max_decode else val, label, ha='center', va='bottom', fontsize=annot_fs)
    plt.tight_layout()
    plt.savefig(output_png, dpi=150)
    plt.close(fig)


def derive_output(path: str | Path, suffix: str):
    path = Path(path)
    stem = path.stem
    for extra in ('_compression_bar_data', '_compression_line_data', '_speed_bar_data'):
        if stem.endswith(extra):
            stem = stem[: -len(extra)]
            break
    return path.with_name(f'{stem}_{suffix}.png')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--bar-csv', required=True)
    parser.add_argument('--line-csv', required=True)
    parser.add_argument('--speed-csv')
    parser.add_argument('--bar-out')
    parser.add_argument('--line-out')
    parser.add_argument('--speed-out')
    parser.add_argument('--bar-title', default='Mean Compression Ratio By Mode')
    parser.add_argument('--line-title', default='Per-file Compression Ratio For Top Modes')
    parser.add_argument('--encode-title', default='Mean Encode Time By Mode')
    parser.add_argument('--decode-title', default='Mean Decode Time By Mode')
    parser.add_argument('--bar-width', type=float)
    parser.add_argument('--bar-height', type=float)
    parser.add_argument('--line-width', type=float)
    parser.add_argument('--line-height', type=float)
    parser.add_argument('--bar-legend-loc')
    parser.add_argument('--line-legend-loc')
    parser.add_argument('--bar-legend-bbox-x', type=float)
    parser.add_argument('--bar-legend-bbox-y', type=float)
    parser.add_argument('--line-legend-bbox-x', type=float)
    parser.add_argument('--line-legend-bbox-y', type=float)
    parser.add_argument('--bar-annot-fontsize', type=float)
    parser.add_argument('--line-annot-fontsize', type=float)
    args = parser.parse_args()

    bar_out = args.bar_out or derive_output(args.bar_csv, 'compression_comparison')
    line_out = args.line_out or derive_output(args.line_csv, 'compression_line')

    bar_rows = read_csv_rows(args.bar_csv)
    line_rows = read_csv_rows(args.line_csv)
    bar_figsize = (
        args.bar_width if args.bar_width is not None else 14,
        args.bar_height if args.bar_height is not None else 11,
    )
    line_figsize = (
        args.line_width if args.line_width is not None else 14,
        args.line_height if args.line_height is not None else 10,
    )
    bar_legend_bbox = None
    if args.bar_legend_bbox_x is not None and args.bar_legend_bbox_y is not None:
        bar_legend_bbox = (args.bar_legend_bbox_x, args.bar_legend_bbox_y)
    line_legend_bbox = None
    if args.line_legend_bbox_x is not None and args.line_legend_bbox_y is not None:
        line_legend_bbox = (args.line_legend_bbox_x, args.line_legend_bbox_y)

    plot_compression_bar_custom(
        bar_rows,
        bar_out,
        title=args.bar_title,
        figsize=bar_figsize,
        legend_loc=args.bar_legend_loc or 'upper right',
        legend_bbox=bar_legend_bbox,
        annotation_fontsize=args.bar_annot_fontsize or 13,
    )
    plot_compression_line_custom(
        line_rows,
        line_out,
        title=args.line_title,
        figsize=line_figsize,
        legend_loc=args.line_legend_loc or 'best',
        legend_bbox=line_legend_bbox,
        annotation_fontsize=args.line_annot_fontsize or 11,
    )

    speed_out = None
    if args.speed_csv:
        speed_out = args.speed_out or derive_output(args.speed_csv, 'speed_bar')
        plot_speed_bar(read_csv_rows(args.speed_csv), speed_out, title_encode=args.encode_title, title_decode=args.decode_title)

    outputs = {
        'compression_bar_png': str(bar_out),
        'compression_line_png': str(line_out),
    }
    if speed_out:
        outputs['speed_bar_png'] = str(speed_out)
    print(outputs)


if __name__ == '__main__':
    main()
