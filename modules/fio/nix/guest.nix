# fio NixOS guest additions: packaged FIO job files.

{ fio-jobs }:

{ ... }:

{
  environment.systemPackages = [
    fio-jobs
  ];
}
