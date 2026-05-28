"""
Common Matplotlib Configuration for Paper Plots

Centralized styling constants and helper functions for publication-ready plots.
Based on reference styling from Wallet-GPU motivation_plotting_config.py.

All constants are designed for academic paper figures with precise dimensions
and consistent typography.
"""

import matplotlib as mpl
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.legend_handler import HandlerPatch
from matplotlib.container import BarContainer
import seaborn as sns
from pathlib import Path


# ============================
# Legend Customization
# ============================


class DenseHatchHandler(HandlerPatch):
    """Custom legend handler that increases hatch pattern density using official matplotlib API.

    Uses matplotlib.hatch.get_path() with custom density parameter to generate
    denser hatch patterns in legend patches only, without affecting plot bars.

    Handles both Patch objects and BarContainer objects (extracts first patch from container).
    """

    def __init__(self, density=1, **kwargs):
        """
        Args:
            density: Lines per unit square for hatch pattern (default: 1).
        """
        self.density = density
        super().__init__(**kwargs)

    def create_artists(
        self, legend, orig_handle, xdescent, ydescent, width, height, fontsize, trans
    ):
        # Handle BarContainer by extracting the first patch
        if isinstance(orig_handle, BarContainer):
            # Get the first patch from the container as a template
            orig_handle = orig_handle.patches[0]

        # Get the legend patch using parent class
        artists = super().create_artists(
            legend, orig_handle, xdescent, ydescent, width, height, fontsize, trans
        )

        # Apply denser hatch pattern and smaller linewidth to the legend patch
        for artist in artists:
            if hasattr(artist, "get_hatch") and hasattr(artist, "set_hatch"):
                original_hatch = artist.get_hatch() or ""
                if original_hatch:
                    dense_hatch = original_hatch * self.density
                    artist.set_hatch(dense_hatch)
            # Set smaller linewidth for the hatch pattern
            if hasattr(artist, "set_hatch_linewidth"):
                artist.set_hatch_linewidth(0.7)

        return artists


# ============================
# Standard Figure Dimensions
# ============================

# Axis dimensions (in inches) - Total width: 3.3"
AX_WIDTH = 2.85  # Width of plotting area (3.3 - 0.4 - 0.05)
AX_HEIGHT = 0.8625  # Height of plotting area (1.15 * 0.75)
AX_HEIGHT_SMALL = 0.65  # Smaller height for compact plots (1.15 * 0.565)
AX_HEIGHT_SMALL2 = 0.57  # Even smaller height for very compact plots (1.15 * 0.35)

# Margins (in inches)
AX_LEFT_MARGIN = 0.4  # Left margin for y-axis labels
AX_RIGHT_MARGIN = 0.05  # Right margin
AX_TOP_MARGIN = 0.65  # Top margin for title
AX_BOTTOM_MARGIN = 0.5  # Bottom margin for x-axis labels


# ============================
# Font Sizes and Text Settings
# ============================

TITLE_FONTSIZE = 6  # Plot title font size (pt)
TITLE_COLOR = "navy"  # Plot title color
AXIS_LABEL_FONTSIZE = 6  # Axis labels font size (pt)
TICKS_FONTSIZE = 5  # Tick labels font size (pt)
LEGEND_FONTSIZE = 6  # Legend font size (pt)
ANNOTATION_SIZE = 4  # Annotation text size (pt)
ANNOTATION_ROTATION = 30  # Annotation text angle (degrees)


# ============================
# Colors and Styles
# ============================

# Color palettes (using seaborn)
PALETTE_PASTEL = sns.color_palette("pastel")  # Pastel colors
PALETTE_REGULAR = sns.color_palette("deep")  # Regular colors

# Hatch patterns for bar plots (repeated for enough variations)
HATCHES = ["", "//", "\\\\", "..", "xx"] * 3


# ============================
# VM Type and Engine Mappings
# ============================

# VM type to color mapping (using PALETTE_PASTEL indices)
VM_TYPE_COLORS = {
    "host": PALETTE_PASTEL[1],  # Orange
    "amd": PALETTE_PASTEL[0],  # Blue
    "snp": PALETTE_PASTEL[2],  # Green
}

# Engine to hatch pattern mapping (matplotlib patterns, doubled for visibility)
ENGINE_HATCHES = {
    "spdk": "//",  # Solid (no hatch)
    "libaio": "",  # Forward slash
    "io_uring_cmd": "\\\\",  # Backslash
    # Encrypted filesystem variants
    "libaio-luks-ext4-aes": "--",
    "libaio-luks-ext4-aegis128l": "++",
    "libaio-luks-ext4-aes-xts": "xx",
    "libaio-dmverity-ext4": "..",
    "libaio-fsverity-ext4": "oo",
    # Block storage variants
    "libaio-ext4": "///",
}

# Fallback values
DEFAULT_VM_COLOR = PALETTE_PASTEL[7]  # Gray from pastel palette
DEFAULT_ENGINE_HATCH = ""  # Solid


# ============================
# Line Widths and Sizes
# ============================

LINE_WIDTH = 1.0  # Default line width
MARKER_SIZE = 2.0  # Default marker size
ERROR_BAR_CAP_SIZE = 1.0  # Error bar cap size
GRID_LINE_WIDTH = 0.5  # Grid line width
GRID_ALPHA = 0.7  # Grid line opacity
BORDER_LINE_WIDTH = 0.6  # Axis border line width


# ============================
# Bar Plot Settings
# ============================

BAR_EDGECOLOR = "k"  # Default edge color for bars
BAR_LINEWIDTH = 0.1  # Default edge line width for bars
BAR_ALPHA = 0.9  # Default opacity for bars


# ============================
# Semantic Colors & Hatches (AccelStore)
# ============================

COLOR_BASE = PALETTE_PASTEL[0]  # Blue
COLOR_ENCRYPTION = PALETTE_PASTEL[1]  # Orange
COLOR_MERKLE = PALETTE_PASTEL[2]  # Green
COLOR_METADATA = PALETTE_PASTEL[3]  # Red
COLOR_TC = PALETTE_PASTEL[4]  # Purple
COLOR_CIF = PALETTE_PASTEL[0]  # Blue (w/ CIF)
COLOR_NO_CIF = PALETTE_PASTEL[1]  # Orange (w/o CIF)

HATCH_ENCRYPTION = "//"
HATCH_MERKLE = "\\\\"
HATCH_METADATA = "..."
HATCH_READ = ""  # Solid fill for reads
HATCH_WRITE = "//"  # Hatched for writes


# ============================
# Legend Configuration
# ============================

LEGEND_BBOX_HEIGHT = 1.2  # Y position for legend bbox_to_anchor
LEGEND_ALIGN = "lower center"  # Legend alignment position


# ============================
# Title Templates
# ============================

HIGHER_BETTER_TITLE = "Higher is better ↑"  # For metrics where higher is better
LOWER_BETTER_TITLE = "Lower is better ↓"  # For metrics where lower is better


# ============================
# Matplotlib Settings
# ============================


def apply_mpl_settings():
    """Apply standard matplotlib settings for paper plots.

    Configures:
    - Non-interactive backend (Agg)
    - TrueType fonts for PDF compatibility
    - Standard seaborn styling
    """
    # Use non-interactive backend
    mpl.use("Agg")

    # Configure PDF font embedding for compatibility
    mpl.rcParams["pdf.fonttype"] = 42  # TrueType fonts
    mpl.rcParams["ps.fonttype"] = 42

    # Seaborn style settings for clean plots
    sns.set_style("whitegrid")
    sns.set_style("ticks", {"xtick.major.size": 8, "ytick.major.size": 8})
    sns.set_context(
        "paper", rc={"font.size": 5, "axes.titlesize": 5, "axes.labelsize": 8}
    )


# ============================
# Helper Functions
# ============================


def create_figure(
    ax_width=AX_WIDTH,
    ax_height=AX_HEIGHT,
    left_margin=AX_LEFT_MARGIN,
    right_margin=AX_RIGHT_MARGIN,
    top_margin=AX_TOP_MARGIN,
    bottom_margin=AX_BOTTOM_MARGIN,
):
    """Create a figure with standardized axis dimensions.

    This function creates figures with precise dimensions suitable for
    publication. The axis dimensions are specified directly, and margins
    are added around them for labels and titles.

    Args:
        ax_width: Width of the plotting area in inches
        ax_height: Height of the plotting area in inches
        left_margin: Left margin in inches (for y-axis labels)
        right_margin: Right margin in inches
        top_margin: Top margin in inches (for title)
        bottom_margin: Bottom margin in inches (for x-axis labels)

    Returns:
        Tuple of (fig, ax) - matplotlib Figure and Axes objects
    """
    # Calculate total figure size from axis dimensions and margins
    fig_width = ax_width + left_margin + right_margin
    fig_height = ax_height + top_margin + bottom_margin

    # Create figure
    fig = plt.figure(figsize=(fig_width, fig_height))

    # Calculate position of the axes as a fraction of figure dimensions
    left = left_margin / fig_width
    bottom = bottom_margin / fig_height
    width = ax_width / fig_width
    height = ax_height / fig_height

    # Create axes with specified dimensions
    ax = fig.add_axes([left, bottom, width, height])

    return fig, ax


def apply_axis_style(ax, title=None, xlabel=None, ylabel=None, grid=True):
    """Apply consistent styling to a matplotlib axis.

    Configures tick sizes, grid appearance, spine widths, and labels
    to match the standard paper plot style.

    Args:
        ax: matplotlib Axes object to style
        title: Plot title (optional)
        xlabel: X-axis label (optional)
        ylabel: Y-axis label (optional)
        grid: Whether to show grid lines (default: True)
    """
    # Set title and labels if provided
    if title:
        ax.set_title(title, fontsize=TITLE_FONTSIZE, pad=3, color=TITLE_COLOR)
    if xlabel:
        ax.set_xlabel(xlabel, fontsize=AXIS_LABEL_FONTSIZE)
    if ylabel:
        ax.set_ylabel(ylabel, fontsize=AXIS_LABEL_FONTSIZE)

    # Configure ticks
    ax.tick_params(
        axis="both",
        which="major",
        labelsize=TICKS_FONTSIZE,
        pad=3,
        length=2.5,
        width=BORDER_LINE_WIDTH,
        direction="in",
    )
    ax.tick_params(axis="both", which="minor", length=1.5, width=0.4, direction="in")

    # Grid settings
    if grid:
        ax.grid(True, linestyle="--", alpha=GRID_ALPHA, linewidth=GRID_LINE_WIDTH)

    # Spine settings (border around plot)
    for spine in ax.spines.values():
        spine.set_linewidth(BORDER_LINE_WIDTH)


def get_style_from_setup(setup: dict) -> tuple:
    """Get color and hatch pattern from a setup dict.

    Supports manual override via 'color' and 'hatch' keys in setup dict,
    with auto-discovery from path as fallback.

    Args:
        setup: Setup dict with the following keys:
            - "path": Setup path like "bench-result/fio/host-large/spdk"
            - "color" (optional): Manual color override (RGB tuple or matplotlib color)
            - "hatch" (optional): Manual hatch pattern override (e.g., "//", "\\\\", "..")

    Returns:
        Tuple of (color, hatch) where:
        - color is an RGB tuple like (0.5, 0.6, 0.7) or matplotlib color
        - hatch is a string pattern like "//" or ""

    Examples:
        # Auto-discovery from path
        get_style_from_setup({"path": "bench-result/fio/host-large/spdk"})
        → (PALETTE_PASTEL[1], "")

        # Manual color only
        get_style_from_setup({"path": "...", "color": (1.0, 0.0, 0.0)})
        → ((1.0, 0.0, 0.0), "")

        # Manual color and hatch
        get_style_from_setup({"path": "...", "color": "red", "hatch": "xx"})
        → ("red", "xx")
    """
    # Check for manual overrides
    color = setup.get("color")
    hatch = setup.get("hatch")

    # If both are manually set, return immediately
    if color is not None and hatch is not None:
        return color, hatch

    # Auto-discover missing values from path
    path = setup.get("path")
    if path is None:
        # No path to auto-discover from, use defaults for missing values
        color = color if color is not None else DEFAULT_VM_COLOR
        hatch = hatch if hatch is not None else DEFAULT_ENGINE_HATCH
        return color, hatch

    # Parse path for auto-discovery
    parts = Path(path).parts

    print(parts)

    if len(parts) >= 3:
        config_name = parts[-2]  # e.g., "amd-disk-medium"
        job_name_raw = parts[-1]  # e.g., "spdk_bs512"

        # Strip block size suffix (e.g., "_bs512") from job name
        job_name = job_name_raw.split("_bs")[0]  # e.g., "spdk"

        # Extract VM type from config_name (first component before dash)
        vm_type = config_name.split("-")[0]

        # Get color and hatch from mappings (only if not manually set)
        if color is None:
            color = VM_TYPE_COLORS.get(vm_type, DEFAULT_VM_COLOR)
        if hatch is None:
            hatch = ENGINE_HATCHES.get(job_name, DEFAULT_ENGINE_HATCH)

        return color, hatch

    # Fallback for malformed paths (only for missing values)
    color = color if color is not None else DEFAULT_VM_COLOR
    hatch = hatch if hatch is not None else DEFAULT_ENGINE_HATCH
    return color, hatch
