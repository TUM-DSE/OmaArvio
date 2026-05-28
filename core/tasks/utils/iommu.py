def iommu_label(no_iommu: bool) -> str:
    """Return 'iommu' or 'noiommu' based on the explicit no_iommu flag.

    The flag must be set explicitly by the caller — IOMMU state requires
    a server reboot to change and cannot be toggled at runtime.
    """
    return "noiommu" if no_iommu else "iommu"
