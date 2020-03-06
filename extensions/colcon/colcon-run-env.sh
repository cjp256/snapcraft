#!/bin/sh

# First, source the upstream ROS underlay
if [ -f "{underlay_setup}" ]; then
    . "{underlay_setup}"
fi

# Then source the overlay
if [ -f "{overlay_setup}" ]; then
    . "{overlay_setup}"
fi

exec "$@"
