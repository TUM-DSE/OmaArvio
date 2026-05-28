#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
SAR Data Visualization Generator for FIO Benchmarks

This module generates interactive HTML visualizations showing CPU and memory
usage during FIO benchmark runs. Parsing is handled by sar_parser.py.
"""

from pathlib import Path
from typing import Dict, List, Tuple, Optional

from core.tasks.plotting.sar_parser import SARData, parse_sar_file, find_sar_files


def get_color_for_config(config_name: str) -> str:
    """Get consistent color for a configuration based on type-location.

    Args:
        config_name: Format "{type}-{size}-{location}" (e.g., "snp-medium-host")

    Returns:
        Hex color string
    """
    # Extract type-location pattern (ignore size)
    # "snp-medium-host" → "snp-host"
    parts = config_name.split("-")
    if len(parts) >= 3:
        type_loc = f"{parts[0]}-{parts[-1]}"  # first and last parts
    else:
        type_loc = config_name

    color_map = {
        "snp-host": "#1f77b4",  # blue
        "snp-guest": "#ff7f0e",  # orange
        "amd-host": "#2ca02c",  # green
        "amd-guest": "#d62728",  # red
        "host-host": "#9467bd",  # purple
    }
    return color_map.get(type_loc, "#7f7f7f")  # gray as fallback


def get_line_style_for_config(config_name: str) -> str:
    """Get line style based on size from configuration name.

    Args:
        config_name: Format "{type}-{size}-{location}" (e.g., "snp-medium-host")

    Returns:
        Plotly dash style string
    """
    # Line styles by size
    line_styles = {
        "small": "dot",  # dotted
        "small2": "dash",  # dashed
        "medium": "solid",  # solid (default)
        "large": "dashdot",  # dash-dot
    }

    # Extract size from config name
    for size in ["small2", "small", "medium", "large"]:
        if f"-{size}-" in config_name:
            return line_styles.get(size, "solid")

    return "solid"  # default


def get_legend_group_info(config_name: str) -> Tuple[str, str]:
    """Get legend group and title for organizing legend.

    Args:
        config_name: Format "{type}-{size}-{location}" (e.g., "snp-medium-host")

    Returns:
        Tuple of (legendgroup, legendgrouptitle)
    """
    # Extract type from config name
    if config_name.startswith("snp-"):
        return ("snp", "SNP")
    elif config_name.startswith("amd-"):
        return ("amd", "AMD")
    elif config_name.startswith("host-"):
        return ("host", "HOST")

    return ("other", "Other")


def create_interactive_plot(
    parsed_data: Dict[str, SARData], config_names: List[str], title: str
):
    """Create interactive Plotly figure with CPU and memory subplots.

    Args:
        parsed_data: Dict mapping config names to SARData objects
        config_names: List of configuration names to plot
        title: Overall figure title

    Returns:
        Plotly figure object
    """
    from plotly.subplots import make_subplots
    import plotly.graph_objects as go

    # Create figure with 2 subplots
    fig = make_subplots(
        rows=2,
        cols=1,
        subplot_titles=("CPU Usage Over Time", "Memory Usage Over Time"),
        vertical_spacing=0.12,
        row_heights=[0.5, 0.5],
    )

    # Add traces for each configuration
    for config_name in sorted(config_names):
        if config_name not in parsed_data:
            continue

        data = parsed_data[config_name]
        if not data.timestamps:
            continue

        color = get_color_for_config(config_name)
        line_style = get_line_style_for_config(config_name)
        legend_group, legend_group_title = get_legend_group_info(config_name)

        # Calculate CPU used percentage
        cpu_used = [100 - idle for idle in data.cpu_idle]

        # Add CPU trace
        fig.add_trace(
            go.Scatter(
                x=data.timestamps,
                y=cpu_used,
                name=config_name,
                line=dict(color=color, width=2, dash=line_style),
                mode="lines",
                legendgroup=legend_group,
                legendgrouptitle_text=legend_group_title,
                hovertemplate=f"{config_name}<br>Time: %{{x:.1f}}s<br>CPU: %{{y:.1f}}%<extra></extra>",
            ),
            row=1,
            col=1,
        )

        # Calculate memory in GB
        mem_used_gb = [kb / (1024 * 1024) for kb in data.mem_used_kb]

        # Add Memory trace (don't show in legend again)
        fig.add_trace(
            go.Scatter(
                x=data.timestamps,
                y=mem_used_gb,
                name=config_name,
                line=dict(color=color, width=2, dash=line_style),
                mode="lines",
                legendgroup=legend_group,
                showlegend=False,
                hovertemplate=f"{config_name}<br>Time: %{{x:.1f}}s<br>Memory: %{{y:.2f}} GB<extra></extra>",
            ),
            row=2,
            col=1,
        )

    # Update axes
    fig.update_xaxes(title_text="Time (seconds)", row=2, col=1)
    fig.update_yaxes(title_text="CPU Usage (%)", row=1, col=1, range=[0, 105])
    fig.update_yaxes(title_text="Memory Used (GB)", row=2, col=1)

    # Calculate trace indices for host and guest configurations
    # Each config has 2 traces: CPU (i*2) and Memory (i*2+1)
    sorted_configs = sorted(
        [
            name
            for name in config_names
            if name in parsed_data and parsed_data[name].timestamps
        ]
    )

    host_indices = []
    guest_indices = []

    for i, config_name in enumerate(sorted_configs):
        trace_idx_cpu = i * 2
        trace_idx_mem = i * 2 + 1

        if config_name.endswith("-host"):
            host_indices.extend([trace_idx_cpu, trace_idx_mem])
        elif config_name.endswith("-guest"):
            guest_indices.extend([trace_idx_cpu, trace_idx_mem])

    # Create toggle buttons for host/guest filtering
    buttons = [
        dict(
            label="Hide All Host",
            method="restyle",
            args=[{"visible": False}, host_indices],
            args2=[{"visible": True}, host_indices],
        ),
        dict(
            label="Hide All Guest",
            method="restyle",
            args=[{"visible": False}, guest_indices],
            args2=[{"visible": True}, guest_indices],
        ),
        dict(
            label="Show All",
            method="restyle",
            args=[{"visible": True}],
        ),
    ]

    # Update layout
    fig.update_layout(
        title=dict(text=title, x=0.5, xanchor="center", font=dict(size=18)),
        height=900,
        hovermode="closest",
        legend=dict(
            title=dict(text="Configuration<br>(click to toggle)"),
            yanchor="top",
            y=0.99,
            xanchor="right",
            x=0.99,
        ),
        updatemenus=[
            dict(
                type="buttons",
                direction="right",
                x=0.0,
                xanchor="left",
                y=1.08,
                yanchor="top",
                buttons=buttons,
                pad={"r": 10, "t": 10},
                showactive=True,
            )
        ],
    )

    return fig


def create_comparison_plot(
    parsed_job1: Dict[str, SARData],
    parsed_job2: Dict[str, SARData],
    job1_name: str,
    job2_name: str,
    size_filter: Optional[str] = None,
):
    """Create overlaid comparison plot with both jobs on same axes.

    Args:
        parsed_job1: Parsed SAR data for first job
        parsed_job2: Parsed SAR data for second job
        job1_name: Name of first job
        job2_name: Name of second job
        size_filter: Optional size filter ("small", "small2", "medium", "large", or None for all)

    Returns:
        Plotly figure object
    """
    from plotly.subplots import make_subplots
    import plotly.graph_objects as go

    # Filter configs by size if specified
    def matches_size_filter(config_name: str) -> bool:
        if size_filter is None:
            return True
        return f"-{size_filter}-" in config_name

    # Filter parsed data
    filtered_job1 = {k: v for k, v in parsed_job1.items() if matches_size_filter(k)}
    filtered_job2 = {k: v for k, v in parsed_job2.items() if matches_size_filter(k)}

    # Create figure title
    size_suffix = f" ({size_filter})" if size_filter else " (all sizes)"
    cpu_title = f"CPU Usage: {job1_name} vs {job2_name}{size_suffix}"
    mem_title = f"Memory Usage: {job1_name} vs {job2_name}{size_suffix}"

    # Create figure with 2 subplots (overlaid, not side-by-side)
    fig = make_subplots(
        rows=2,
        cols=1,
        subplot_titles=(cpu_title, mem_title),
        vertical_spacing=0.12,
        row_heights=[0.5, 0.5],
    )

    all_configs = set(list(filtered_job1.keys()) + list(filtered_job2.keys()))

    # Add Job 1 traces (solid lines)
    for config_name in sorted(filtered_job1.keys()):
        data = filtered_job1[config_name]
        if not data.timestamps:
            continue

        color = get_color_for_config(config_name)
        line_style = get_line_style_for_config(config_name)
        legend_group, legend_group_title = get_legend_group_info(config_name)
        cpu_used = [100 - idle for idle in data.cpu_idle]
        mem_used_gb = [kb / (1024 * 1024) for kb in data.mem_used_kb]

        # CPU trace
        fig.add_trace(
            go.Scatter(
                x=data.timestamps,
                y=cpu_used,
                name=f"{job1_name}-{config_name}",
                line=dict(color=color, width=2, dash=line_style),
                mode="lines",
                legendgroup=legend_group,
                legendgrouptitle_text=legend_group_title,
                hovertemplate=f"{job1_name}-{config_name}<br>Time: %{{x:.1f}}s<br>CPU: %{{y:.1f}}%<extra></extra>",
            ),
            row=1,
            col=1,
        )

        # Memory trace
        fig.add_trace(
            go.Scatter(
                x=data.timestamps,
                y=mem_used_gb,
                name=f"{job1_name}-{config_name}",
                line=dict(color=color, width=2, dash=line_style),
                mode="lines",
                legendgroup=legend_group,
                showlegend=False,
                hovertemplate=f"{job1_name}-{config_name}<br>Time: %{{x:.1f}}s<br>Memory: %{{y:.2f}} GB<extra></extra>",
            ),
            row=2,
            col=1,
        )

    # Add Job 2 traces (different line pattern to distinguish from job1)
    for config_name in sorted(filtered_job2.keys()):
        data = filtered_job2[config_name]
        if not data.timestamps:
            continue

        color = get_color_for_config(config_name)
        line_style = get_line_style_for_config(config_name)
        # Make job2 lines slightly different - add "longdash" pattern
        job2_line_style = "longdash" if line_style == "solid" else line_style
        legend_group, legend_group_title = get_legend_group_info(config_name)
        cpu_used = [100 - idle for idle in data.cpu_idle]
        mem_used_gb = [kb / (1024 * 1024) for kb in data.mem_used_kb]

        # CPU trace
        fig.add_trace(
            go.Scatter(
                x=data.timestamps,
                y=cpu_used,
                name=f"{job2_name}-{config_name}",
                line=dict(color=color, width=2, dash=job2_line_style),
                mode="lines",
                legendgroup=legend_group,
                hovertemplate=f"{job2_name}-{config_name}<br>Time: %{{x:.1f}}s<br>CPU: %{{y:.1f}}%<extra></extra>",
            ),
            row=1,
            col=1,
        )

        # Memory trace
        fig.add_trace(
            go.Scatter(
                x=data.timestamps,
                y=mem_used_gb,
                name=f"{job2_name}-{config_name}",
                line=dict(color=color, width=2, dash=job2_line_style),
                mode="lines",
                legendgroup=legend_group,
                showlegend=False,
                hovertemplate=f"{job2_name}-{config_name}<br>Time: %{{x:.1f}}s<br>Memory: %{{y:.2f}} GB<extra></extra>",
            ),
            row=2,
            col=1,
        )

    # Update axes
    fig.update_xaxes(title_text="Time (seconds)", row=2, col=1)
    fig.update_yaxes(title_text="CPU Usage (%)", row=1, col=1, range=[0, 105])
    fig.update_yaxes(title_text="Memory Used (GB)", row=2, col=1)

    # Calculate trace indices for host and guest configurations (both jobs)
    # Each config has 2 traces: CPU (i*2) and Memory (i*2+1)
    sorted_configs_job1 = sorted(
        [name for name in filtered_job1.keys() if filtered_job1[name].timestamps]
    )
    sorted_configs_job2 = sorted(
        [name for name in filtered_job2.keys() if filtered_job2[name].timestamps]
    )

    host_indices = []
    guest_indices = []

    # Job 1 traces
    for i, config_name in enumerate(sorted_configs_job1):
        trace_idx_cpu = i * 2
        trace_idx_mem = i * 2 + 1

        if config_name.endswith("-host"):
            host_indices.extend([trace_idx_cpu, trace_idx_mem])
        elif config_name.endswith("-guest"):
            guest_indices.extend([trace_idx_cpu, trace_idx_mem])

    # Job 2 traces (offset by job1 trace count)
    offset = len(sorted_configs_job1) * 2
    for i, config_name in enumerate(sorted_configs_job2):
        trace_idx_cpu = offset + i * 2
        trace_idx_mem = offset + i * 2 + 1

        if config_name.endswith("-host"):
            host_indices.extend([trace_idx_cpu, trace_idx_mem])
        elif config_name.endswith("-guest"):
            guest_indices.extend([trace_idx_cpu, trace_idx_mem])

    # Create toggle buttons for host/guest filtering
    buttons = [
        dict(
            label="Hide All Host",
            method="restyle",
            args=[{"visible": False}, host_indices],
            args2=[{"visible": True}, host_indices],
        ),
        dict(
            label="Hide All Guest",
            method="restyle",
            args=[{"visible": False}, guest_indices],
            args2=[{"visible": True}, guest_indices],
        ),
        dict(
            label="Show All",
            method="restyle",
            args=[{"visible": True}],
        ),
    ]

    # Create title with size suffix
    title_suffix = f" ({size_filter} only)" if size_filter else " (all sizes)"
    title_text = f"FIO Benchmark Comparison: {job1_name} vs {job2_name}{title_suffix}"
    subtitle = f"Line styles: solid=medium, dotted=small, dashed=small2, dashdot=large | Hover for details"

    # Update layout
    fig.update_layout(
        title=dict(
            text=f"{title_text}<br><sub>{subtitle}</sub>",
            x=0.5,
            xanchor="center",
            font=dict(size=18),
        ),
        height=900,
        hovermode="closest",
        legend=dict(
            title=dict(text="Configuration<br>(click to toggle)"),
            yanchor="top",
            y=0.99,
            xanchor="right",
            x=0.99,
        ),
        updatemenus=[
            dict(
                type="buttons",
                direction="right",
                x=0.0,
                xanchor="left",
                y=1.08,
                yanchor="top",
                buttons=buttons,
                pad={"r": 10, "t": 10},
                showactive=True,
            )
        ],
    )

    return fig


def generate_job_page(
    job_name: str, sar_files_dict: Dict[str, Path], output_path: Path
):
    """Generate an interactive HTML page for a single job type.

    Args:
        job_name: Name of the job (e.g., 'spdk', 'libaio')
        sar_files_dict: Dict mapping config names to SAR file paths
        output_path: Path where HTML file should be saved
    """
    print(f"  Generating page for job: {job_name}")

    # Parse all SAR files for this job
    parsed_data = {}
    for config_name, sar_file in sar_files_dict.items():
        try:
            print(f"    Parsing {config_name}: {sar_file}")
            parsed_data[config_name] = parse_sar_file(sar_file)
        except Exception as e:
            print(f"    Warning: Failed to parse {sar_file}: {e}")
            continue

    if not parsed_data:
        print(f"    No valid data found for {job_name}, skipping")
        return

    # Create interactive plot
    config_names = list(parsed_data.keys())
    title = f"FIO Benchmark SAR Analysis: {job_name}"
    fig = create_interactive_plot(parsed_data, config_names, title)

    # Save to HTML
    html_str = fig.to_html(include_plotlyjs="cdn", config={"displayModeBar": True})
    output_path.write_text(html_str)
    print(f"    ✓ Saved: {output_path}")


def generate_comparison_page(
    job_pair: Tuple[str, str],
    sar_files: Dict[str, Dict[str, Path]],
    output_path: Path,
    size_filter: Optional[str] = None,
):
    """Generate a comparison page with overlaid plots for two jobs.

    Args:
        job_pair: Tuple of two job names to compare (e.g., ('libaio', 'spdk'))
        sar_files: Full dict of all SAR files
        output_path: Path where HTML file should be saved
        size_filter: Optional size filter ("small", "small2", "medium", "large", or None for all)
    """
    job1, job2 = job_pair
    size_desc = f" ({size_filter})" if size_filter else " (all sizes)"
    print(f"  Generating comparison: {job1} vs {job2}{size_desc}")

    # Parse data for both jobs
    parsed_job1 = {}
    parsed_job2 = {}

    if job1 in sar_files:
        for config_name, sar_file in sar_files[job1].items():
            try:
                parsed_job1[config_name] = parse_sar_file(sar_file)
            except Exception as e:
                print(f"    Warning: Failed to parse {sar_file}: {e}")

    if job2 in sar_files:
        for config_name, sar_file in sar_files[job2].items():
            try:
                parsed_job2[config_name] = parse_sar_file(sar_file)
            except Exception as e:
                print(f"    Warning: Failed to parse {sar_file}: {e}")

    if not parsed_job1 and not parsed_job2:
        print(f"    No valid data found for comparison, skipping")
        return

    # Create overlaid comparison plot
    fig = create_comparison_plot(parsed_job1, parsed_job2, job1, job2, size_filter)

    # Save to HTML
    html_str = fig.to_html(include_plotlyjs="cdn", config={"displayModeBar": True})
    output_path.write_text(html_str)
    print(f"    ✓ Saved: {output_path}")


def generate_all_plots(bench_result_dir: str = "bench-result/fio"):
    """Main entry point: generate all SAR visualization HTML files.

    Args:
        bench_result_dir: Path to benchmark results directory
    """
    bench_path = Path(bench_result_dir)

    if not bench_path.exists():
        print(f"Error: Benchmark directory not found: {bench_result_dir}")
        return

    # Create plots subdirectory
    plots_path = bench_path / "plots"
    plots_path.mkdir(exist_ok=True)

    print(f"Scanning for SAR files in: {bench_result_dir}")
    sar_files = find_sar_files(bench_path)

    if not sar_files:
        print("No SAR files found!")
        return

    print(f"\nFound SAR data for {len(sar_files)} job types:")
    for job_name, configs in sar_files.items():
        print(f"  - {job_name}: {len(configs)} configurations")

    # Generate individual job pages
    print("\n=== Generating Individual Job Pages ===")
    for job_name, config_files in sar_files.items():
        output_file = plots_path / f"{job_name}_sar_analysis.html"
        generate_job_page(job_name, config_files, output_file)

    # Generate comparison pages
    print("\n=== Generating Comparison Pages ===")

    # Define size filters: None for "all", plus each individual size
    size_filters = [None, "small", "small2", "medium", "large"]

    # Comparison 1: libaio vs spdk (default blocksize)
    if "libaio" in sar_files and "spdk" in sar_files:
        for size_filter in size_filters:
            suffix = f"_{size_filter}" if size_filter else "_all"
            output_file = plots_path / f"comparison_default{suffix}.html"
            generate_comparison_page(
                ("libaio", "spdk"), sar_files, output_file, size_filter
            )

    print("\n✓ Plot generation complete!")
    print(f"\nHTML files saved in: {plots_path.absolute()}")
    print("\nInteractive features:")
    print("  - Click legend items to show/hide individual configurations")
    print("  - Legend grouped by VM type (SNP/AMD/HOST)")
    print("  - Line colors indicate type-location (snp-host=blue, amd-guest=red, etc.)")
    print("  - Line styles indicate size (solid=medium, dotted=small, etc.)")
    print("  - Comparison pages: Per-size and all-sizes versions available")
