# fio NixOS guest additions: packaged FIO job files.

{ fio-jobs, fio-cuda }:

{ ... }:

{
  environment.systemPackages = [
    fio-jobs
    fio-cuda
  ];
}
