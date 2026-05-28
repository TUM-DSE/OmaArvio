FROM nvidia/cuda:13.2.1-devel-ubuntu24.04

RUN apt-get update && apt-get upgrade -y

RUN apt-get install gds-tools-13-2 python3 gettext-base -y

ENV PATH="/usr/local/cuda-13.2/gds/tools:${PATH}"

RUN sed -i 's/"use_pci_p2pdma": false/"use_pci_p2pdma": true/g' /etc/cufile.json && \
    sed -i 's/"use_pci_p2pdma" : false/"use_pci_p2pdma": true/g' /etc/cufile.json && \
    sed -i 's/"allow_compat_mode": true/"allow_compat_mode": false/' /etc/cufile.json
