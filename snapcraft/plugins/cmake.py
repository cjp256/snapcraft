# -*- Mode:Python; indent-tabs-mode:nil; tab-width:4 -*-
#
# Copyright (C) 2015-2016 Canonical Ltd
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

"""The cmake plugin is useful for building cmake based parts.

These are projects that have a CMakeLists.txt that drives the build.
The plugin requires a CMakeLists.txt in the root of the source tree.

If the part has a list of build-snaps listed, the part will be set up in
such a way that the paths to those snaps are used as paths for find_package
and find_library by use of `CMAKE_FIND_ROOT_PATH``.
This plugin uses the common plugin keywords as well as those for "sources".
For more information check the 'plugins' topic for the former and the
'sources' topic for the latter.

Additionally, this plugin uses the following plugin-specific keywords:

    - configflags:
      (list of strings)
      configure flags to pass to the build using the common cmake semantics.
"""

import logging
import os
from typing import Dict, List, Optional

import snapcraft
from snapcraft.internal import errors


logger = logging.getLogger(name=__name__)


class _Flag:
    def __str__(self) -> str:
        if self.value is None:
            flag = self.name
        else:
            flag = "{}={}".format(self.name, self.value)
        return flag

    def __init__(self, flag: str) -> None:
        parts = flag.split("=")
        self.name = parts[0]
        try:
            self.value: Optional[str] = parts[1]
        except IndexError:
            self.value = None


class CMakePlugin(snapcraft.BasePlugin):
    @classmethod
    def schema(cls):
        schema = super().schema()
        schema["properties"]["configflags"] = {
            "type": "array",
            "minitems": 1,
            "uniqueItems": True,
            "items": {"type": "string"},
            "default": [],
        }
        # For backwards compatibility
        schema["properties"]["make-parameters"] = {
            "type": "array",
            "minitems": 1,
            "uniqueItems": True,
            "items": {"type": "string"},
            "default": [],
        }
        schema["required"] = ["source"]

        return schema

    @classmethod
    def get_build_properties(cls):
        # Inform Snapcraft of the properties associated with building. If these
        # change in the YAML Snapcraft will consider the build step dirty.
        return super().get_build_properties() + ["configflags"]

    def __init__(self, name, options, project):
        super().__init__(name, options, project)
        self.build_packages.append("cmake")
        self.out_of_source_build = True

        if project.info.get_build_base() not in ("core", "core16", "core18"):
            raise errors.PluginBaseError(
                part_name=self.name, base=project.info.get_build_base()
            )

        if options.make_parameters:
            logger.warning("make-paramaters is deprecated, ignoring.")

    def _get_processed_flags(self) -> List[str]:
        # Return the original if no build_snaps are in options.
        if not self.options.build_snaps:
            return self.options.configflags

        build_snap_paths = [
            os.path.join(os.path.sep, "snap", snap_name.split("/")[0], "current")
            for snap_name in self.options.build_snaps
        ]

        flags = [_Flag(f) for f in self.options.configflags]
        for flag in flags:
            if flag.name == "-DCMAKE_FIND_ROOT_PATH":
                flag.value = "{};{}".format(flag.value, ";".join(build_snap_paths))
                break
        else:
            flags.append(
                _Flag("-DCMAKE_FIND_ROOT_PATH={}".format(";".join(build_snap_paths)))
            )

        return [str(f) for f in flags]

    def get_build_environment(self) -> Dict[str, str]:
        env = super().get_build_environment()

        env.update(
            {
                "CMAKE_INCLUDE_PATH": ":".join(
                    [
                        "$CMAKE_INCLUDE_PATH",
                        "$SNAPCRAFT_STAGE/include",
                        "$SNAPCRAFT_STAGE/usr/include",
                        "$SNAPCRAFT_STAGE/include/$SNAPCRAFT_ARCH_TRIPLET",
                        "$SNAPCRAFT_STAGE/usr/include/$SNAPCRAFT_ARCH_TRIPLET",
                    ]
                ),
                "CMAKE_LIBRARY_PATH": ":".join(
                    [
                        "$CMAKE_LIBRARY_PATH",
                        "$SNAPCRAFT_STAGE/qlib",
                        "$SNAPCRAFT_STAGE/usr/lib",
                        "$SNAPCRAFT_STAGE/lib/$SNAPCRAFT_ARCH_TRIPLET",
                        "$SNAPCRAFT_STAGE/usr/lib/$SNAPCRAFT_ARCH_TRIPLET",
                    ]
                ),
                "CMAKE_PREFIX_PATH": "$CMAKE_PREFIX_PATH:$SNAPCRAFT_STAGE",
                "SNAPCRAFT_CMAKE_FLAGS": " ".join(self._get_processed_flags()),
            }
        )

        return env

    def get_build_commands(self) -> List[str]:
        return [
            "cmake $SNAPCRAFT_PART_SRC_SUBDIR -DCMAKE_INSTALL_PREFIX= $SNAPCRAFT_CMAKE_FLAGS",
            "cmake --build . -- -j$SNAPCRAFT_PARALLEL_COUNT",
            "DESTDIR=$SNAPCRAFT_PART_INSTALL/ cmake --build . --target install",
        ]
