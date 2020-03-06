# -*- Mode:Python; indent-tabs-mode:nil; tab-width:4 -*-
#
# Copyright (C) 2019 Canonical Ltd
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 3 as
# published by the Free Software Foundation.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

"""The colcon plugin is useful for building ROS2 parts.

This plugin uses the common plugin keywords as well as those for "sources".
For more information check the 'plugins' topic for the former and the
'sources' topic for the latter.

Additionally, this plugin uses the following plugin-specific keywords:

    - colcon-packages:
      (list of strings)
      List of colcon packages to build. If not specified, all packages in the
      workspace will be built. If set to an empty list ([]), no packages will
      be built, which could be useful if you only want ROS debs in the snap.
    - colcon-packages-ignore:
      (list of strings)
      List of colcon packages to ignore. If not specified or set to an empty
      list ([]), no packages will be ignored.
    - colcon-source-space:
      (string)
      The source space containing colcon packages (defaults to 'src').
    - colcon-rosdistro:
      (string)
      The ROS distro to use. Available options are bouncy, crystal, dashing, and
      eloquent, all of which are only compatible with core18 as the base. The default
      value is crystal.
    - colcon-cmake-args:
      (list of strings)
      Arguments to pass to cmake projects. Note that any arguments here which match
      colcon arguments need to be prefixed with a space. This can be done by quoting
      each argument with a leading space.
    - colcon-catkin-cmake-args:
      (list of strings)
      Arguments to pass to catkin packages. Note that any arguments here which match
      colcon arguments need to be prefixed with a space. This can be done by quoting
      each argument with a leading space.
    - colcon-ament-cmake-args:
      (list of strings)
      Arguments to pass to ament_cmake packages. Note that any arguments here which
      match colcon arguments need to be prefixed with a space. This can be done by
      quoting each argument with a leading space.
"""

import contextlib
import collections
import os
import logging
import re
import shutil
import textwrap
from typing import Dict, List

import snapcraft
from snapcraft.plugins import _ros
from snapcraft.plugins import _python
from snapcraft import file_utils, repo
from snapcraft.internal import errors, mangling
from snapcraft.internal.meta.package_management import PackageManagement, Repository

logger = logging.getLogger(__name__)

# Map bases to ROS releases
_ROSDISTRO_TO_BASE_MAP = {
    "bouncy": "core18",
    "crystal": "core18",
    "dashing": "core18",
    "eloquent": "core18",
}

# Snaps can still be built with ROS distros that are end-of-life, but such
# things are not supported. Maintain a list so we can warn about such things.
# This really should be using rosdistro to automatically detect the support
# status, but that's a larger feature than we want to implement at this time.
_EOL_ROSDISTROS = ["bouncy", "crystal"]

# Map bases to Ubuntu releases. Every base in _ROSDISTRO_TO_BASE_MAP needs to be
# specified here.
_BASE_TO_UBUNTU_RELEASE_MAP = {"core18": "bionic"}

_SUPPORTED_DEPENDENCY_TYPES = {"apt", "pip"}

_ROS_KEYRING_PATH = os.path.join(snapcraft.internal.common.get_keyringsdir(), "ros.gpg")

_ROS2_KEY = """
-----BEGIN PGP PUBLIC KEY BLOCK-----
Version: GnuPG v1

mQINBFzvJpYBEADY8l1YvO7iYW5gUESyzsTGnMvVUmlV3XarBaJz9bGRmgPXh7jc
VFrQhE0L/HV7LOfoLI9H2GWYyHBqN5ERBlcA8XxG3ZvX7t9nAZPQT2Xxe3GT3tro
u5oCR+SyHN9xPnUwDuqUSvJ2eqMYb9B/Hph3OmtjG30jSNq9kOF5bBTk1hOTGPH4
K/AY0jzT6OpHfXU6ytlFsI47ZKsnTUhipGsKucQ1CXlyirndZ3V3k70YaooZ55rG
aIoAWlx2H0J7sAHmqS29N9jV9mo135d+d+TdLBXI0PXtiHzE9IPaX+ctdSUrPnp+
TwR99lxglpIG6hLuvOMAaxiqFBB/Jf3XJ8OBakfS6nHrWH2WqQxRbiITl0irkQoz
pwNEF2Bv0+Jvs1UFEdVGz5a8xexQHst/RmKrtHLct3iOCvBNqoAQRbvWvBhPjO/p
V5cYeUljZ5wpHyFkaEViClaVWqa6PIsyLqmyjsruPCWlURLsQoQxABcL8bwxX7UT
hM6CtH6tGlYZ85RIzRifIm2oudzV5l+8oRgFr9yVcwyOFT6JCioqkwldW52P1pk/
/SnuexC6LYqqDuHUs5NnokzzpfS6QaWfTY5P5tz4KHJfsjDIktly3mKVfY0fSPVV
okdGpcUzvz2hq1fqjxB6MlB/1vtk0bImfcsoxBmF7H+4E9ZN1sX/tSb0KQARAQAB
tCZPcGVuIFJvYm90aWNzIDxpbmZvQG9zcmZvdW5kYXRpb24ub3JnPokCVAQTAQoA
PhYhBMHPbjHmut6IaLFytPQu1vurF8ZUBQJc7yaWAhsDBQkDwmcABQsJCAcCBhUK
CQgLAgQWAgMBAh4BAheAAAoJEPQu1vurF8ZUkhIP/RbZY1ErvCEUy8iLJm9aSpLQ
nDZl5xILOxyZlzpg+Ml5bb0EkQDr92foCgcvLeANKARNCaGLyNIWkuyDovPV0xZJ
rEy0kgBrDNb3++NmdI/+GA92pkedMXXioQvqdsxUagXAIB/sNGByJEhs37F05AnF
vZbjUhceq3xTlvAMcrBWrgB4NwBivZY6IgLvl/CRQpVYwANShIQdbvHvZSxRonWh
NXr6v/Wcf8rsp7g2VqJ2N2AcWT84aa9BLQ3Oe/SgrNx4QEhA1y7rc3oaqPVu5ZXO
K+4O14JrpbEZ3Xs9YEjrcOuEDEpYktA8qqUDTdFyZrxb9S6BquUKrA6jZgT913kj
J4e7YAZobC4rH0w4u0PrqDgYOkXA9Mo7L601/7ZaDJob80UcK+Z12ZSw73IgBix6
DiJVfXuWkk5PM2zsFn6UOQXUNlZlDAOj5NC01V0fJ8P0v6GO9YOSSQx0j5UtkUbR
fp/4W7uCPFvwAatWEHJhlM3sQNiMNStJFegr56xQu1a/cbJH7GdbseMhG/f0BaKQ
qXCI3ffB5y5AOLc9Hw7PYiTFQsuY1ePRhE+J9mejgWRZxkjAH/FlAubqXkDgterC
h+sLkzGf+my2IbsMCuc+3aeNMJ5Ej/vlXefCH/MpPWAHCqpQhe2DET/jRSaM53US
AHNx8kw4MPUkxExgI7Sd
=4Ofr
-----END PGP PUBLIC KEY BLOCK-----
"""


class ColconInvalidSystemDependencyError(errors.SnapcraftError):
    fmt = (
        "Package {dependency!r} isn't a valid system dependency. Did you "
        "forget to add it to colcon-packages? If not, add the Ubuntu package "
        "containing it to stage-packages until you can get it into the rosdep "
        "database."
    )

    def __init__(self, dependency):
        super().__init__(dependency=dependency)


class ColconUnsupportedDependencyTypeError(errors.SnapcraftError):
    fmt = (
        "Package {dependency!r} resolved to an unsupported type of "
        "dependency: {dependency_type!r}."
    )

    def __init__(self, dependency_type, dependency):
        super().__init__(dependency_type=dependency_type, dependency=dependency)


class ColconWorkspaceIsRootError(errors.SnapcraftError):
    fmt = (
        "colcon-source-space cannot be the root of the colcon workspace; use a "
        "subdirectory."
    )


class ColconAptDependencyFetchError(errors.SnapcraftError):
    fmt = "Failed to fetch apt dependencies: {message}"

    def __init__(self, message):
        super().__init__(message=message)


class ColconPackagePathNotFoundError(errors.SnapcraftError):
    fmt = "Failed to find package path: {path!r}"

    def __init__(self, path):
        super().__init__(path=path)


class ColconPluginBaseError(errors.PluginBaseError):
    fmt = (
        "The colcon plugin (used by part {part_name!r}) does not support using base "
        "{base!r} with rosdistro {rosdistro!r}."
    )

    def __init__(self, part_name, base, rosdistro):
        super(errors.PluginBaseError, self).__init__(
            part_name=part_name, base=base, rosdistro=rosdistro
        )


class ColconPlugin(snapcraft.BasePlugin):
    @classmethod
    def schema(cls):
        schema = super().schema()

        schema["properties"]["colcon-rosdistro"] = {
            "type": "string",
            "default": "crystal",
            "enum": list(_ROSDISTRO_TO_BASE_MAP.keys()),
        }

        schema["properties"]["colcon-packages"] = {
            "type": "array",
            "minitems": 1,
            "uniqueItems": True,
            "items": {"type": "string"},
        }

        schema["properties"]["colcon-source-space"] = {
            "type": "string",
            "default": "src",
        }

        schema["properties"]["colcon-cmake-args"] = {
            "type": "array",
            "minitems": 1,
            "items": {"type": "string"},
            "default": [],
        }

        schema["properties"]["colcon-catkin-cmake-args"] = {
            "type": "array",
            "minitems": 1,
            "items": {"type": "string"},
            "default": [],
        }

        schema["properties"]["colcon-ament-cmake-args"] = {
            "type": "array",
            "minitems": 1,
            "items": {"type": "string"},
            "default": [],
        }

        schema["properties"]["colcon-packages-ignore"] = {
            "type": "array",
            "minitems": 1,
            "uniqueItems": True,
            "items": {"type": "string"},
            "default": [],
        }

        schema["required"] = ["source"]

        return schema

    @classmethod
    def get_pull_properties(cls):
        # Inform Snapcraft of the properties associated with pulling. If these
        # change in the YAML Snapcraft will consider the pull step dirty.
        return ["colcon-packages", "colcon-source-space", "colcon-rosdistro"]

    @classmethod
    def get_build_properties(cls):
        # Inform Snapcraft of the properties associated with building. If these
        # change in the YAML Snapcraft will consider the build step dirty.
        return [
            "colcon-cmake-args",
            "colcon-catkin-cmake-args",
            "colcon-ament-cmake-args",
            "colcon-packages-ignore",
        ]

    @property
    def PLUGIN_STAGE_SOURCES(self) -> PackageManagement:
        codename = _BASE_TO_UBUNTU_RELEASE_MAP[self.project.info.get_build_base()]
        source = f"deb http://repo.ros2.org/ubuntu/main {codename} main"

        repo = Repository(source=source, gpg_public_key=_ROS2_KEY)
        return PackageManagement(repositories=[repo])

    def __init__(self, name, options, project):
        super().__init__(name, options, project)
        self.out_of_source_build = True

        self._rosdistro = options.colcon_rosdistro
        if project.info.get_build_base() != _ROSDISTRO_TO_BASE_MAP[self._rosdistro]:
            raise ColconPluginBaseError(
                self.name, project.info.get_build_base(), self._rosdistro
            )

        if self._rosdistro in _EOL_ROSDISTROS:
            logger.warning(
                "The {!r} ROS distro has reached end-of-life and is no longer supported. Use at your own risk.".format(
                    self._rosdistro
                )
            )

        # Beta warning. Remove this comment and warning once plugin is stable.
        logger.warning(
            "The colcon plugin is currently in beta, its API may break. Use at your "
            "own risk."
        )

        # Always fetch colcon in order to build the workspace
        self.stage_packages.append("python3-colcon-common-extensions")
        self.build_packages.extend(
            [
                "python3-colcon-common-extensions",
                "python3-rosdep",
                "python3-wstool",
                "python3-rosinstall",
                f"ros-{self._rosdistro}-ros-base",
            ]
        )

        # Get a unique set of packages
        self._packages = None
        if options.colcon_packages is not None:
            self._packages = set(options.colcon_packages)

        # The path created via the `source` key (or a combination of `source`
        # and `source-subdir` keys) needs to point to a valid Colcon workspace
        # containing another subdirectory called the "source space." By
        # default, this is a directory named "src," but it can be remapped via
        # the `source-space` key. It's important that the source space is not
        # the root of the Colcon workspace, since Colcon won't work that way
        # and it'll create a circular link that causes rosdep to hang.
        if self.options.source_subdir:
            self._ros_package_path = os.path.join(
                self.sourcedir,
                self.options.source_subdir,
                self.options.colcon_source_space,
            )
        else:
            self._ros_package_path = os.path.join(
                self.sourcedir, self.options.colcon_source_space
            )

        if os.path.abspath(self.sourcedir) == os.path.abspath(self._ros_package_path):
            raise ColconWorkspaceIsRootError()

    def get_build_environment(self) -> Dict[str, str]:
        env = super().get_build_environment()

        env.update(
            {
                "AMENT_PYTHON_EXECUTABLE": "/usr/bin/python3",
                "COLCON_PYTHON_EXECUTABLE": "/usr/bin/python3",
                "SNAP_COLCON_ROOT": "/",
                "ROS_DISTRO": self._rosdistro,
                "ROS_PACKAGE_PATH": self.builddir,
                "ROS_PYTHON_VERSION": "3",
                "SNAPCRAFT_COLCON_CMAKE_ARGS": "--cmake-args -DCMAKE_BUILD_TYPE=release",  # --cmake-args <...>
                "SNAPCRAFT_COLCON_AMENT_ARGS": "",  # --ament-cmake-args <...>
                "SNAPCRAFT_COLCON_CATKIN_ARGS": "",  # --catkin-cmake-args <...>
                "SNAPCRAFT_COLCON_PACKAGES_IGNORE_ARGS": "",  # --packages-ignore <...>
                "SNAPCRAFT_COLCON_PACKAGES_SELECT_ARGS": "",  # --packages-select <...>
                "SNAPCRAFT_COLCON_BUILD_BASE": self.builddir,
                "SNAPCRAFT_COLCON_BASE_PATHS": "$SNAPCRAFT_PART_SRC_SUBDIR",
                "SNAPCRAFT_COLCON_INSTALL_BASE": "$SNAPCRAFT_PART_INSTALL/opt/ros/snap",
            }
        )

        return env

    def get_build_commands(self) -> List[str]:
        return [
            "source /opt/ros/$ROS_DISTRO/setup.bash",
            "if [ ! -f /etc/ros/rosdep/sources.list.d/20-default.list ]; then sudo rosdep init; fi",
            "rosdep update",
            "rosdep install --from-paths $SNAPCRAFT_COLCON_BASE_PATHS -y -r",
            "colcon build --merge-install --build-base $SNAPCRAFT_COLCON_BUILD_BASE --base-paths $SNAPCRAFT_COLCON_BASE_PATHS --install-base $SNAPCRAFT_COLCON_INSTALL_BASE --parallel-workers=$SNAPCRAFT_PARALLEL_BUILD_COUNT $SNAPCRAFT_ROS_CMAKE_ARGS $SNAPCRAFT_ROS_AMENT_ARGS $SNAPCRAFT_ROS_CATKIN_CMAKE_ARGS $SNAPCRAFT_ROS_PACKAGES_IGNORE_ARGS $SNAPCRAFT_ROS_PACKAGES_SELECT_ARGS",
        ]

    def env(self, root):
        """Runtime environment for ROS binaries and services."""

        env = [
            f'AMENT_PYTHON_EXECUTABLE="{root}/usr/bin/python3"',
            f'COLCON_PYTHON_EXECUTABLE="{root}/usr/bin/python3"',
            f'SNAP_COLCON_ROOT="{root}"',
        ]

        # Each of these lines is prepended with an `export` when the environment is
        # actually generated. In order to inject real shell code we have to hack it in
        # by appending it on the end of an item already in the environment.
        # FIXME: There should be a better way to do this. LP: #1792034
        env[-1] = env[-1] + "\n\n" + self._source_setup_sh(root)
        return env

    def _source_setup_sh(self, root):
        # TODO: install this as file

        # We need to source ROS's setup.sh at this point. However, it accepts
        # arguments (thus will parse $@), and we really don't want it to, since
        # $@ in this context will be meant for the app being launched
        # (LP: #1660852). So we'll backup all args, source the setup.sh, then
        # restore all args for the wrapper's `exec` line.
        return textwrap.dedent(
            """
            # Shell quote arbitrary string by replacing every occurrence of '
            # with '\\'', then put ' at the beginning and end of the string.
            # Prepare yourself, fun regex ahead.
            quote()
            {{
                for i; do
                    printf %s\\\\n "$i" | sed "s/\'/\'\\\\\\\\\'\'/g;1s/^/\'/;\$s/\$/\' \\\\\\\\/"
                done
                echo " "
            }}

            BACKUP_ARGS=$(quote "$@")
            set --

            # First, source the upstream ROS underlay
            if [ -f "/opt/ros/{ros_distro}/setup.sh" ]; then
                . "/opt/ros/{ros_distro}/setup.sh"
            fi

            # Then source the overlay
            if [ -f "/opt/ros/snap/setup.sh" ]; then
                . "/opt/ros/snap/setup.sh"
            fi

            eval "set -- $BACKUP_ARGS"
        """.format(
                ros_distro=self._rosdistro
            )
        )

    def build(self):
        super().build()

        mangling.rewrite_python_shebangs(self.installdir)