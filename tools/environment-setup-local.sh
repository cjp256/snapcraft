#!/bin/bash -ex

VIRTUAL_ENV=${VIRTUAL_ENV:=${HOME}/.venv/snapcraft}
SNAPCRAFT_DIR=${SNAPCRAFT_DIR:=$( cd "$(dirname "${BASH_SOURCE[0]}")/.." >/dev/null 2>&1 && pwd )}

sudo apt update
sudo apt install --yes \
    execstack \
    g++ \
    gcc \
    libapt-pkg-dev \
    libffi-dev \
    libsodium-dev \
    libssl-dev \
    libxml2-dev \
    libxslt1-dev \
    libyaml-dev \
    make \
    patchelf \
    python3-dev \
    python3-pip \
    python3-venv \
    rpm2cpio \
    squashfs-tools

# Create a virtual environment
python3 -m venv ${VIRTUAL_ENV}

# Activate virtual environment
source ${VIRTUAL_ENV}/bin/activate

# Install python dependencies
pip install --upgrade pip wheel
pip install -r "${SNAPCRAFT_DIR}/requirements-devel.txt"
pip install -r "${SNAPCRAFT_DIR}/requirements.txt"

# Install the project for quick tests
pip install --editable "${SNAPCRAFT_DIR}"

# Install black to run static tests.
sudo snap install black --beta

# Install shellcheck for static tests.
sudo snap install shellcheck

# Install bases.
sudo snap install core
sudo snap install core18
sudo snap install core20

echo "Virtual environment may be activated by running:"
echo "source ${VIRTUAL_ENV}/bin/activate"
