"""
Interactive HTML plotting for FIO benchmark results.
Generates interactive Plotly bar charts for bandwidth, IOPS, and latency comparisons.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from collections import defaultdict

import plotly.graph_objects as go
from plotly.subplots import make_subplots

from . import fio_parser
from .fio_parser import FIOData


@dataclass
class TraceInfo:
    """Information about a trace for the checkbox selector."""

    trace_id: str  # Unique identifier: "{timestamp}_{config}_{job}"
    timestamp: str  # Raw timestamp string
    config: str  # Config name (e.g., "snp-large")
    job: str  # Job name (e.g., "libaio")
    display_name: str  # Human-readable display name
    is_latest: bool  # Whether this is the latest run for this (config, job)
    bw_trace_indices: List[int] = field(default_factory=list)
    iops_trace_indices: List[int] = field(default_factory=list)
    lat_trace_indices: List[int] = field(default_factory=list)


def generate_html_with_selector(
    fig_bw: go.Figure,
    fig_iops: go.Figure,
    fig_lat: go.Figure,
    trace_infos: List[TraceInfo],
) -> str:
    """Generate HTML with collapsible selector panel organized by config and job.

    Args:
        fig_bw: Bandwidth figure with all traces
        fig_iops: IOPS figure with all traces
        fig_lat: Latency figure with all traces
        trace_infos: List of TraceInfo objects describing each selectable group

    Returns:
        Complete HTML string with selector panel and charts
    """
    # Group trace_infos by config, then by job
    # Build: {config: {job: [(idx, TraceInfo), ...]}}
    grouped: Dict[str, Dict[str, List[Tuple[int, TraceInfo]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for i, info in enumerate(trace_infos):
        grouped[info.config][info.job].append((i, info))

    # Generate collapsible HTML structure
    selector_html = []
    for config in sorted(grouped.keys()):
        jobs = grouped[config]
        # Count total selected for config
        config_selected = sum(
            1
            for job_traces in jobs.values()
            for (_, info) in job_traces
            if info.is_latest
        )
        config_id = config.replace("-", "_")

        selector_html.append(
            f'<details class="config-section">'
            f'<summary class="config-header">{config} '
            f'<span class="count" id="count-{config_id}">({config_selected} selected)</span></summary>'
        )

        for job in sorted(jobs.keys()):
            traces = jobs[job]
            # Count selected for this job
            job_selected = sum(1 for (_, info) in traces if info.is_latest)
            job_id = f"{config_id}_{job.replace('-', '_')}"

            selector_html.append(
                f'<details class="job-section">'
                f'<summary class="job-header">{job} '
                f'<span class="count" id="count-{job_id}">({job_selected} selected)</span></summary>'
                f'<div class="timestamp-list">'
            )

            # Sort traces by timestamp descending (newest first)
            sorted_traces = sorted(traces, key=lambda x: x[1].timestamp, reverse=True)

            for idx, info in sorted_traces:
                checked = "checked" if info.is_latest else ""
                latest_tag = " [latest]" if info.is_latest else ""
                css_class = (
                    "trace-checkbox trace-latest"
                    if info.is_latest
                    else "trace-checkbox"
                )
                # Format timestamp for display (just time portion)
                display_time = fio_parser.format_timestamp(info.timestamp)
                selector_html.append(
                    f'<div class="{css_class}">'
                    f'<input type="checkbox" id="trace-{idx}" {checked} '
                    f'data-config="{config_id}" data-job="{job_id}" '
                    f'onchange="toggleTrace({idx})">'
                    f'<label for="trace-{idx}">{display_time}{latest_tag}</label>'
                    f"</div>"
                )

            selector_html.append("</div></details>")

        selector_html.append("</details>")

    # Generate trace mapping JavaScript
    trace_map_entries = []
    for i, info in enumerate(trace_infos):
        bw_indices = json.dumps(info.bw_trace_indices)
        iops_indices = json.dumps(info.iops_trace_indices)
        lat_indices = json.dumps(info.lat_trace_indices)
        config_id = info.config.replace("-", "_")
        job_id = f"{config_id}_{info.job.replace('-', '_')}"
        trace_map_entries.append(
            f"    {i}: {{bw: {bw_indices}, iops: {iops_indices}, lat: {lat_indices}, "
            f'config: "{config_id}", job: "{job_id}"}}'
        )
    trace_map_js = "const TRACE_MAP = {\n" + ",\n".join(trace_map_entries) + "\n};"

    # Convert figures to HTML divs (without full HTML wrapper)
    bw_html = fig_bw.to_html(full_html=False, include_plotlyjs=False, div_id="bw-chart")
    iops_html = fig_iops.to_html(
        full_html=False, include_plotlyjs=False, div_id="iops-chart"
    )
    lat_html = fig_lat.to_html(
        full_html=False, include_plotlyjs=False, div_id="lat-chart"
    )

    html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>FIO Benchmark Results</title>
    <script src="https://cdn.plot.ly/plotly-2.27.0.min.js"></script>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            margin: 20px;
            background: #fafafa;
        }}
        .selector-panel {{
            padding: 15px 20px;
            background: #fff;
            border-radius: 8px;
            margin-bottom: 20px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.1);
        }}
        .selector-header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 10px;
        }}
        .selector-panel h3 {{
            margin: 0;
            font-size: 12px;
            color: #333;
        }}
        .toggle-btn {{
            font-size: 10px;
            padding: 3px 8px;
            border: 1px solid #ccc;
            border-radius: 4px;
            background: #f5f5f5;
            cursor: pointer;
            color: #555;
        }}
        .toggle-btn:hover {{
            background: #e8e8e8;
        }}
        .config-grid {{
            display: flex;
            flex-wrap: wrap;
            gap: 15px 25px;
        }}
        .config-section {{
            min-width: 180px;
        }}
        .config-header {{
            font-size: 11px;
            font-weight: 600;
            color: #333;
            cursor: pointer;
            padding: 4px 0;
            list-style: none;
        }}
        .config-header::-webkit-details-marker {{
            display: none;
        }}
        .config-header::before {{
            content: "▶ ";
            font-size: 8px;
            margin-right: 4px;
        }}
        details[open] > .config-header::before {{
            content: "▼ ";
        }}
        .config-header .count {{
            font-weight: 400;
            color: #666;
            font-size: 10px;
        }}
        .job-section {{
            margin-left: 12px;
            margin-bottom: 2px;
        }}
        .job-header {{
            font-size: 10px;
            font-weight: 500;
            color: #555;
            cursor: pointer;
            padding: 2px 0;
            list-style: none;
        }}
        .job-header::-webkit-details-marker {{
            display: none;
        }}
        .job-header::before {{
            content: "▶ ";
            font-size: 7px;
            margin-right: 4px;
        }}
        details[open] > .job-header::before {{
            content: "▼ ";
        }}
        .job-header .count {{
            font-weight: 400;
            color: #888;
            font-size: 9px;
        }}
        .timestamp-list {{
            margin-left: 12px;
            padding: 2px 0;
        }}
        .trace-checkbox {{
            padding: 1px 0;
        }}
        .trace-checkbox label {{
            cursor: pointer;
            font-size: 10px;
            color: #666;
        }}
        .trace-checkbox input {{
            margin-right: 5px;
            transform: scale(0.85);
        }}
        .trace-latest label {{
            font-weight: 600;
            color: #333;
        }}
        .chart-container {{
            background: #fff;
            border-radius: 8px;
            margin-bottom: 20px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.1);
            padding: 10px;
        }}
    </style>
</head>
<body>
    <div class="selector-panel">
        <div class="selector-header">
            <h3>Select benchmark runs to display:</h3>
            <button class="toggle-btn" onclick="toggleAllSections()">Expand All</button>
        </div>
        <div class="config-grid">
            {"".join(selector_html)}
        </div>
    </div>
    <div class="chart-container">
        {bw_html}
    </div>
    <div class="chart-container">
        {iops_html}
    </div>
    <div class="chart-container">
        {lat_html}
    </div>
    <script>
        {trace_map_js}

        function toggleTrace(idx) {{
            const checkbox = document.getElementById('trace-' + idx);
            const checked = checkbox.checked;
            const visibility = checked ? true : 'legendonly';
            const mapping = TRACE_MAP[idx];

            if (mapping.bw.length > 0) {{
                Plotly.restyle('bw-chart', {{visible: visibility}}, mapping.bw);
            }}
            if (mapping.iops.length > 0) {{
                Plotly.restyle('iops-chart', {{visible: visibility}}, mapping.iops);
            }}
            if (mapping.lat.length > 0) {{
                Plotly.restyle('lat-chart', {{visible: visibility}}, mapping.lat);
            }}

            // Update counts
            updateCounts(mapping.config, mapping.job);
        }}

        function updateCounts(configId, jobId) {{
            // Count selected in job
            const jobCheckboxes = document.querySelectorAll(`input[data-job="${{jobId}}"]`);
            const jobSelected = Array.from(jobCheckboxes).filter(cb => cb.checked).length;
            const jobCountEl = document.getElementById('count-' + jobId);
            if (jobCountEl) {{
                jobCountEl.textContent = `(${{jobSelected}} selected)`;
            }}

            // Count selected in config
            const configCheckboxes = document.querySelectorAll(`input[data-config="${{configId}}"]`);
            const configSelected = Array.from(configCheckboxes).filter(cb => cb.checked).length;
            const configCountEl = document.getElementById('count-' + configId);
            if (configCountEl) {{
                configCountEl.textContent = `(${{configSelected}} selected)`;
            }}
        }}

        let allExpanded = false;
        function toggleAllSections() {{
            const details = document.querySelectorAll('.selector-panel details');
            const btn = document.querySelector('.toggle-btn');
            allExpanded = !allExpanded;
            details.forEach(d => d.open = allExpanded);
            btn.textContent = allExpanded ? 'Collapse All' : 'Expand All';
        }}
    </script>
</body>
</html>"""

    return html


def create_fio_plot(
    job_name: str,
    config_files: Dict[str, Path],
    output_file: Path,
    size_filter: Optional[str] = None,
) -> None:
    """Create interactive HTML plot for a single FIO job.

    Generates one HTML page with 3 separate figures (bandwidth, IOPS, latency).

    Args:
        job_name: Name of the FIO job (e.g., "libaio", "spdk")
        config_files: Dict mapping config names to file paths
        output_file: Output HTML file path
        size_filter: Optional size to filter configs (e.g., "small", "medium")
    """
    # Parse all FIO data
    parsed_data: Dict[str, Dict[str, FIOData]] = {}

    for config_name, filepath in config_files.items():
        # Apply size filter if specified
        if size_filter is not None:
            if f"-{size_filter}" not in config_name:
                continue

        try:
            job_data = fio_parser.parse_fio_file(filepath)
            parsed_data[config_name] = job_data
        except Exception as e:
            print(f"Warning: Failed to parse {filepath}: {e}")
            continue

    if not parsed_data:
        print(f"No data found for job {job_name} with size filter {size_filter}")
        return

    # Wrap single job data in multi-job structure for unified functions
    all_jobs = {job_name: parsed_data}

    # Create three separate figures
    html_parts = []

    # Figure 1: Bandwidth Comparison
    fig_bw = create_bandwidth_figure(all_jobs, f"Bandwidth Comparison - {job_name}")
    html_parts.append(go.Figure(fig_bw).to_html(full_html=True, include_plotlyjs="cdn"))

    # Figure 2: IOPS Comparison
    fig_iops = create_iops_figure(all_jobs, f"IOPS Comparison - {job_name}")
    html_parts.append(
        go.Figure(fig_iops).to_html(full_html=False, include_plotlyjs=False)
    )

    # Figure 3: Latency Comparison
    fig_lat = create_latency_figure(all_jobs, f"Latency Comparison - {job_name}")
    html_parts.append(
        go.Figure(fig_lat).to_html(full_html=False, include_plotlyjs=False)
    )

    # Combine HTML parts
    combined_html = "\n".join(html_parts)

    # Write to file
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(combined_html)
    print(f"Created {output_file}")


def create_fio_comparison_plot(
    all_jobs_files: Dict[str, Dict[str, Path]],
    output_file: Path,
    size_filter: Optional[str] = None,
) -> None:
    """Create comparison plot for multiple FIO jobs.

    Args:
        all_jobs_files: Dict mapping {job_name: {config_name: Path}}
        output_file: Output HTML file path
        size_filter: Optional size to filter configs
    """
    # Parse data for all jobs
    all_jobs: Dict[str, Dict[str, Dict[str, FIOData]]] = {}

    for job_name, config_files in all_jobs_files.items():
        all_jobs[job_name] = {}
        for config_name, filepath in config_files.items():
            if size_filter is not None and f"-{size_filter}" not in config_name:
                continue
            try:
                all_jobs[job_name][config_name] = fio_parser.parse_fio_file(filepath)
            except Exception as e:
                print(f"Warning: Failed to parse {filepath}: {e}")

    # Filter out empty jobs
    all_jobs = {k: v for k, v in all_jobs.items() if v}

    if not all_jobs:
        print(f"No data found for comparison with size filter {size_filter}")
        return

    # Create title
    job_names = sorted(all_jobs.keys())
    if len(job_names) <= 3:
        title = " vs ".join(job_names)
    else:
        title = f"Multi-Job Comparison ({len(job_names)} jobs)"

    # Create three separate figures
    html_parts = []

    # Figure 1: Bandwidth
    fig_bw = create_bandwidth_figure(all_jobs, f"Bandwidth Comparison - {title}")
    html_parts.append(go.Figure(fig_bw).to_html(full_html=True, include_plotlyjs="cdn"))

    # Figure 2: IOPS
    fig_iops = create_iops_figure(all_jobs, f"IOPS Comparison - {title}")
    html_parts.append(
        go.Figure(fig_iops).to_html(full_html=False, include_plotlyjs=False)
    )

    # Figure 3: Latency
    fig_lat = create_latency_figure(all_jobs, f"Latency Comparison - {title}")
    html_parts.append(
        go.Figure(fig_lat).to_html(full_html=False, include_plotlyjs=False)
    )

    # Combine HTML
    combined_html = "\n".join(html_parts)

    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(combined_html)
    print(f"Created {output_file}")


def create_bandwidth_figure(
    all_jobs: Dict[str, Dict[str, Dict[str, FIOData]]],
    title: str = "Bandwidth Comparison",
) -> go.Figure:
    """Create bandwidth comparison figure for jobs and setups.

    Args:
        all_jobs: Nested dict {job_name: {config_name: {test_name: fio_parser.FIOData}}}
        title: Figure title

    Returns:
        Plotly figure with all jobs and setups
    """
    fig = go.Figure()
    all_y_values = []

    bw_read_job = "bw read"
    bw_write_job = "bw write"

    # Get all unique configs across all jobs
    all_configs = set()
    for job_data in all_jobs.values():
        all_configs.update(job_data.keys())
    sorted_configs = sorted(all_configs)

    # Add traces: for each config, for each job
    for config in sorted_configs:
        legend_group, legend_title = fio_parser.get_legend_group_info(config)
        base_color = fio_parser.get_color_for_config(config)

        for job_name in sorted(all_jobs.keys()):
            if config not in all_jobs[job_name]:
                continue

            job_data = all_jobs[job_name][config]
            hatch_pattern = fio_parser.get_job_hatch_pattern(job_name)
            adjusted_color = fio_parser.adjust_color_for_filesystem(
                base_color, job_name
            )

            x_vals = []
            y_vals = []
            text_vals = []

            if bw_read_job in job_data:
                read_bw = job_data[bw_read_job].read_bw_mean / 1_000_000
                read_min = job_data[bw_read_job].read_bw_min / 1_000_000
                read_max = job_data[bw_read_job].read_bw_max / 1_000_000
                read_stddev = job_data[bw_read_job].read_bw_stddev / 1_000_000
                x_vals.append("Read")
                y_vals.append(read_bw)
                all_y_values.append(read_bw)
                text_vals.append(
                    f"{read_bw:.2f}<br>min: {read_min:.2f}<br>max: {read_max:.2f}<br>σ: {read_stddev:.2f}"
                )

            if bw_write_job in job_data:
                write_bw = job_data[bw_write_job].write_bw_mean / 1_000_000
                write_min = job_data[bw_write_job].write_bw_min / 1_000_000
                write_max = job_data[bw_write_job].write_bw_max / 1_000_000
                write_stddev = job_data[bw_write_job].write_bw_stddev / 1_000_000
                x_vals.append("Write")
                y_vals.append(write_bw)
                all_y_values.append(write_bw)
                text_vals.append(
                    f"{write_bw:.2f}<br>min: {write_min:.2f}<br>max: {write_max:.2f}<br>σ: {write_stddev:.2f}"
                )

            if x_vals:
                fig.add_trace(
                    go.Bar(
                        name=f"{config} ({job_name})",
                        x=x_vals,
                        y=y_vals,
                        marker=dict(
                            color=adjusted_color,
                            pattern=(
                                dict(shape=hatch_pattern) if hatch_pattern else None
                            ),
                        ),
                        legendgroup=legend_group,
                        legendgrouptitle_text=legend_title,
                        text=text_vals,
                        textposition="outside",
                        hovertemplate=f"{config} ({job_name})<br>Bandwidth: %{{y:.2f}} GiB/s<extra></extra>",
                    )
                )

    # Calculate y-axis range to accommodate text labels
    max_y = max(all_y_values) * 1.3 if all_y_values else 10

    fig.update_layout(
        title=f"{title}<br><sub>Higher is better ↑</sub>",
        xaxis_title="",
        yaxis_title="Bandwidth [GiB/s]",
        yaxis=dict(range=[0, max_y]),
        barmode="group",
        height=700,
        hovermode="closest",
        legend=dict(
            groupclick="toggleitem", y=1, yanchor="top", bgcolor="rgba(255,255,255,0.8)"
        ),
        bargap=0.15,
        bargroupgap=0.0,
    )

    return fig


def create_iops_figure(
    all_jobs: Dict[str, Dict[str, Dict[str, FIOData]]],
    title: str = "IOPS Comparison",
) -> go.Figure:
    """Create IOPS comparison figure for jobs and setups.

    Args:
        all_jobs: Nested dict {job_name: {config_name: {test_name: fio_parser.FIOData}}}
        title: Figure title

    Returns:
        Plotly figure with all jobs and setups
    """
    fig = go.Figure()
    all_y_values = []

    iops_jobs = {
        "iops randread": ("RandRead", "read"),
        "iops randwrite": ("RandWrite", "write"),
        "iops rwmixread": ("RWMix70R", "read"),
        "iops rwmixwrite": ("RWMix30R", "write"),
    }

    # Get all unique configs
    all_configs = set()
    for job_data in all_jobs.values():
        all_configs.update(job_data.keys())
    sorted_configs = sorted(all_configs)

    # Add traces: for each config, for each job
    for config in sorted_configs:
        legend_group, legend_title = fio_parser.get_legend_group_info(config)
        base_color = fio_parser.get_color_for_config(config)

        for job_name in sorted(all_jobs.keys()):
            if config not in all_jobs[job_name]:
                continue

            job_data = all_jobs[job_name][config]
            hatch_pattern = fio_parser.get_job_hatch_pattern(job_name)
            adjusted_color = fio_parser.adjust_color_for_filesystem(
                base_color, job_name
            )

            x_vals = []
            y_vals = []
            text_vals = []

            for job_key, (x_label, rw_type) in iops_jobs.items():
                if job_key in job_data:
                    data = job_data[job_key]
                    if rw_type == "read":
                        iops = data.read_iops_mean / 1000
                        iops_min = data.read_iops_min / 1000
                        iops_max = data.read_iops_max / 1000
                        iops_stddev = data.read_iops_stddev / 1000
                    else:
                        iops = data.write_iops_mean / 1000
                        iops_min = data.write_iops_min / 1000
                        iops_max = data.write_iops_max / 1000
                        iops_stddev = data.write_iops_stddev / 1000

                    x_vals.append(x_label)
                    y_vals.append(iops)
                    all_y_values.append(iops)
                    text_vals.append(
                        f"{iops:.0f}K<br>min: {iops_min:.0f}K<br>max: {iops_max:.0f}K<br>σ: {iops_stddev:.1f}K"
                    )

            if x_vals:
                fig.add_trace(
                    go.Bar(
                        name=f"{config} ({job_name})",
                        x=x_vals,
                        y=y_vals,
                        marker=dict(
                            color=adjusted_color,
                            pattern=(
                                dict(shape=hatch_pattern) if hatch_pattern else None
                            ),
                        ),
                        legendgroup=legend_group,
                        legendgrouptitle_text=legend_title,
                        text=text_vals,
                        textposition="outside",
                        hovertemplate=f"{config} ({job_name})<br>IOPS: %{{y:.0f}}K<extra></extra>",
                    )
                )

    # Calculate y-axis range to accommodate text labels
    max_y = max(all_y_values) * 1.3 if all_y_values else 100

    fig.update_layout(
        title=f"{title}<br><sub>Higher is better ↑</sub>",
        xaxis_title="",
        yaxis_title="Throughput [K IOPS]",
        yaxis=dict(range=[0, max_y]),
        barmode="group",
        height=700,
        hovermode="closest",
        legend=dict(
            groupclick="toggleitem", y=1, yanchor="top", bgcolor="rgba(255,255,255,0.8)"
        ),
    )

    return fig


def create_latency_figure(
    all_jobs: Dict[str, Dict[str, Dict[str, FIOData]]],
    title: str = "Latency Comparison",
) -> go.Figure:
    """Create latency comparison figure for jobs and setups.

    Args:
        all_jobs: Nested dict {job_name: {config_name: {test_name: fio_parser.FIOData}}}
        title: Figure title

    Returns:
        Plotly figure with all jobs and setups
    """
    fig = go.Figure()
    all_y_values = []

    lat_jobs = {
        "alat read": ("Read", "read"),
        "alat write": ("Write", "write"),
        "alat randread": ("RandRead", "read"),
        "alat randwrite": ("RandWrite", "write"),
    }

    # Get all unique configs
    all_configs = set()
    for job_data in all_jobs.values():
        all_configs.update(job_data.keys())
    sorted_configs = sorted(all_configs)

    # Add traces: for each config, for each job
    for config in sorted_configs:
        legend_group, legend_title = fio_parser.get_legend_group_info(config)
        base_color = fio_parser.get_color_for_config(config)

        for job_name in sorted(all_jobs.keys()):
            if config not in all_jobs[job_name]:
                continue

            job_data = all_jobs[job_name][config]
            hatch_pattern = fio_parser.get_job_hatch_pattern(job_name)
            adjusted_color = fio_parser.adjust_color_for_filesystem(
                base_color, job_name
            )

            x_vals = []
            y_vals = []
            text_vals = []

            for job_key, (x_label, rw_type) in lat_jobs.items():
                if job_key in job_data:
                    data = job_data[job_key]
                    if rw_type == "read":
                        lat = data.read_lat_mean / 1000
                        lat_min = data.read_lat_min / 1000
                        lat_max = data.read_lat_max / 1000
                        lat_stddev = data.read_lat_stddev / 1000
                    else:
                        lat = data.write_lat_mean / 1000
                        lat_min = data.write_lat_min / 1000
                        lat_max = data.write_lat_max / 1000
                        lat_stddev = data.write_lat_stddev / 1000

                    x_vals.append(x_label)
                    y_vals.append(lat)
                    all_y_values.append(lat)
                    text_vals.append(
                        f"{lat:.0f}μs<br>min: {lat_min:.0f}μs<br>max: {lat_max:.0f}μs<br>σ: {lat_stddev:.1f}μs"
                    )

            if x_vals:
                fig.add_trace(
                    go.Bar(
                        name=f"{config} ({job_name})",
                        x=x_vals,
                        y=y_vals,
                        marker=dict(
                            color=adjusted_color,
                            pattern=(
                                dict(shape=hatch_pattern) if hatch_pattern else None
                            ),
                        ),
                        legendgroup=legend_group,
                        legendgrouptitle_text=legend_title,
                        text=text_vals,
                        textposition="outside",
                        hovertemplate=f"{config} ({job_name})<br>Latency: %{{y:.0f}} μs<extra></extra>",
                    )
                )

    # Calculate y-axis range to accommodate text labels
    max_y = max(all_y_values) * 1.3 if all_y_values else 1000

    fig.update_layout(
        title=f"{title}<br><sub>Lower is better ↓</sub>",
        xaxis_title="",
        yaxis_title="4KB Latency [μs]",
        yaxis=dict(range=[0, max_y]),
        barmode="group",
        height=700,
        hovermode="closest",
        legend=dict(
            groupclick="toggleitem", y=1, yanchor="top", bgcolor="rgba(255,255,255,0.8)"
        ),
    )

    return fig


def create_figures_with_history(
    historical_data: Dict[str, Dict[str, List[Tuple[str, Path]]]],
    title_prefix: str = "",
    size_filter: Optional[str] = None,
) -> Tuple[go.Figure, go.Figure, go.Figure, List[TraceInfo]]:
    """Create bandwidth, IOPS, and latency figures with historical data support.

    Args:
        historical_data: {job_name: {config_name: [(timestamp, Path), ...]}}
        title_prefix: Prefix for chart titles
        size_filter: Optional size filter (e.g., "small", "medium", "large")

    Returns:
        Tuple of (bw_fig, iops_fig, lat_fig, trace_infos)
    """
    fig_bw = go.Figure()
    fig_iops = go.Figure()
    fig_lat = go.Figure()
    trace_infos: List[TraceInfo] = []

    # Track trace indices for each figure
    bw_trace_idx = 0
    iops_trace_idx = 0
    lat_trace_idx = 0

    # Track latest timestamps per (config, job)
    latest_timestamps: Dict[Tuple[str, str], str] = {}
    for job_name, config_data in historical_data.items():
        for config_name, timestamp_list in config_data.items():
            if timestamp_list:
                latest_timestamps[(config_name, job_name)] = timestamp_list[0][0]

    # Collect all y-values for axis scaling
    all_bw_y = []
    all_iops_y = []
    all_lat_y = []

    # Job definitions for each metric type
    bw_jobs = {"bw read": ("Read", "read"), "bw write": ("Write", "write")}
    iops_jobs = {
        "iops randread": ("RandRead", "read"),
        "iops randwrite": ("RandWrite", "write"),
        "iops rwmixread": ("RWMix70R", "read"),
        "iops rwmixwrite": ("RWMix30R", "write"),
    }
    lat_jobs = {
        "alat read": ("Read", "read"),
        "alat write": ("Write", "write"),
        "alat randread": ("RandRead", "read"),
        "alat randwrite": ("RandWrite", "write"),
    }

    # Build a flat list of all (timestamp, config, job) combinations
    all_entries: List[Tuple[str, str, str, Path, bool]] = []
    for job_name in sorted(historical_data.keys()):
        config_data = historical_data[job_name]
        for config_name in sorted(config_data.keys()):
            # Apply size filter
            if size_filter is not None and f"-{size_filter}" not in config_name:
                continue

            timestamp_list = config_data[config_name]
            for timestamp, filepath in timestamp_list:
                is_latest = timestamp == latest_timestamps.get(
                    (config_name, job_name), ""
                )
                all_entries.append(
                    (timestamp, config_name, job_name, filepath, is_latest)
                )

    # Sort entries by config, job, then timestamp (newest first)
    all_entries.sort(key=lambda x: (x[1], x[2], x[0]), reverse=False)
    # Re-sort to have newest first within each (config, job)
    all_entries.sort(key=lambda x: (x[1], x[2]))

    # Process each entry
    for timestamp, config_name, job_name, filepath, is_latest in all_entries:
        try:
            job_data = fio_parser.parse_fio_file(filepath)
        except Exception as e:
            print(f"Warning: Failed to parse {filepath}: {e}")
            continue

        # Create trace info for this (timestamp, config, job) combination
        trace_id = f"{timestamp}_{config_name}_{job_name}"
        display_name = (
            f"{fio_parser.format_timestamp(timestamp)} - {config_name} ({job_name})"
        )

        trace_info = TraceInfo(
            trace_id=trace_id,
            timestamp=timestamp,
            config=config_name,
            job=job_name,
            display_name=display_name,
            is_latest=is_latest,
        )

        # Get styling
        base_color = fio_parser.get_color_for_config(config_name)
        adjusted_color = fio_parser.adjust_color_for_filesystem(base_color, job_name)
        hatch_pattern = fio_parser.get_job_hatch_pattern(job_name)
        legend_group, legend_title = fio_parser.get_legend_group_info(config_name)

        # Visibility based on whether this is the latest
        visibility = True if is_latest else "legendonly"

        # Full date with time for trace names
        full_date = fio_parser.format_timestamp(timestamp)  # "2026-01-28 10:43"

        # --- Bandwidth traces ---
        x_vals, y_vals, text_vals = [], [], []
        for job_key, (x_label, rw_type) in bw_jobs.items():
            if job_key in job_data:
                data = job_data[job_key]
                if rw_type == "read":
                    bw = data.read_bw_mean / 1_000_000
                    bw_min = data.read_bw_min / 1_000_000
                    bw_max = data.read_bw_max / 1_000_000
                    bw_stddev = data.read_bw_stddev / 1_000_000
                else:
                    bw = data.write_bw_mean / 1_000_000
                    bw_min = data.write_bw_min / 1_000_000
                    bw_max = data.write_bw_max / 1_000_000
                    bw_stddev = data.write_bw_stddev / 1_000_000
                x_vals.append(x_label)
                y_vals.append(bw)
                all_bw_y.append(bw)
                text_vals.append(
                    f"{bw:.2f}<br>min: {bw_min:.2f}<br>max: {bw_max:.2f}<br>σ: {bw_stddev:.2f}"
                )

        if x_vals:
            trace_name = f"{config_name} ({job_name}) @ {full_date}"
            fig_bw.add_trace(
                go.Bar(
                    name=trace_name,
                    x=x_vals,
                    y=y_vals,
                    marker=dict(
                        color=adjusted_color,
                        pattern=dict(shape=hatch_pattern) if hatch_pattern else None,
                    ),
                    legendgroup=legend_group,
                    legendgrouptitle_text=legend_title,
                    text=text_vals,
                    textposition="outside",
                    hovertemplate=f"{trace_name}<br>Bandwidth: %{{y:.2f}} GiB/s<extra></extra>",
                    visible=visibility,
                )
            )
            trace_info.bw_trace_indices.append(bw_trace_idx)
            bw_trace_idx += 1

        # --- IOPS traces ---
        x_vals, y_vals, text_vals = [], [], []
        for job_key, (x_label, rw_type) in iops_jobs.items():
            if job_key in job_data:
                data = job_data[job_key]
                if rw_type == "read":
                    iops = data.read_iops_mean / 1000
                    iops_min = data.read_iops_min / 1000
                    iops_max = data.read_iops_max / 1000
                    iops_stddev = data.read_iops_stddev / 1000
                else:
                    iops = data.write_iops_mean / 1000
                    iops_min = data.write_iops_min / 1000
                    iops_max = data.write_iops_max / 1000
                    iops_stddev = data.write_iops_stddev / 1000
                x_vals.append(x_label)
                y_vals.append(iops)
                all_iops_y.append(iops)
                text_vals.append(
                    f"{iops:.0f}K<br>min: {iops_min:.0f}K<br>max: {iops_max:.0f}K<br>σ: {iops_stddev:.1f}K"
                )

        if x_vals:
            trace_name = f"{config_name} ({job_name}) @ {full_date}"
            fig_iops.add_trace(
                go.Bar(
                    name=trace_name,
                    x=x_vals,
                    y=y_vals,
                    marker=dict(
                        color=adjusted_color,
                        pattern=dict(shape=hatch_pattern) if hatch_pattern else None,
                    ),
                    legendgroup=legend_group,
                    legendgrouptitle_text=legend_title,
                    text=text_vals,
                    textposition="outside",
                    hovertemplate=f"{trace_name}<br>IOPS: %{{y:.0f}}K<extra></extra>",
                    visible=visibility,
                )
            )
            trace_info.iops_trace_indices.append(iops_trace_idx)
            iops_trace_idx += 1

        # --- Latency traces ---
        x_vals, y_vals, text_vals = [], [], []
        for job_key, (x_label, rw_type) in lat_jobs.items():
            if job_key in job_data:
                data = job_data[job_key]
                if rw_type == "read":
                    lat = data.read_lat_mean / 1000
                    lat_min = data.read_lat_min / 1000
                    lat_max = data.read_lat_max / 1000
                    lat_stddev = data.read_lat_stddev / 1000
                else:
                    lat = data.write_lat_mean / 1000
                    lat_min = data.write_lat_min / 1000
                    lat_max = data.write_lat_max / 1000
                    lat_stddev = data.write_lat_stddev / 1000
                x_vals.append(x_label)
                y_vals.append(lat)
                all_lat_y.append(lat)
                text_vals.append(
                    f"{lat:.0f}μs<br>min: {lat_min:.0f}μs<br>max: {lat_max:.0f}μs<br>σ: {lat_stddev:.1f}μs"
                )

        if x_vals:
            trace_name = f"{config_name} ({job_name}) @ {full_date}"
            fig_lat.add_trace(
                go.Bar(
                    name=trace_name,
                    x=x_vals,
                    y=y_vals,
                    marker=dict(
                        color=adjusted_color,
                        pattern=dict(shape=hatch_pattern) if hatch_pattern else None,
                    ),
                    legendgroup=legend_group,
                    legendgrouptitle_text=legend_title,
                    text=text_vals,
                    textposition="outside",
                    hovertemplate=f"{trace_name}<br>Latency: %{{y:.0f}} μs<extra></extra>",
                    visible=visibility,
                )
            )
            trace_info.lat_trace_indices.append(lat_trace_idx)
            lat_trace_idx += 1

        # Only add trace_info if we have at least one trace
        if (
            trace_info.bw_trace_indices
            or trace_info.iops_trace_indices
            or trace_info.lat_trace_indices
        ):
            trace_infos.append(trace_info)

    # Update layouts
    max_bw_y = max(all_bw_y) * 1.3 if all_bw_y else 10
    max_iops_y = max(all_iops_y) * 1.3 if all_iops_y else 100
    max_lat_y = max(all_lat_y) * 1.3 if all_lat_y else 1000

    title_suffix = f" - {title_prefix}" if title_prefix else ""

    fig_bw.update_layout(
        title=f"Bandwidth Comparison{title_suffix}<br><sub>Higher is better ↑</sub>",
        xaxis_title="",
        yaxis_title="Bandwidth [GiB/s]",
        yaxis=dict(range=[0, max_bw_y]),
        barmode="group",
        height=700,
        hovermode="closest",
        legend=dict(
            groupclick="toggleitem", y=1, yanchor="top", bgcolor="rgba(255,255,255,0.8)"
        ),
        bargap=0.15,
        bargroupgap=0.0,
    )

    fig_iops.update_layout(
        title=f"IOPS Comparison{title_suffix}<br><sub>Higher is better ↑</sub>",
        xaxis_title="",
        yaxis_title="Throughput [K IOPS]",
        yaxis=dict(range=[0, max_iops_y]),
        barmode="group",
        height=700,
        hovermode="closest",
        legend=dict(
            groupclick="toggleitem", y=1, yanchor="top", bgcolor="rgba(255,255,255,0.8)"
        ),
    )

    fig_lat.update_layout(
        title=f"Latency Comparison{title_suffix}<br><sub>Lower is better ↓</sub>",
        xaxis_title="",
        yaxis_title="4KB Latency [μs]",
        yaxis=dict(range=[0, max_lat_y]),
        barmode="group",
        height=700,
        hovermode="closest",
        legend=dict(
            groupclick="toggleitem", y=1, yanchor="top", bgcolor="rgba(255,255,255,0.8)"
        ),
    )

    return fig_bw, fig_iops, fig_lat, trace_infos


def create_fio_plot_with_history(
    job_name: str,
    historical_data: Dict[str, Dict[str, List[Tuple[str, Path]]]],
    output_file: Path,
    size_filter: Optional[str] = None,
) -> None:
    """Create interactive HTML plot for a single FIO job with historical data.

    Args:
        job_name: Name of the FIO job (e.g., "libaio", "spdk")
        historical_data: Full historical data dict (will be filtered to this job)
        output_file: Output HTML file path
        size_filter: Optional size to filter configs
    """
    # Filter to just this job
    filtered_data = {job_name: historical_data.get(job_name, {})}

    fig_bw, fig_iops, fig_lat, trace_infos = create_figures_with_history(
        filtered_data, title_prefix=job_name, size_filter=size_filter
    )

    if not trace_infos:
        print(f"No data found for job {job_name} with size filter {size_filter}")
        return

    html = generate_html_with_selector(fig_bw, fig_iops, fig_lat, trace_infos)

    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(html)
    print(f"Created {output_file}")


def create_fio_comparison_plot_with_history(
    historical_data: Dict[str, Dict[str, List[Tuple[str, Path]]]],
    output_file: Path,
    size_filter: Optional[str] = None,
) -> None:
    """Create comparison plot for multiple FIO jobs with historical data.

    Args:
        historical_data: Full historical data dict
        output_file: Output HTML file path
        size_filter: Optional size to filter configs
    """
    job_names = sorted(historical_data.keys())
    if len(job_names) <= 3:
        title = " vs ".join(job_names)
    else:
        title = f"Multi-Job Comparison ({len(job_names)} jobs)"

    fig_bw, fig_iops, fig_lat, trace_infos = create_figures_with_history(
        historical_data, title_prefix=title, size_filter=size_filter
    )

    if not trace_infos:
        print(f"No data found for comparison with size filter {size_filter}")
        return

    html = generate_html_with_selector(fig_bw, fig_iops, fig_lat, trace_infos)

    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(html)
    print(f"Created {output_file}")


def generate_all_plots(
    bench_result_dir: str = "bench-result/fio",
    max_history: int = 10,
    use_history: bool = True,
) -> None:
    """Generate all FIO plots from benchmark results.

    Creates:
    - Individual job pages for each job (libaio.html, spdk.html, etc.)
    - Multi-job comparison pages with all detected jobs
    - Per-size filtered versions of comparison pages

    Args:
        bench_result_dir: Path to benchmark results directory
        max_history: Maximum number of historical runs to include (default: 10)
        use_history: Whether to use historical data with selector (default: True)
    """
    bench_path = Path(bench_result_dir)
    output_dir = bench_path.parent / "fio-plots"
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"Discovering FIO files in {bench_path}...")

    if use_history:
        # Use new historical data functions
        historical_data = fio_parser.find_fio_files_with_history(
            bench_path, max_history
        )

        if not historical_data:
            print("No FIO files found!")
            return

        job_names = sorted(historical_data.keys())
        # Count total files
        total_files = sum(
            len(ts_list)
            for config_data in historical_data.values()
            for ts_list in config_data.values()
        )
        print(f"Found jobs: {', '.join(job_names)} ({total_files} total files)")

        # Generate individual job pages
        print("\nGenerating individual job pages with history selector...")
        for job_name in job_names:
            output_file = output_dir / f"{job_name}.html"
            create_fio_plot_with_history(job_name, historical_data, output_file)

        # Generate multi-job comparison pages
        print("\nGenerating multi-job comparison pages with history selector...")

        size_filters = [None, "small", "small2", "medium", "large"]

        for size_filter in size_filters:
            suffix = f"_{size_filter}" if size_filter else "_all"
            output_file = output_dir / f"comparison_all_jobs{suffix}.html"
            create_fio_comparison_plot_with_history(
                historical_data, output_file, size_filter
            )
    else:
        # Use original functions (backward compatibility)
        all_files = fio_parser.find_fio_files(bench_path)

        if not all_files:
            print("No FIO files found!")
            return

        job_names = sorted(all_files.keys())
        print(f"Found jobs: {', '.join(job_names)}")

        print("\nGenerating individual job pages...")
        for job_name in job_names:
            output_file = output_dir / f"{job_name}.html"
            create_fio_plot(job_name, all_files[job_name], output_file)

        print("\nGenerating multi-job comparison pages...")

        size_filters = [None, "small", "small2", "medium", "large"]

        for size_filter in size_filters:
            suffix = f"_{size_filter}" if size_filter else "_all"
            output_file = output_dir / f"comparison_all_jobs{suffix}.html"
            create_fio_comparison_plot(all_files, output_file, size_filter)

    print(f"\nAll plots generated in {output_dir}/")
