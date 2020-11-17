import os
import pathlib
import shutil
from typing import Dict, List
from urllib import request


class MycraftHostProvider:
    """Manages the lifecycle of a project."""

    def __init__(
        self,
        *,
        provider=None,
        artifacts_dir: pathlib.Path = pathlib.Path(os.getcwd(), "artifacts"),
        project_dir: pathlib.Path = pathlib.Path(os.getcwd()),
    ) -> None:
        self.provider = provider
        self.artifacts_dir = artifacts_dir
        self.project_dir = project_dir
        self.work_dir = project_dir / "work"

    def pull(self, *, parts: List[str]) -> None:
        """Run pull phase."""
        self.work_dir.mkdir(parents=True, exist_ok=True)

        request.urlretrieve(
            "https://bnetproduct-a.akamaihd.net//fde/6828d768c4640a9a214edeabbfa20c60-prod-thumb_02.jpg",
            filename=str(self.work_dir / "craft.jpg"),
        )

    def build(self, *, parts: List[str]) -> None:
        catalog = self.work_dir / "catalog.yaml"
        catalog.write_text("icon=craft.jpg")

    def craft(self) -> List[pathlib.Path]:
        """Craft project, executing lifecycle steps as required.
        :returns: Path to snap(s) created from build.
        """
        self.pull(parts=list())
        self.build(parts=list())

        out = self.artifacts_dir / "mycraft_0.1.zip"
        shutil.make_archive(str(out), "zip", self.work_dir)

        return [out]

    def clean_parts(self, *, parts: List[str]) -> None:
        """Clean specified parts.

        :param parts: List of parts to clean.
        """
        if self.artifacts_dir.exists():
            shutil.rmtree(self.artifacts_dir)

        if self.work_dir.exists():
            shutil.rmtree(self.work_dir)

    def clean(self) -> None:
        """Clean all artifacts of project and build environment.

        Purges all artifacts from using the provider to build the project. This
        includes build-instances (containers/VMs) and associated metadata and
        records.

        This does not include any artifacts that have resulted from
        a call to snap(), i.e. snap files or build logs.

        """
        if self.provider is not None:
            self.provider.clean()

    def setup(self) -> None:
        pass
