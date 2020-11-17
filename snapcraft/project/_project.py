# -*- Mode:Python; indent-tabs-mode:nil; tab-width:4 -*-
#
# Copyright (C) 2018-2020 Canonical Ltd
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

import hashlib
import os
from datetime import datetime
from pathlib import Path

from snapcraft.internal import states
from snapcraft.internal.deprecations import handle_deprecation_notice
from snapcraft.internal.meta.snap import Snap
from typing import Set
from ._project_options import ProjectOptions
from ._project_info import ProjectInfo  # noqa: F401
from snapcraft.internal.project_loader._env import build_env_for_stage, environment_to_replacements, runtime_env
from snapcraft.internal.pluginhandler._part_environment import (
    get_snapcraft_global_environment,
)


class Project(ProjectOptions):
    """All details around building a project concerning the build environment
    and the snap being built."""

    def __init__(
        self,
        *,
        target_deb_arch=None,
        debug=False,
        snapcraft_yaml_file_path=None,
        work_dir: str = None,
        is_managed_host: bool = False
    ) -> None:

        project_dir = os.getcwd()
        if is_managed_host:
            work_dir = os.path.expanduser("~")
        else:
            work_dir = project_dir

        super().__init__(target_deb_arch, debug, work_dir=work_dir)

        # This here check is mostly for backwards compatibility with the
        # rest of the code base.
        if snapcraft_yaml_file_path is None:
            self.info: ProjectInfo = None  # type: ignore

        else:
            self.info = ProjectInfo(snapcraft_yaml_file_path=snapcraft_yaml_file_path)

        self._is_managed_host = is_managed_host
        self._project_dir = project_dir
        self._work_dir = work_dir

        self.local_plugins_dir = self._get_local_plugins_dir()
        self._start_time = datetime.utcnow()

        # XXX: (Re)set by Config because it mangles source data.
        # Ideally everywhere wold converge to operating on snap_meta, and ww
        # would only need to initialize it once (properly).
        self._snap_meta = Snap()

    def _get_build_base(self) -> str:
        """
        Return name for type base or the base otherwise build-base is set
        """

        # In case snap_meta is not yet populated, lookup base in info.
        if self.info:
            if self.info.build_base:
                return self.info.build_base
            if self.info.base:
                return self.info.base

        return self._snap_meta.get_build_base()

    def _get_project_directory_hash(self) -> str:
        m = hashlib.sha1()
        m.update(self._project_dir.encode())
        return m.hexdigest()[:6]

    def _get_content_snaps(self) -> Set[str]:
        """Return the set of content snaps from snap_meta."""
        return set(
            [
                x.provider
                for x in self._snap_meta.get_content_plugs()
                if x.provider is not None
            ]
        )

    def _get_provider_content_dirs(self) -> Set[str]:
        """Return the set installed provider content directories."""
        return self._snap_meta.get_provider_content_directories()

    def _get_snapcraft_assets_dir(self) -> str:
        # Many test cases don't set the yaml file path and assume the default dir
        if not self.info:
            return os.path.join(self._project_dir, "snap")

        if self.info.snapcraft_yaml_file_path.endswith(
            os.path.join("build-aux", "snap", "snapcraft.yaml")
        ):
            return os.path.join(self._project_dir, "build-aux", "snap")
        else:
            return os.path.join(self._project_dir, "snap")

    def _get_keys_path(self) -> Path:
        # Directory containing <KEY_ID>.asc keys for use with
        # package-repositories, relative to 'snap' assets.
        return Path(self._get_snapcraft_assets_dir(), "keys")

    def _get_local_plugins_dir(self) -> str:
        deprecated_plugins_dir = os.path.join(self._parts_dir, "plugins")
        if os.path.exists(deprecated_plugins_dir):
            handle_deprecation_notice("dn2")
            return deprecated_plugins_dir
        else:
            assets_dir = self._get_snapcraft_assets_dir()
            return os.path.join(assets_dir, "plugins")

    def _get_global_state_file_path(self) -> str:
        if self._is_managed_host:
            state_file_path = os.path.join(self._work_dir, "state")
        else:
            state_file_path = os.path.join(self._parts_dir, ".snapcraft_global_state")

        return state_file_path

    def _get_start_time(self) -> datetime:
        """Returns the timestamp for when a snapcraft project was loaded."""
        return self._start_time

    def get_project_state(self, step: steps.Step):
        """Returns a dict of states for the given step of each part."""

        state = {}
        for part in self.parts.all_parts:
            state[part.name] = states.get_state(part.part_state_dir, step)

        return state

    def stage_env(self):
        stage_dir = self.stage_dir
        env = []

        env += runtime_env(stage_dir, self.arch_triplet)
        env += build_env_for_stage(
            stage_dir, self.data["name"], self.arch_triplet
        )
        for part in self.parts.all_parts:
            env += part.env(stage_dir)

        return env

    def snap_env(self):
        prime_dir = self.project.prime_dir
        env = []

        env += runtime_env(prime_dir, self.arch_triplet)
        dependency_paths = set()
        for part in self.parts.all_parts:
            env += part.env(prime_dir)
            dependency_paths |= part.get_primed_dependency_paths()

        # Dependency paths are only valid if they actually exist. Sorting them
        # here as well so the LD_LIBRARY_PATH is consistent between runs.
        dependency_paths = sorted(
            {path for path in dependency_paths if os.path.isdir(path)}
        )

        if dependency_paths:
            # Add more specific LD_LIBRARY_PATH from the dependencies.
            env.append(
                'LD_LIBRARY_PATH="' + ":".join(dependency_paths) + ':$LD_LIBRARY_PATH"'
            )

        return env

    def project_env(self):
        return [
            '{}="{}"'.format(variable, value)
            for variable, value in get_snapcraft_global_environment(
                self
            ).items()
        ]
