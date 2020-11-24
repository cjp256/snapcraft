# -*- Mode:Python; indent-tabs-mode:nil; tab-width:4 -*-
#
# Copyright (C) 2017-2019 Canonical Ltd
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

import itertools
import logging
import os
import pathlib
import platform
import subprocess
import sys
import time
import typing
from typing import List, Optional, Union

import click
import progressbar
from xcraft.providers.lxd import LXDProvider

from snapcraft import file_utils
from snapcraft.internal import deprecations, indicators, lifecycle
from snapcraft.project._sanity_checks import conduct_project_sanity_check
from snapcraft.providers.executed import SnapcraftExecutedProvider

from . import echo
from ._command import SnapcraftProjectCommand
from ._errors import TRACEBACK_HOST, TRACEBACK_MANAGED
from ._options import (
    add_provider_options,
    apply_host_provider_flags,
    get_build_provider,
    get_build_provider_flags,
    get_project,
)

if typing.TYPE_CHECKING:
    from snapcraft.internal.project import Project  # noqa: F401


logger = logging.getLogger(__name__)


def _get_primary_mirror() -> str:
    primary_mirror = os.getenv("SNAPCRAFT_APT_PRIMARY_MIRROR", None)

    if primary_mirror is None:
        if platform.machine() in ["AMD64", "i686", "x86_64"]:
            primary_mirror = "http://archive.ubuntu.com/ubuntu"
        else:
            primary_mirror = "http://ports.ubuntu.com/ubuntu-ports"

    return primary_mirror


def _get_security_mirror() -> str:
    security_mirror = os.getenv("SNAPCRAFT_APT_SECURITY_MIRROR", None)

    if security_mirror is None:
        if platform.machine() in ["AMD64", "i686", "x86_64"]:
            security_mirror = "http://security.ubuntu.com/ubuntu"
        else:
            security_mirror = "http://ports.ubuntu.com/ubuntu-ports"

    return security_mirror


def provide_lxd(*, base: str, project_name: str, default_run_environment: dict):
    image_name = dict(
        core="16.04", core18="18.04", core20="20.04", core22="22.04",
    ).get(base)

    if image_name is None:
        raise click.ClickException(f"LXD provider does not support base {base!r}.")

    instance_name = f"snapcraft-{project_name}"

    return LXDProvider(
        instance_name=instance_name,
        default_run_environment=default_run_environment,
        image_name=image_name,
    )


def provide(*, skip_setup: bool = False, **kwargs):
    build_provider = get_build_provider(**kwargs)
    build_provider_flags = get_build_provider_flags(build_provider, **kwargs)
    apply_host_provider_flags(build_provider_flags)

    # Temporary fix to ignore target_arch.
    if kwargs.get("target_arch") is not None and build_provider in ["multipass", "lxd"]:
        echo.warning(
            "Ignoring '--target-arch' flag.  This flag requires --destructive-mode and is unsupported with Multipass and LXD build providers."
        )
        kwargs.pop("target_arch")

    project = get_project(is_managed_host=False, **kwargs)
    conduct_project_sanity_check(project, **kwargs)

    default_run_environment = dict(
        PATH="/snap/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        SNAPCRAFT_BUILD_ENVIRONMENT="host",
    )

    if build_provider == "lxd":
        env_provider = provide_lxd(
            base=project._get_build_base(),
            project_name=project.info.name,
            default_run_environment=default_run_environment,
        )

    if not skip_setup:
        env_provider.setup()

    return SnapcraftExecutedProvider(
        default_run_environment=default_run_environment,
        env_provider=env_provider,
        host_artifacts_dir=pathlib.Path(kwargs.get("output", ".")).resolve(),
        host_project_dir=pathlib.Path(".").resolve(),
        install_apt_primary_mirror=_get_primary_mirror(),
        install_base=project._get_build_base(),
        install_certs=build_provider_flags.get("SNAPCRAFT_ADD_CA_CERTIFICATES", None),
        install_http_proxy=build_provider_flags.get("http_proxy", None),
        install_https_proxy=build_provider_flags.get("https_proxy", None),
        user_debug=kwargs.get("debug", False),
        user_shell=kwargs.get("shell", False),
    )


def _run_pack(snap_command: List[Union[str, pathlib.Path]]) -> str:
    ret = None
    stdout = ""
    stderr = ""
    with subprocess.Popen(
        snap_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    ) as proc:
        if indicators.is_dumb_terminal():
            echo.info("Snapping...")
            ret = proc.wait()
        else:
            message = f"\033[0;32mSnapping \033[0m"
            progress_indicator = progressbar.ProgressBar(
                widgets=[message, progressbar.AnimatedMarker()],
                # From progressbar.ProgressBar.update(...).
                maxval=progressbar.UnknownLength,
            )
            progress_indicator.start()
            for counter in itertools.count():
                progress_indicator.update(counter)
                time.sleep(0.2)
                ret = proc.poll()
                if ret is not None:
                    break
            progress_indicator.finish()

        if proc.stdout is not None:
            stdout = proc.stdout.read().decode()
        if proc.stderr is not None:
            stderr = proc.stderr.read().decode()
        logger.debug(f"stdout: {stdout} | stderr: {stderr}")

    if ret != 0:
        raise RuntimeError(
            f"Failed to create snap, snap command failed:\nstdout:\n{stdout}\nstderr:\n{stderr}"
        )

    try:
        snap_filename = stdout.split(":")[1].strip()
    except IndexError:
        logger.debug("Failed to parse snap pack outpout: {stdout}")
        snap_filename = stdout

    return snap_filename


def _pack(
    directory: str, *, compression: Optional[str] = None, output: Optional[str]
) -> None:
    """Pack a snap.

    :param directory: directory to snap
    :param compression: compression type to use, None for defaults
    :param output: Output may either be:
        (1) a directory path to output snaps to
        (2) an explicit file path to output snap to
        (3) unpsecified/None to output to current (project) directory
    """
    output_file = None
    output_dir = None

    if output:
        output_path = pathlib.Path(output)
        output_parent = output_path.parent
        if output_path.is_dir():
            output_dir = str(output_path)
        elif output_parent and output_parent != pathlib.Path("."):
            output_dir = str(output_parent)
            output_file = output_path.name
        else:
            output_file = output

    snap_path = file_utils.get_host_tool_path(command_name="snap", package_name="snapd")

    command: List[Union[str, pathlib.Path]] = [snap_path, "pack"]
    # When None, just use snap pack's default settings.
    if compression is not None:
        if compression != "xz":
            echo.warning(
                f"EXPERIMENTAL: Setting the squash FS compression to {compression!r}."
            )
        command.extend(["--compression", compression])

    if output_file is not None:
        command.extend(["--filename", output_file])

    command.append(directory)

    if output_dir is not None:
        command.append(output_dir)

    logger.debug(f"Running pack command: {command}")
    snap_filename = _run_pack(command)
    echo.info(f"Snapped {snap_filename}")


def _clean_provider_error() -> None:
    if os.path.isfile(TRACEBACK_HOST):
        try:
            os.remove(TRACEBACK_HOST)
        except Exception as e:
            logger.debug("can't remove error file: {}", str(e))


def _retrieve_provider_error(instance) -> None:
    try:
        instance.pull_file(TRACEBACK_MANAGED, TRACEBACK_HOST, delete=True)
    except Exception as e:
        logger.debug("can't retrieve error file: {}", str(e))


@click.group()
@add_provider_options()
@click.pass_context
def lifecyclecli(ctx, **kwargs):
    pass


@lifecyclecli.command()
def init():
    """Initialize a snapcraft project."""
    snapcraft_yaml_path = lifecycle.init()
    echo.info("Created {}.".format(snapcraft_yaml_path))
    echo.wrapped(
        "Go to https://docs.snapcraft.io/the-snapcraft-format/8337 for more "
        "information about the snapcraft.yaml format."
    )


@lifecyclecli.command(cls=SnapcraftProjectCommand)
@click.pass_context
@add_provider_options()
@click.argument("parts", nargs=-1, metavar="<part>...", required=False)
def pull(ctx, parts, **kwargs):
    """Download or retrieve artifacts defined for a part.

    \b
    Examples:
        snapcraft pull
        snapcraft pull my-part1 my-part2

    """
    with provide(**kwargs) as provider:
        rc = provider.pull(parts=parts)

    sys.exit(rc)


@lifecyclecli.command(cls=SnapcraftProjectCommand)
@add_provider_options()
@click.argument("parts", nargs=-1, metavar="<part>...", required=False)
def build(parts, **kwargs):
    """Build artifacts defined for a part.

    \b
    Examples:
        snapcraft build
        snapcraft build my-part1 my-part2

    """
    with provide(**kwargs) as provider:
        rc = provider.build(parts=parts)

    sys.exit(rc)


@lifecyclecli.command(cls=SnapcraftProjectCommand)
@add_provider_options()
@click.argument("parts", nargs=-1, metavar="<part>...", required=False)
def stage(parts, **kwargs):
    """Stage the part's built artifacts into the common staging area.

    \b
    Examples:
        snapcraft stage
        snapcraft stage my-part1 my-part2

    """
    with provide(**kwargs) as provider:
        rc = provider.stage(parts=parts)

    sys.exit(rc)


@lifecyclecli.command(cls=SnapcraftProjectCommand)
@add_provider_options()
@click.argument("parts", nargs=-1, metavar="<part>...", required=False)
def prime(parts, **kwargs):
    """Final copy and preparation for the snap.

    \b
    Examples:
        snapcraft prime
        snapcraft prime my-part1 my-part2

    """
    with provide(**kwargs) as provider:
        rc = provider.prime()

    sys.exit(rc)


@lifecyclecli.command("try")
@add_provider_options()
def try_command(**kwargs):
    """Try a snap on the host, priming if necessary.

    This feature only works on snap enabled systems.

    \b
    Examples:
        snapcraft try

    """
    with provide(**kwargs) as provider:
        # TODO: prime is now always available.
        rc = provider.prime()

    if rc == 0:
        echo.info("You can now run `snap try prime`.")

    sys.exit(rc)


@lifecyclecli.command(cls=SnapcraftProjectCommand)
@add_provider_options()
@click.argument("directory", required=False)
@click.option("--output", "-o", help="path to the resulting snap.")
def snap(directory, output, **kwargs):
    """Create a snap.

    \b
    Examples:
        snapcraft snap
        snapcraft snap --output renamed-snap.snap

    If you want to snap a directory, you should use the pack command
    instead.
    """
    if directory:
        deprecations.handle_deprecation_notice("dn6")
        _pack(directory, output=output)
    else:
        with provide(**kwargs) as provider:
            rc = provider.snap()

        sys.exit(rc)


@lifecyclecli.command(cls=SnapcraftProjectCommand)
@click.argument("directory")
@click.option("--output", "-o", help="path to the resulting snap.")
def pack(directory, output, **kwargs):
    """Create a snap from a directory holding a valid snap.

    The layout of <directory> should contain a valid meta/snap.yaml in
    order to be a valid snap.

    \b
    Examples:
        snapcraft pack my-snap-directory
        snapcraft pack my-snap-directory --output renamed-snap.snap

    """
    _pack(directory, output=output)


@lifecyclecli.command(cls=SnapcraftProjectCommand)
@click.pass_context
@add_provider_options()
@click.argument("parts", nargs=-1, metavar="<part>...", required=False)
@click.option("--unprime", is_flag=True, required=False, hidden=True)
@click.option("--step", "-s", required=False, hidden=True)
def clean(ctx, parts, unprime, step, **kwargs):
    """Remove a part's assets.

    \b
    Examples:
        snapcraft clean
        snapcraft clean my-part
    """
    # This option is only valid in legacy.
    if step:
        option = "--step" if "--step" in ctx.obj["argv"] else "-s"
        raise click.BadOptionUsage(option, "no such option: {}".format(option))

    provider = provide(skip_setup=True, **kwargs)
    provider.clean(parts=parts)


if __name__ == "__main__":
    lifecyclecli.main()
