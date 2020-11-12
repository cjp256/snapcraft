#!/bin/bash
# -*- Mode:sh; indent-tabs-mode:nil; tab-width:4 -*-
#
# Copyright (C) 2015-2018 Canonical Ltd
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 3 as
# published by the Free Software Foundation.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.f-1
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

set -e

usage() {
    echo "Usage: "
    echo "    ./runtests.sh tests/unit [<use-run>]"
    echo "    ./runtests.sh tests/integration[/<test-suite>]"
    echo "    ./runtests.sh spread"
    echo ""
    echo "<test-suite> can be one of: $(find tests/integration/ -mindepth 1 -maxdepth 1 -type d ! -name __pycache__ | tr '\n' ' ')"
    echo "<use-run> makes use of run instead of discover to run the tests"
}

run_snapcraft_tests(){
    test_suite="$1"

    if [[ "$test_suite" == "tests/unit"* ]]; then
        # Run with coverage results, if available.
        pytest --cov-report=xml --cov=snapcraft "$test_suite"
    else
        python3 -m unittest discover -b -v -s "$test_suite" -t .
    fi
}

run_spread(){
    TMP_SPREAD="$(mktemp -d)"
    curl -s https://niemeyer.s3.amazonaws.com/spread-amd64.tar.gz | tar xzv -C "$TMP_SPREAD"

    if [[ "$#" -eq 0 ]]; then
        "$TMP_SPREAD/spread" -v lxd:
    else
        "$TMP_SPREAD/spread" -v "$@"
    fi
}

if [[ "$#" -eq 0 ]]; then
    usage
    exit 1
fi

test_suite=$1
shift

if [[ "$test_suite" == "spread" ]]; then
    run_spread "$@"
elif [[ "$test_suite" == "-h" ]] || [[ "$test_suite" == "help" ]]; then
    usage
    exit 0
else
    run_snapcraft_tests "$test_suite" "$@"
fi

echo -e '\e[1;32mEverything passed\e[0m'
