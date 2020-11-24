import logging
import pathlib
import subprocess
from textwrap import dedent
from typing import Dict, List, Optional

from xcraft.providers.executed_provider import ExecutedProvider

logger = logging.getLogger(__name__)


class SnapcraftExecutedProvider:
    """Manages the lifecycle of a project for executed providers."""

    def __init__(
        self,
        *,
        bind_mount_project: bool = True,
        bind_mount_ssh: bool = False,
        default_run_environment: Dict[str, str],
        env_provider: ExecutedProvider,
        env_artifacts_dir: pathlib.Path = pathlib.Path("/root/snapcraft-artifacts"),
        env_project_dir: pathlib.Path = pathlib.Path("/root/snapcraft-project"),
        host_artifacts_dir: pathlib.Path,
        host_project_dir: pathlib.Path,
        install_apt_primary_mirror: str,
        install_base: str,
        install_certs: Optional[pathlib.Path],
        install_http_proxy: Optional[str],
        install_https_proxy: Optional[str],
        user_debug: bool = False,
        user_shell: bool = False,
    ) -> None:
        self.bind_mount_project = bind_mount_project
        self.bind_mount_ssh = bind_mount_ssh
        self.default_run_environment = default_run_environment
        self.env_provider = env_provider
        self.env_artifacts_dir = env_artifacts_dir
        self.env_project_dir = env_project_dir
        self.host_artifacts_dir = host_artifacts_dir
        self.host_project_dir = host_project_dir
        self.install_apt_primary_mirror = install_apt_primary_mirror
        self.install_base = install_base
        self.install_certs = install_certs
        self.install_http_proxy = install_http_proxy
        self.install_https_proxy = install_https_proxy
        self.user_debug = user_debug
        self.user_shell = user_shell

    def build(self, *, parts: List[str]) -> int:
        """Run build phase."""
        return self._run_lifecycle_command(["snapcraft", "build", *parts])

    def clean(self, *, parts: List[str]) -> int:
        """Clean artifacts of project and build environment.

        If no parts are specified, purges all project artifacts,
        including build-instances and associated metadata.

        If parts are specified, cleans associated parts.

        This does not include any artifacts that have resulted from
        a call to snap(), i.e. snap files or build logs.
        """
        if not self.env_provider.executor.exists():
            return 0

        if parts:
            return self._run(["snapcraft", "clean", *parts])

        self.env_provider.teardown(clean=True)
        return 0

    def __enter__(self) -> "SnapcraftExecutedProvider":
        self.setup()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        pass

    def prime(self) -> int:
        return self._run_lifecycle_command(["snapcraft", "prime"])

    def pull(self, *, parts: List[str]) -> int:
        return self._run_lifecycle_command(["snapcraft", "pull", *parts])

    def _query_environment_home_directory(self) -> pathlib.Path:
        proc = self._run(["echo", "$HOME"], check=True, stdout=subprocess.PIPE)
        return pathlib.Path(proc.stdout)

    def _run(self, command: List[str], **kwargs):
        if "env" in kwargs:
            env = kwargs.pop("env")
        else:
            env = self.default_run_environment

        if "cwd" in kwargs:
            cwd = kwargs.pop("cwd")
        else:
            cwd = self.env_project_dir.as_posix()

        return self.env_provider.executor.execute_run(
            command, env=env, cwd=cwd, **kwargs,
        )

    def _run_lifecycle_command(self, command: List[str]) -> int:
        """Launch a shell if able to do so given the user args."""

        proc = self._run(command)

        # Launch a shell if necessary.
        if self.user_shell or (proc.returncode != 0 and self.user_debug):
            self._run(["bash", "-i"])

        return proc.returncode

    def _setup_environment_apt(self) -> None:
        self.env_provider.executor.create_file(
            destination=pathlib.Path("/etc/apt/apt.conf.d/00-snapcraft"),
            content=dedent(
                """\
                    Apt::Install-Recommends "false";
                    """
            ).encode(),
            file_mode="0644",
        )

        # Refresh repository caches.
        self._run(["apt-get", "update"])

        # And make sure we are using the latest from that cache.
        self._run(["apt-get", "dist-upgrade", "--yes"])

        # Install any packages that might be missing from the base
        # image, but may be required for snapcraft to function.
        self._run(["apt-get", "install", "--yes", "apt-transport-https"])

    def _setup_environment_ca_certificates(self) -> None:
        """Install additional CA certificate(s), if configured."""
        if self.install_certs is None:
            return

        # Should have been validated by click already.
        if not self.install_certs.exists():
            raise RuntimeError(
                f"Unable to read CA certificates {self.install_certs!r}."
            )

        if self.install_certs.is_file():
            certificate_files = [self.install_certs]
        elif self.install_certs.is_dir():
            certificate_files = [x for x in self.install_certs.iterdir() if x.is_file()]
        else:
            raise RuntimeError(
                f"Unable to read CA certificates {self.install_certs!r} - unhandled file type."
            )

        for certificate_file in sorted(certificate_files):
            logger.debug(f"Installing CA certificate: {certificate_file}")
            dst_path = "/usr/local/share/ca-certificates/" + certificate_file.name
            self.env_provider.executor.sync_to(
                source=certificate_file, destination=dst_path
            )

        if certificate_files:
            self._run(["update-ca-certificates"], check=True)

    def _setup_environment_shell_prompt(self) -> None:
        self.env_provider.executor.create_file(
            destination=pathlib.Path("/root/.bashrc"),
            content=dedent(
                """\
                        #!/bin/bash
                        export PS1="\\h \\$(/bin/_snapcraft_prompt)# "
                        """
            ).encode(),
            file_mode="0600",
        )

        self.env_provider.executor.create_file(
            destination=pathlib.Path("/bin/_snapcraft_prompt"),
            content=dedent(
                """\
                        #!/bin/bash
                        if [[ "$PWD" =~ ^$HOME.* ]]; then
                            path="${PWD/#$HOME/\\ ..}"
                            if [[ "$path" == " .." ]]; then
                                ps1=""
                            else
                                ps1="$path"
                            fi
                        else
                            ps1="$PWD"
                        fi
                        echo -n $ps1
                        """
            ).encode(),
            file_mode="0755",
        )

    def _setup_environment_ssh(self) -> None:
        if self.bind_mount_project and self.env_provider.executor.supports_mount():
            self.env_provider.executor.mount(
                source=self.host_project_dir, destination=self.env_project_dir
            )

    def _setup_environment_snapcraft(self) -> None:
        self._run(["snap", "install", "core18"], check=True)

        if self.install_base != "core18":
            self._run(["snap", "install", self.install_base], check=True)

        self._run(["snap", "install", "snapcraft", "--classic"], check=True)

    def _setup_environment_snapd(self) -> None:
        """Configure snapd, base provider installs snapd."""
        if self.install_http_proxy:
            self._run(
                ["snap", "set", "system", f"proxy.http={self.install_http_proxy}"],
                check=True,
            )
        else:
            self._run(["snap", "unset", "system", "proxy.http"], check=True)

        if self.install_https_proxy:
            self._run(
                ["snap", "set", "system", f"proxy.https={self.install_https_proxy}"],
                check=True,
            )
        else:
            self._run(["snap", "unset", "system", "proxy.https"], check=True)

    def _setup_environment_bind_mounts(self) -> None:
        if self.bind_mount_ssh:
            home_dir = self._query_environment_home_directory()
            ssh_dir = home_dir / ".ssh"

            self._run(["mkdir", "-p", ssh_dir.as_posix()], check=True)
            self._run(["chmod", "700", ssh_dir.as_posix()], check=True)

            self.env_provider.executor.mount(
                source=self.host_project_dir, destination=ssh_dir
            )

    def _setup_environment_project(self) -> None:
        self._run(
            ["mkdir", "-p", self.env_project_dir.as_posix()],
            cwd=pathlib.Path("/root"),
            check=True,
        )
        self._run(["mkdir", "-p", self.env_artifacts_dir.as_posix()], check=True)

        if self.env_provider.executor.supports_mount() and self.bind_mount_project:
            self.env_provider.executor.mount(
                source=self.host_project_dir, destination=self.env_project_dir
            )
        else:
            self.env_provider.executor.sync_to(
                source=self.host_project_dir, destination=self.env_project_dir
            )

    def setup(self) -> None:
        """Run any required setup prior to executing lifecycle steps."""
        self._setup_environment_project()
        self._setup_environment_ca_certificates()
        self._setup_environment_apt()
        self._setup_environment_snapd()
        self._setup_environment_snapcraft()
        self._setup_environment_shell_prompt()
        self._setup_environment_bind_mounts()

    def snap(self) -> int:
        """Craft project, executing lifecycle steps as required.

        Write output snaps to host project directory.
        """
        rc = self._run_lifecycle_command(["snapcraft"])
        if rc == 0:
            # Sync artifacts...
            # self.env_provider.executor.sync_from(
            #    source=self.env_artifacts_dir, destination=self.host_artifacts_dir,
            # )
            pass

        return rc
