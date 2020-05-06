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

import contextlib
import logging
import pickle
from pathlib import Path
from typing import List, Optional, Set

from .apt_cache import DebPackage

logger = logging.getLogger(__name__)


class AptStageCacheDbEntry:
    def __init__(
        self,
        *,
        package_names: Set[str],
        filtered_names: Set[str],
        packages: List[DebPackage],
    ) -> None:
        self.package_names = package_names
        self.filtered_names = filtered_names
        self.packages = packages


class AptStageCacheDb:
    def __init__(self, *, stage_cache: Path):
        self._pickle_db = stage_cache / "snapcraft-pickle.db"

    def _purge(self):
        with contextlib.suppress(FileNotFoundError):
            self._pickle_db.unlink()
            return None

    def _load(self) -> Set[AptStageCacheDbEntry]:
        results: Set[AptStageCacheDbEntry] = set()

        try:
            with open(self._pickle_db, "rb") as fd:
                results = pickle.load(fd)
        except pickle.UnpicklingError:
            self._purge()
            return results
        except FileNotFoundError:
            return results

        # Sanity check format.
        if not isinstance(results, set) or any(
            not isinstance(result, AptStageCacheDbEntry) for result in results
        ):
            self._purge()
            return set()

        return results

    def _save(self, entries: Set[AptStageCacheDbEntry]) -> None:
        self._pickle_db.parent.mkdir(exist_ok=True, parents=True)
        with open(self._pickle_db, "wb") as fd:
            pickle.dump(entries, fd)

    def find(
        self, *, package_names: Set[str], filtered_names: Set[str]
    ) -> Optional[List[DebPackage]]:
        for result in self._load():
            if (
                result.package_names == package_names
                and result.filtered_names == filtered_names
            ):
                return result.packages
        return None

    def insert(
        self,
        *,
        package_names: Set[str],
        filtered_names: Set[str],
        packages: List[DebPackage],
    ) -> None:
        # Clear the db if the entry looks to overwrite an existing entry.
        entries = self._load()
        if [
            entry
            for entry in entries
            if entry.package_names == package_names
            and entry.filtered_names == filtered_names
        ]:
            entries = set()

        entries.add(
            AptStageCacheDbEntry(
                package_names=package_names,
                filtered_names=filtered_names,
                packages=packages,
            )
        )

        self._save(entries)
