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

import pickle
from pathlib import Path

from testtools.matchers import Equals

from snapcraft.internal.repo.apt_cache import DebPackage
from snapcraft.internal.repo.apt_stage_cache_db import (
    AptStageCacheDb,
    AptStageCacheDbEntry,
)
from tests import unit


class TestAptStageCacheDb(unit.TestCase):
    def setUp(self):
        super().setUp()

        self.packages = [
            DebPackage(name="fake-name", version="1.0", path=Path("/fake-path")),
            DebPackage(name="fake-name2", version="2.0", path=Path("/fake-path2")),
        ]
        self.db = AptStageCacheDb(stage_cache=Path(self.path))

    def test_find_empty(self):
        results = self.db.find(package_names={"foo"}, filtered_names={"filtered"})
        self.assertThat(results, Equals(None))

    def test_insert(self):
        self.db.insert(
            package_names={"foo"}, filtered_names={"filtered"}, packages=self.packages
        )
        results = self.db.find(package_names={"foo"}, filtered_names={"filtered"})

        self.assertThat(results, Equals(self.packages))

    def test_no_parent_directory(self):
        self.db = AptStageCacheDb(stage_cache=Path(self.path, "does-not-exist"))
        self.db.insert(
            package_names={"foo"}, filtered_names={"filtered"}, packages=self.packages
        )
        results = self.db.find(package_names={"foo"}, filtered_names={"filtered"})

        self.assertThat(results, Equals(self.packages))

    def test_corrupt_db_find(self):
        # Put junk into the underlying DB file.
        self.db._pickle_db.write_text("fake-news")

        results = self.db.find(package_names={"foo"}, filtered_names={"filtered"})

        self.assertThat(results, Equals(None))

    def test_corrupt_db_insert(self):
        # Put junk into the underlying DB file.
        self.db._pickle_db.write_text("fake-news")

        self.db.insert(
            package_names={"foo"}, filtered_names={"filtered"}, packages=self.packages
        )
        results = self.db.find(package_names={"foo"}, filtered_names={"filtered"})

        self.assertThat(results, Equals(self.packages))

    def test_incompatible_db_find(self):
        entry = AptStageCacheDbEntry(
            package_names={"foo"}, filtered_names={"filtered"}, packages=self.packages
        )
        del entry.package_names

        with open(self.db._pickle_db, "wb") as fd:
            pickle.dump([entry], fd)

        results = self.db.find(package_names={"foo"}, filtered_names={"filtered"})

        self.assertThat(results, Equals(None))
        self.assertThat(self.db._pickle_db.exists(), Equals(False))

    def test_incompatible_db_insert(self):
        entry = AptStageCacheDbEntry(
            package_names={"foo"}, filtered_names={"filtered"}, packages=self.packages
        )
        del entry.package_names

        with open(self.db._pickle_db, "wb") as fd:
            pickle.dump([entry], fd)

        self.db.insert(
            package_names={"foo"}, filtered_names={"filtered"}, packages=self.packages
        )
        results = self.db.find(package_names={"foo"}, filtered_names={"filtered"})

        self.assertThat(results, Equals(self.packages))
