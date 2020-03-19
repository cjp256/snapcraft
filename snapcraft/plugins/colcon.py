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

import click
import contextlib
import collections
import glob
import os
import logging
import re
import shutil
import subprocess
import sys
import textwrap
from typing import Dict, List, Set, Tuple

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

# XXX: will be removed with global apt config
_ROS2_REPO = """
deb http://repo.ros2.org/ubuntu/main bionic main
deb http://${prefix}.ubuntu.com/${suffix}/ bionic main universe
deb http://${prefix}.ubuntu.com/${suffix}/ bionic-updates main universe
deb http://${prefix}.ubuntu.com/${suffix}/ bionic-security main universe
deb http://${security}.ubuntu.com/${suffix}/ bionic-security main universe
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

        if self.options.colcon_packages_ignore:
            args = ["--packages-ignore", *self.options.colcon_packages_ignore]
            packages_ignore_args = " ".join(args)
        else:
            packages_ignore_args = ""

        if self._packages:
            args = ["--packages-select $SNAPCRAFT_COLCON_PACKAGES"]
            packages = " ".join(self._packages)
            packages_select_args = " ".join(args)

        else:
            packages = ""
            packages_select_args = ""

        if self.options.colcon_ament_cmake_args:
            args = ["--ament-cmake-args", *self.options.colcon_ament_cmake_args]
            ament_cmake_args = " ".join(args)
        else:
            ament_cmake_args = ""

        if self.options.colcon_catkin_cmake_args:
            args = ["--catkin-cmake-args", *self.options.colcon_catkin_cmake_args]
            catkin_cmake_args = " ".join(args)
        else:
            catkin_cmake_args = ""

        build_type = "Release"
        if "debug" in self.options.build_attributes:
            build_type = "Debug"

        cmake_args = [
            "--cmake-args",
            "-DCMAKE_BUILD_TYPE={}",
            *self.options.colcon_cmake_args,
        ]

        env.update(
            {
                "AMENT_PYTHON_EXECUTABLE": "/usr/bin/python3",
                "COLCON_PYTHON_EXECUTABLE": "/usr/bin/python3",
                "SNAP_COLCON_ROOT": "/",
                "ROS_DISTRO": self._rosdistro,
                "ROS_PACKAGE_PATH": self._ros_package_path,
                "ROS_PYTHON_VERSION": "3",
                "SNAPCRAFT_COLCON_CMAKE_ARGS": cmake_args,
                "SNAPCRAFT_COLCON_AMENT_CMAKE_ARGS": ament_cmake_args,
                "SNAPCRAFT_COLCON_CATKIN_CMAKE_ARGS": catkin_cmake_args,
                "SNAPCRAFT_COLCON_PACKAGES_IGNORE_ARGS": packages_ignore_args,
                "SNAPCRAFT_COLCON_PACKAGES_SELECT_ARGS": packages_select_args,
                "SNAPCRAFT_COLCON_BUILD_BASE": self.builddir,
                "SNAPCRAFT_COLCON_INSTALL_BASE": "$SNAPCRAFT_PART_INSTALL/opt/ros/snap",
                "SNAPCRAFT_COLCON_PACKAGES": packages,
            }
        )

        return env

    def get_build_commands(self) -> List[str]:
        return [
            "source /opt/ros/$ROS_DISTRO/setup.bash",
            "if [ ! -f /etc/ros/rosdep/sources.list.d/20-default.list ]; then sudo rosdep init; fi",
            "rosdep update",
            f"{sys.executable} -m snapcraft.plugins.colcon stage-dependencies || bash",
            "rosdep install --from-paths $ROS_PACKAGE_PATH -y -r",
            "colcon build --merge-install --build-base $SNAPCRAFT_COLCON_BUILD_BASE --base-paths $ROS_PACKAGE_PATH --install-base $SNAPCRAFT_COLCON_INSTALL_BASE --parallel-workers=$SNAPCRAFT_PARALLEL_BUILD_COUNT $SNAPCRAFT_ROS_CMAKE_ARGS $SNAPCRAFT_ROS_AMENT_ARGS $SNAPCRAFT_ROS_CATKIN_CMAKE_ARGS $SNAPCRAFT_ROS_PACKAGES_IGNORE_ARGS $SNAPCRAFT_ROS_PACKAGES_SELECT_ARGS",
            f"{sys.executable} -m snapcraft.plugins.colcon rewrite-prefixes || bash",
            "sed -i 's|^COLCON_CURRENT_PREFIX=\"/opt.*|COLCON_CURRENT_PREFIX=\"$SNAP_COLCON_ROOT/opt/ros/'$ROS_DISTRO'\"|g' $SNAPCRAFT_COLCON_INSTALL_BASE/setup.sh",
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
        print("source setup:", root)

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
            if [ -f "{root}/opt/ros/{ros_distro}/setup.sh" ]; then
                . "{root}/opt/ros/{ros_distro}/setup.sh"
            fi

            # Then source the overlay
            if [ -f "{root}/opt/ros/snap/setup.sh" ]; then
                . "{root}/opt/ros/snap/setup.sh"
            fi

            eval "set -- $BACKUP_ARGS"
        """.format(
                root=root, ros_distro=self._rosdistro
            )
        )


def _rewrite_cmake_paths(new_path_callable, install_dir: str, ros_underlay_dir: str):
    def _rewrite_paths(match):
        paths = match.group(1).strip().split(";")
        for i, path in enumerate(paths):
            # Offer the opportunity to rewrite this path if it's absolute.
            if os.path.isabs(path):
                paths[i] = new_path_callable(path)

        return '"' + ";".join(paths) + '"'

    # Looking for any path-like string
    file_utils.replace_in_file(
        ros_underlay_dir,
        re.compile(r".*Config.cmake$"),
        re.compile(r'"(.*?/.*?)"'),
        _rewrite_paths,
    )


def _fix_prefixes(install_dir: str):
    installdir_pattern = re.compile(r"^{}".format(install_dir))
    new_prefix = "$SNAP_COLCON_ROOT"

    def _rewrite_prefix(match):
        # Group 1 is the variable definition, group 2 is the path, which we may need
        # to modify.
        path = match.group(3).strip(" \n\t'\"")

        # Bail early if this isn't even a path, or if it's already been rewritten
        if os.path.sep not in path or new_prefix in path:
            return match.group()

        # If the path doesn't start with the installdir, then it needs to point to
        # the underlay given that the upstream ROS packages are expecting to be in
        # /opt/ros/.
        if not path.startswith(install_dir):
            path = os.path.join(new_prefix, path.lstrip("/"))

        return match.expand(
            '\\1\\2"{}"\\4'.format(installdir_pattern.sub(new_prefix, path))
        )

    # Set the AMENT_CURRENT_PREFIX throughout to the in-snap prefix
    snapcraft.file_utils.replace_in_file(
        install_dir,
        re.compile(r""),
        re.compile(r"(\${)(AMENT_CURRENT_PREFIX:=)(.*)(})"),
        _rewrite_prefix,
    )

    # Set the COLCON_CURRENT_PREFIX (if it's in the installdir) to the in-snap
    # prefix
    snapcraft.file_utils.replace_in_file(
        install_dir,
        re.compile(r""),
        re.compile(r"()(COLCON_CURRENT_PREFIX=)(['\"].*{}.*)()".format(install_dir)),
        _rewrite_prefix,
    )

    # Set the _colcon_prefix_sh_COLCON_CURRENT_PREFIX throughout to the in-snap
    # prefix
    snapcraft.file_utils.replace_in_file(
        install_dir,
        re.compile(r""),
        re.compile(r"()(_colcon_prefix_sh_COLCON_CURRENT_PREFIX=)(.*)()"),
        _rewrite_prefix,
    )

    # Set the _colcon_package_sh_COLCON_CURRENT_PREFIX throughout to the in-snap
    # prefix
    snapcraft.file_utils.replace_in_file(
        install_dir,
        re.compile(r""),
        re.compile(r"()(_colcon_package_sh_COLCON_CURRENT_PREFIX=)(.*)()"),
        _rewrite_prefix,
    )

    # Set the _colcon_prefix_chain_sh_COLCON_CURRENT_PREFIX throughout to the in-snap
    # prefix
    snapcraft.file_utils.replace_in_file(
        install_dir,
        re.compile(r""),
        re.compile(r"()(_colcon_prefix_chain_sh_COLCON_CURRENT_PREFIX=)(.*)()"),
        _rewrite_prefix,
    )

    # Set the _colcon_python_executable throughout to use the in-snap python
    snapcraft.file_utils.replace_in_file(
        install_dir,
        re.compile(r""),
        re.compile(r"()(_colcon_python_executable=)(.*)()"),
        _rewrite_prefix,
    )


def stage_apt_packages(*, install_dir: str, packages: List[str]) -> None:
    print(packages)

    ubuntudir = os.path.join(install_dir, "..", "ubuntu")
    os.makedirs(ubuntudir, exist_ok=True)

    logger.info("Preparing to fetch apt dependencies...")
    ubuntu = repo.Ubuntu(
        ubuntudir,
        sources=_ROS2_REPO,
        keyrings=[_ROS_KEYRING_PATH],
        project_options=None,
    )

    logger.info("Fetching apt dependencies...")
    try:
        ubuntu.get(packages)
    except repo.errors.PackageNotFoundError as e:
        raise ColconAptDependencyFetchError(e.message)

    logger.info("Installing apt dependencies...")
    ubuntu.unpack(install_dir)


def stage_python_packages(
    *, part_dir: str, install_dir: str, stage_dir: str, packages: List[str]
) -> None:
    pip = _python.Pip(
        python_major_version="3",  # ROS2 uses python3
        part_dir=part_dir,
        install_dir=install_dir,
        stage_dir=stage_dir,
    )

    pip.setup()

    logger.info("Fetching pip dependencies...")
    pip.download(packages)

    logger.info("Installing pip dependencies...")
    pip.install(packages)


def get_rosdep_dependencies() -> Tuple[Set[str], Set[str]]:
    apt_packages: Set[str] = set()
    pip_packages: Set[str] = set()
    ros_distro = os.environ["ROS_DISTRO"]

    deps = (
        subprocess.check_output(["rosdep", "keys", "-a"])
        .decode("utf8")
        .strip()
        .split("\n")
    )

    for dep in deps:
        resolve_output = (
            subprocess.check_output(
                ["rosdep", "resolve", dep, "--rosdistro", ros_distro]
            )
            .decode("utf8")
            .strip()
        )

        parsed = _ros.rosdep._parse_dependencies(resolve_output)
        apt_packages |= parsed.get("apt", set())
        pip_packages |= parsed.get("pip", set())

    return apt_packages, pip_packages


@click.group()
def plugin_cli():
    pass


@plugin_cli.command()
def rewrite_prefixes():
    install_dir = os.environ["SNAPCRAFT_PART_INSTALL"]
    ros_distro = os.environ["ROS_DISTRO"]
    stage_dir = os.environ["SNAPCRAFT_STAGE"]

    ros_underlay_dir = os.path.join(install_dir, "opt", "ros", ros_distro)

    # Fix all shebangs to use the in-snap python.
    mangling.rewrite_python_shebangs(install_dir)

    # We've finished the build, but we need to make sure we turn the cmake
    # files back into something that doesn't include our installdir. This
    # way it's usable from the staging area, and won't clash with the same
    # file coming from other parts.
    pattern = re.compile(r"^{}".format(install_dir))

    def _new_path(path):
        return pattern.sub("$ENV{SNAPCRAFT_STAGE}", path)

    _rewrite_cmake_paths(_new_path, install_dir, ros_underlay_dir)

    # Rewrite prefixes for both the underlay and overlay.
    _fix_prefixes(install_dir)

    # TODO: Better way to conditionally set this?
    path_glob = os.path.join(install_dir, "lib", "python3*", "site-packages")
    if glob.glob(path_glob):
        _python.generate_sitecustomize(
            "3", stage_dir=stage_dir, install_dir=install_dir
        )


@plugin_cli.command()
def stage_dependencies():
    install_dir = os.environ["SNAPCRAFT_PART_INSTALL"]
    part_dir = os.path.join(install_dir, "..")
    stage_dir = os.environ["SNAPCRAFT_STAGE"]

    apt_packages, pip_packages = get_rosdep_dependencies()

    if apt_packages:
        stage_apt_packages(install_dir=install_dir, packages=apt_packages)

    if pip_packages:
        stage_python_packages(
            install_dir=install_dir,
            part_dir=part_dir,
            stage_dir=stage_dir,
            packages=pip_packages,
        )


if __name__ == "__main__":
    plugin_cli()
