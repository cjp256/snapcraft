# -*- Mode:Python; indent-tabs-mode:nil; tab-width:4 -*-
#
# Copyright (C) 2020 Canonical Ltd
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

# Import types and tell flake8 to ignore the "unused" List.

from typing import Any, Dict, Tuple

from ._extension import Extension


class ExtensionImpl(Extension):
    @staticmethod
    def get_supported_bases() -> Tuple[str, ...]:
        return ("core18",)

    @staticmethod
    def get_supported_confinement() -> Tuple[str, ...]:
        return ("strict", "devmode")

    def __init__(self, *, extension_name: str, yaml_data: Dict[str, Any]) -> None:
        super().__init__(extension_name=extension_name, yaml_data=yaml_data)

        self.root_snippet = {
            "environment": {"SNAP_DESKTOP_RUNTIME": "$SNAP/gnome-platform"},
            "layout": {},
        }

        self.app_snippet = {
            "command-chain": ["snap/command-chain/colcon-launch"],
            "environment": {
                "AMENT_PYTHON_EXECUTABLE": "$SNAP/usr/bin/python3",
                "COLCON_PYTHON_EXECUTABLE": "$SNAP/usr/bin/python3",
                "SNAP_COLCON_ROOT": "$SNAP",
            },
        }

        self.parts = {
            "gnome-extension": {
                "source": "$SNAPCRAFT_EXTENSIONS_DIR/desktop",
                "source-subdir": "gnome",
                "plugin": "make",
                "build-packages": ["libgtk-3-dev"],
            }
        }
