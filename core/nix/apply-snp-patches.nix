kernel:

kernel.override {
  kernelPatches = (kernel.kernelPatches or [ ]) ++ [
    {
      name = "enable-sev-snp";
      patch = null;
      extraConfig = ''
        AMD_MEM_ENCRYPT y
        VIRT_DRIVERS y
        SEV_GUEST m
        X86_CPUID m
        VFIO_NOIOMMU y
        DMA_API_DEBUG y
        FS_VERITY y
      '';
    }
  ];
}
