import pathlib
from typing import Dict, List

from xcraft.providers.executed_provider import ExecutedProvider


class MycraftExecutedProvider:
    """Manages the lifecycle of a project for executed providers."""

    def __init__(
        self,
        *,
        env_provider: ExecutedProvider,
        env_artifacts_dir: pathlib.Path = pathlib.Path("/root/mycraft-artifacts"),
        env_project_dir: pathlib.Path = pathlib.Path("/root/mycraft-project"),
        host_artifacts_dir: pathlib.Path,
        host_project_dir: pathlib.Path,
        run_environment: Dict[str, str] = None,
    ) -> None:
        self.env_provider = env_provider
        self.env_artifacts_dir = env_artifacts_dir
        self.env_project_dir = env_project_dir
        self.host_artifacts_dir = host_artifacts_dir
        self.host_project_dir = host_project_dir

        if run_environment:
            self.run_environment = run_environment.copy()
        else:
            self.run_environment = {
                "SNAPCRAFT_BUILD_ENVIRONMENT": "host",
                "PATH": "/snap/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
            }

    def _run(self, command: List[str], **kwargs):
        if "env" in kwargs:
            env = kwargs.pop("env")
        else:
            env = self.run_environment

        if "cwd" in kwargs:
            cwd = kwargs.pop("cwd")
        else:
            cwd = self.env_project_dir.as_posix()

        return self.env_provider.executor.execute_run(
            command,
            env=env,
            cwd=cwd,
            **kwargs,
        )

    def _setup_snapcraft(self) -> None:
        self._run(["apt", "install", "-y", "git", "python3-pip"], check=True)
        self._run(
            ["snap", "install", "snapcraft", "--classic"],
            check=True,
        )

    def setup(self) -> None:
        """Run any required setup prior to executing lifecycle steps."""
        self.env_provider.executor.sync_to(
            source=self.host_project_dir, destination=self.env_project_dir
        )
        self._setup_mycraft()
        self._run(["mkdir", "-p", self.env_artifacts_dir.as_posix()], check=True)

    def build(self, *, parts: List[str]) -> None:
        """Run build phase."""
        self._run(["mycraft", "build", *parts])

    def pull(self, *, parts: List[str]) -> None:
        """Run pull phase."""
        self._run(["mycraft", "pull", *parts])

    def craft(self) -> List[pathlib.Path]:
        """Craft project, executing lifecycle steps as required.

        Write output snaps to host project directory.

        :param output_dir: Directory to write snaps to.

        :returns: Path to snap(s) created from build.
        """
        self._run(["mycraft", "--output", self.env_artifacts_dir.as_posix(), "craft"])

        # Sync artifacts.
        self.env_provider.executor.sync_from(
            source=self.env_artifacts_dir,
            destination=self.host_artifacts_dir,
        )

        return list(self.host_artifacts_dir.glob("*.zip"))

    def clean_parts(self, *, parts: List[str]) -> None:
        """Clean specified parts.

        :param parts: List of parts to clean.
        """
        self._run(["mycraft", "clean", *parts])

    def clean(self) -> None:
        """Clean all artifacts of project and build environment.

        Purges all artifacts from using the provider to build the
        project.  This includes build-instances (containers/VMs) and
        associated metadata and records.

        This does not include any artifacts that have resulted from
        a call to snap(), i.e. snap files or build logs.
        """
        self.env_provider.clean()
