# FIO Job files
Based on: https://github.com/TUM-DSE/CVM_eval/tree/main/config/fio

## Structure

- `readwrite.fio` - 10 workload job definitions (latency, bandwidth, IOPS for read+write)
- `readwrite_bandwidth.fio` - 2 bandwidth-only jobs (read+write, focused on encryption throughput)
- `readonly.fio` - 4 read-only workload job definitions (for dm-verity/fs-verity)
- `readonly_bandwidth.fio` - 1 bandwidth read job (for dm-verity/fs-verity throughput)
- `spdk_bandwidth.fio` - Standalone SPDK bandwidth write job (100G, not a symlink)
- `misc/spdk-write.fio` - Standalone SPDK write reproducer

Engine-specific parameters (`ioengine`, `direct`, `thread`, `cmd_type`, `cuda_io`) are **not** in the
`.fio` files. They are injected as CLI arguments by the Python benchmark runner
(`tasks/actions/storage.py` via `ENGINE_CONFIGS` and `FioJobConfig`).

## Symlinks

Symlinks map job names to the correct job file. The job name encodes the engine and storage
stack. Each engine also has a `_bandwidth` variant pointing to the corresponding bandwidth file:

```
spdk.fio                             -> readwrite.fio

libaio.fio                           -> readwrite.fio
libaio_bandwidth.fio                 -> readwrite_bandwidth.fio

libaio-ext4.fio                      -> readwrite.fio
libaio-ext4_bandwidth.fio            -> readwrite_bandwidth.fio
libaio-f2fs.fio                      -> readwrite.fio

libaio-luks-ext4-aes.fio             -> readwrite.fio
libaio-luks-ext4-aes_bandwidth.fio   -> readwrite_bandwidth.fio
libaio-luks-ext4-aes-xts.fio         -> readwrite.fio
libaio-luks-ext4-aes-xts_bandwidth.fio -> readwrite_bandwidth.fio
libaio-luks-ext4-aegis128.fio        -> readwrite.fio
libaio-luks-ext4-aegis128_bandwidth.fio -> readwrite_bandwidth.fio
libaio-luks-f2fs-aes.fio             -> readwrite.fio
libaio-luks-f2fs-aes-xts.fio         -> readwrite.fio
libaio-luks-f2fs-aegis128.fio        -> readwrite.fio

libaio-dmverity-ext4.fio             -> readonly.fio
libaio-dmverity-ext4_bandwidth.fio   -> readonly_bandwidth.fio
libaio-dmverity-f2fs.fio             -> readonly.fio
libaio-dmverity-f2fs_bandwidth.fio   -> readonly_bandwidth.fio

libaio-fsverity-ext4.fio             -> readonly.fio
libaio-fsverity-ext4_bandwidth.fio   -> readonly_bandwidth.fio
libaio-fsverity-f2fs.fio             -> readonly.fio
libaio-fsverity-f2fs_bandwidth.fio   -> readonly_bandwidth.fio

libcufilep2p-ext4.fio                -> readwrite.fio
libcufilep2p-ext4_bandwidth.fio      -> readwrite_bandwidth.fio
libcufileposix-ext4.fio              -> readwrite.fio
libcufileposix-ext4_bandwidth.fio    -> readwrite_bandwidth.fio
```

To add a new workload pattern, create a new `.fio` file with job definitions
(no engine params) and add symlinks for each engine+storage combination.
