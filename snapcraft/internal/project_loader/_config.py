# -*- Mode:Python; indent-tabs-mode:nil; tab-width:4 -*-
#
# Copyright (C) 2015-2020 Canonical Ltd
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

import collections
import logging
import os
import os.path
import re
from collections import OrderedDict
from copy import deepcopy
from typing import Any, Dict, List, Optional, Sequence, Set

import jsonschema

from snapcraft import formatting_utils, project, yaml_utils
from snapcraft.internal import common, deprecations, repo, states, steps
from snapcraft.internal.meta.application import Application
from snapcraft.internal.meta.hooks import Hook
from snapcraft.internal.meta.package_repository import PackageRepository
from snapcraft.internal.meta.plugs import ContentPlug, Plug
from snapcraft.internal.meta.slots import ContentSlot, Slot
from snapcraft.internal.meta.system_user import SystemUser
from snapcraft.internal.pluginhandler._part_environment import (
    get_snapcraft_global_environment,
)
from snapcraft.project._schema import Validator

from . import errors, grammar, replace_attr
from ._env import build_env_for_stage, environment_to_replacements, runtime_env
from ._extensions import apply_extensions
#from ._grammar_processing._package_transformer import package_transformer
from ._parts_config import PartsConfig

logger = logging.getLogger(__name__)


@jsonschema.FormatChecker.cls_checks("icon-path")
def _validate_icon(icon):
    allowed_extensions = [".png", ".svg"]
    extension = os.path.splitext(icon.lower())[1]
    if extension not in allowed_extensions:
        raise jsonschema.exceptions.ValidationError(
            f"icon {icon!r} must be either a .png or a .svg"
        )

    return True


@jsonschema.FormatChecker.cls_checks("epoch", raises=errors.InvalidEpochError)
def _validate_epoch(instance):
    str_instance = str(instance)
    pattern = re.compile("^(?:0|[1-9][0-9]*[*]?)$")
    if not pattern.match(str_instance):
        raise errors.InvalidEpochError()

    return True


@jsonschema.FormatChecker.cls_checks("architectures")
def _validate_architectures(instance):
    standalone_build_ons = collections.Counter()
    build_ons = collections.Counter()
    run_ons = collections.Counter()

    saw_strings = False
    saw_dicts = False

    for item in instance:
        # This could either be a dict or a string. In the latter case, the
        # schema will take care of it. We just need to further validate the
        # dict.
        if isinstance(item, str):
            saw_strings = True
        elif isinstance(item, dict):
            saw_dicts = True
            build_on = _get_architectures_set(item, "build-on")
            build_ons.update(build_on)

            # Add to the list of run-ons. However, if no run-on is specified,
            # we know it's implicitly the value of build-on, so use that
            # for validation instead.
            run_on = _get_architectures_set(item, "run-on")
            if run_on:
                run_ons.update(run_on)
            else:
                standalone_build_ons.update(build_on)

    # Architectures can either be a list of strings, or a list of objects.
    # Mixing the two forms is unsupported.
    if saw_strings and saw_dicts:
        raise jsonschema.exceptions.ValidationError(
            "every item must either be a string or an object",
            path=["architectures"],
            instance=instance,
        )

    # At this point, individual build-ons and run-ons have been validated,
    # we just need to validate them across each other.

    # First of all, if we have a `run-on: [all]` (or a standalone
    # `build-on: [all]`) then we should only have one item in the instance,
    # otherwise we know we'll have multiple snaps claiming they run on the same
    # architectures (i.e. all and something else).
    number_of_snaps = len(instance)
    if "all" in run_ons and number_of_snaps > 1:
        raise jsonschema.exceptions.ValidationError(
            "one of the items has 'all' in 'run-on', but there are {} "
            "items: upon release they will conflict. 'all' should only be "
            "used if there is a single item".format(number_of_snaps),
            path=["architectures"],
            instance=instance,
        )
    if "all" in build_ons and number_of_snaps > 1:
        raise jsonschema.exceptions.ValidationError(
            "one of the items has 'all' in 'build-on', but there are {} "
            "items: snapcraft doesn't know which one to use. 'all' should "
            "only be used if there is a single item".format(number_of_snaps),
            path=["architectures"],
            instance=instance,
        )

    # We want to ensure that multiple `run-on`s (or standalone `build-on`s)
    # don't include the same arch, or they'll clash with each other when
    # releasing.
    all_run_ons = run_ons + standalone_build_ons
    duplicates = {arch for (arch, count) in all_run_ons.items() if count > 1}
    if duplicates:
        raise jsonschema.exceptions.ValidationError(
            "multiple items will build snaps that claim to run on {}".format(
                formatting_utils.humanize_list(duplicates, "and")
            ),
            path=["architectures"],
            instance=instance,
        )

    # Finally, ensure that multiple `build-on`s don't include the same arch
    # or Snapcraft has no way of knowing which one to use.
    duplicates = {arch for (arch, count) in build_ons.items() if count > 1}
    if duplicates:
        raise jsonschema.exceptions.ValidationError(
            "{} {} present in the 'build-on' of multiple items, which means "
            "snapcraft doesn't know which 'run-on' to use when building on "
            "{} {}".format(
                formatting_utils.humanize_list(duplicates, "and"),
                formatting_utils.pluralize(duplicates, "is", "are"),
                formatting_utils.pluralize(duplicates, "that", "those"),
                formatting_utils.pluralize(duplicates, "architecture", "architectures"),
            ),
            path=["architectures"],
            instance=instance,
        )

    return True


def _get_architectures_set(item, name):
    value = item.get(name, set())
    if isinstance(value, str):
        value_set = {value}
    else:
        value_set = set(value)

    _validate_architectures_set(value_set, name)

    return value_set


def _validate_architectures_set(architectures_set, name):
    if "all" in architectures_set and len(architectures_set) > 1:
        raise jsonschema.exceptions.ValidationError(
            "'all' can only be used within {!r} by itself, "
            "not with other architectures".format(name),
            path=["architectures"],
            instance=architectures_set,
        )


class _Architecture:
    def __init__(self, *, build_on, run_on=None):
        if isinstance(build_on, str):
            self.build_on = [build_on]
        else:
            self.build_on = build_on

        # If there is no run_on, it defaults to the value of build_on
        if not run_on:
            self.run_on = self.build_on
        elif isinstance(run_on, str):
            self.run_on = [run_on]
        else:
            self.run_on = run_on


def _create_architecture_list(architectures, current_arch):
    if not architectures:
        return [_Architecture(build_on=[current_arch])]

    build_architectures: List[str] = []
    architecture_list: List[_Architecture] = []
    for item in architectures:
        if isinstance(item, str):
            build_architectures.append(item)
        if isinstance(item, dict):
            architecture_list.append(
                _Architecture(build_on=item.get("build-on"), run_on=item.get("run-on"))
            )

    if build_architectures:
        architecture_list.append(_Architecture(build_on=build_architectures))

    return architecture_list


def _process_architectures(architectures, current_arch):
    architecture_list = _create_architecture_list(architectures, current_arch)

    for architecture in architecture_list:
        if current_arch in architecture.build_on or "all" in architecture.build_on:
            return architecture.run_on

    return [current_arch]


def _expand_filesets_for(step, properties):
    filesets = properties.get("filesets", {})
    fileset_for_step = properties.get(step, {})
    new_step_set = []

    for item in fileset_for_step:
        if item.startswith("$"):
            try:
                new_step_set.extend(filesets[item[1:]])
            except KeyError:
                raise errors.SnapcraftLogicError(
                    "'{}' referred to in the '{}' fileset but it is not "
                    "in filesets".format(item, step)
                )
        else:
            new_step_set.append(item)

    return new_step_set


def _expand_filesets(snapcraft_yaml):
    parts = snapcraft_yaml.get("parts", {})

    for part_name in parts:
        for step in ("stage", "prime"):
            step_fileset = _expand_filesets_for(step, parts[part_name])
            parts[part_name][step] = step_fileset

    return snapcraft_yaml


def _expand_env(*, snapcraft_yaml: Dict[str, Any], environment: Dict[str, str]):
    environment_keys = ["name", "version"]
    for key in snapcraft_yaml:
        if key in environment_keys:
            continue

        replacements = environment_to_replacements(environment)

        snapcraft_yaml[key] = replace_attr(snapcraft_yaml[key], replacements)
    return snapcraft_yaml


class Config:
    """Representation of snapcraft.yaml"""

    def __init__(  # noqa: C901
        self,
        *,
        parts: PartsConfig,
        project: project.Project,
        adopt_info: Optional[str] = None,
        apps: Optional[Dict[str, Application]] = None,
        architectures: Optional[Sequence[str]] = None,
        assumes: Optional[Set[str]] = None,
        base: Optional[str] = None,
        build_base: Optional[str] = None,
        build_packages: Set[str] = None,
        compression: Optional[str] = None,
        confinement: Optional[str] = None,
        description: Optional[str] = None,
        environment: Optional[Dict[str, Any]] = None,
        epoch: Any = None,
        expanded_snapcraft_yaml: Optional[Dict[str, Any]] = None,
        grade: Optional[str] = None,
        hooks: Optional[Dict[str, Hook]] = None,
        layout: Optional[Dict[str, Any]] = None,
        license: Optional[str] = None,
        name: Optional[str] = None,
        package_repositories: Optional[List[PackageRepository]] = None,
        passthrough: Optional[Dict[str, Any]] = None,
        plugs: Optional[Dict[str, Plug]] = None,
        slots: Optional[Dict[str, Slot]] = None,
        summary: Optional[str] = None,
        system_usernames: Optional[Dict[str, SystemUser]] = None,
        title: Optional[str] = None,
        type: Optional[str] = None,
        version: Optional[str] = None,
    ) -> None:
        self.adopt_info = adopt_info

        if apps is None:
            self.apps: Dict[str, Application] = dict()
        else:
            self.apps = apps

        if architectures is None:
            self.architectures: Sequence[str] = list()
        else:
            self.architectures = architectures

        if assumes is None:
            self.assumes: Set[str] = set()
        else:
            self.assumes = assumes

        self.base = base
        self.build_base = build_base
        self.build_packages = build_packages
        self.compression = compression
        self.confinement = confinement
        self.description = description

        if environment is None:
            self.environment: Dict[str, Any] = dict()
        else:
            self.environment = environment

        self.epoch = epoch
        self.expanded_snapcraft_yaml = expanded_snapcraft_yaml
        self.grade = grade

        if hooks is None:
            self.hooks: Dict[str, Hook] = dict()
        else:
            self.hooks = hooks

        if layout is None:
            self.layout: Dict[str, Any] = dict()
        else:
            self.layout = layout

        self.license = license
        self.name = name

        if package_repositories is None:
            self.package_repositories: List[PackageRepository] = list()
        else:
            self.package_repositories = package_repositories

        self.parts = parts

        # TODO: remove
        self.project = project

        if passthrough is None:
            self.passthrough: Dict[str, Any] = dict()
        else:
            self.passthrough = passthrough

        if plugs is None:
            self.plugs: Dict[str, Plug] = dict()
        else:
            self.plugs = plugs

        if slots is None:
            self.slots: Dict[str, Slot] = dict()
        else:
            self.slots = slots

        self.summary = summary

        if system_usernames is None:
            self.system_usernames: Dict[str, SystemUser] = dict()
        else:
            self.system_usernames = system_usernames

        self.title = title
        self.type = type
        self.version = version

    @property
    def is_passthrough_enabled(self) -> bool:
        if self.passthrough:
            return True

        for app in self.apps.values():
            if app.passthrough:
                return True

        for hook in self.hooks.values():
            if hook.passthrough:
                return True

        return False

    def get_build_base(self) -> str:
        """
        Return the base to use to create the snap.

        Returns build-base if set, but if not, name is returned if the
        snap is of type base. For all other snaps, the base is returned
        as the build-base.
        """
        build_base: Optional[str] = None
        if self.build_base is not None:
            build_base = self.build_base
        elif self.name is not None and self.type == "base":
            build_base = self.name
        else:
            build_base = self.base

        # The schema does not allow for this when loaded from snapcraft.yaml.
        if build_base is None:
            raise RuntimeError("'build_base' cannot be None")

        return build_base

    def get_content_plugs(self) -> List[ContentPlug]:
        """Get list of content plugs."""
        return [plug for plug in self.plugs.values() if isinstance(plug, ContentPlug)]

    def get_content_slots(self) -> List[ContentSlot]:
        """Get list of content slots."""
        return [slot for slot in self.slots.values() if isinstance(slot, ContentSlot)]

    def get_provider_content_directories(self) -> Set[str]:
        """Get provider content directories from installed snaps."""
        provider_dirs: Set[str] = set()

        for plug in self.get_content_plugs():
            # Get matching slot provider for plug.
            provider = plug.provider
            if not provider:
                continue

            provider_path = common.get_installed_snap_path(provider)
            yaml_path = os.path.join(provider_path, "meta", "snap.yaml")

            if not os.path.exists(yaml_path):
                continue

            snap = Config.from_file(yaml_path)
            for slot in snap.get_content_slots():
                slot_installed_path = common.get_installed_snap_path(provider)
                provider_dirs |= slot.get_content_dirs(
                    installed_path=slot_installed_path
                )

        return provider_dirs

    def validate(self) -> None:
        """Validate snap, raising exception on error."""
        self._validate_required_keys()

        for app in self.apps.values():
            app.validate()

        for hook in self.hooks.values():
            hook.validate()

        for plug in self.plugs.values():
            plug.validate()

        for slot in self.slots.values():
            slot.validate()

        for user in self.system_usernames.values():
            user.validate()

        if self.is_passthrough_enabled:
            logger.warning(
                "The 'passthrough' property is being used to "
                "propagate experimental properties to snap.yaml "
                "that have not been validated."
            )

        self._validate_command_chain_assumption()
        self._validate_no_duplicate_app_aliases()

    def _validate_command_chain_assumption(self) -> None:
        """Ensure command-chain is in assumes (if used)."""
        if "command-chain" in self.assumes:
            return

        for app in self.apps.values():
            if app.command_chain:
                self.assumes.add("command-chain")
                return

        for hook in self.hooks.values():
            if hook.command_chain:
                self.assumes.add("command-chain")
                return

    def _validate_no_duplicate_app_aliases(self):
        # Prevent multiple apps within a snap from having duplicate alias names
        aliases = []
        for app_name, app in self.data.get("apps", {}).items():
            aliases.extend(app.get("aliases", []))

        # The aliases property is actually deprecated:
        if aliases:
            deprecations.handle_deprecation_notice("dn5")
        seen = set()
        duplicates = set()
        for alias in aliases:
            if alias in seen:
                duplicates.add(alias)
            else:
                seen.add(alias)
        if duplicates:
            raise errors.DuplicateAliasError(aliases=duplicates)

    def _validate_required_keys(self) -> None:
        """Verify that all mandatory keys have been satisfied."""
        missing_keys: List[str] = []

        if not self.name:
            missing_keys.append("name")

        if not self.version and not self.adopt_info:
            missing_keys.append("version")

        if not self.summary:
            missing_keys.append("summary")

        if not self.description:
            missing_keys.append("description")

        if missing_keys:
            raise errors.MissingSnapcraftYamlKeysError(keys=missing_keys)

    def process_package_grammars(
        self, *, build_package_is_valid=repo.Repo.build_package_is_valid
    ) -> None:
        """Process grammars which require package repositories to be installed."""
        raw_build_packages = self.expanded_snapcraft_yaml.get("build-packages", list())
        self.build_packages = set(
            grammar.GrammarProcessor(
                raw_build_packages,
                self.project,
                build_package_is_valid,
                transformer=package_transformer,
            ).process()
        )

    @classmethod  # noqa: C901
    def unmarshal(
        cls,
        *,
        snap_dict: Dict[str, Any],
        global_environment: Dict[str, str],
        project: project.Project,
    ) -> "Config":
        # Make sure we are operating on a copy so we don't change
        # input dictionaries.
        snap_dict = deepcopy(snap_dict)
        snap_dict = apply_extensions(snap_dict)

        validator = Validator(snap_dict)
        validator.validate()

        snap_dict = _expand_filesets(snap_dict)
        snap_dict = _expand_env(snap_dict=snap_dict, environment=global_environment)
        snap_dict["architectures"] = _process_architectures(
            snap_dict.get("architectures"), project
        )
        expanded_snapcraft_yaml = deepcopy(snap_dict)

        # Using pop() so we can catch if we *miss* fields
        # with whatever remains in the dictionary.
        adopt_info = snap_dict.pop("adopt-info", None)
        architectures = snap_dict.pop("architectures", None)

        # Process apps into Applications.
        apps: Dict[str, Application] = dict()
        apps_dict = snap_dict.pop("apps", None)
        if apps_dict:
            for app_name, app_dict in apps_dict.items():
                app = Application.from_dict(app_dict=app_dict, app_name=app_name)
                apps[app_name] = app

        # Treat `assumes` as a set, not as a list.
        assumes = set(snap_dict.pop("assumes", set()))

        base = snap_dict.pop("base", None)
        build_base = snap_dict.pop("build-base", None)

        # Build packages must be processed as a grammar
        # after package repositories are configured.
        build_packages: Set[str] = set()

        compression = snap_dict.pop("compression", None)
        confinement = snap_dict.pop("confinement", None)
        description = snap_dict.pop("description", None)
        environment = snap_dict.pop("environment", None)
        epoch = snap_dict.pop("epoch", None)
        grade = snap_dict.pop("grade", None)

        # Process hooks into Hooks.
        hooks: Dict[str, Hook] = dict()
        hooks_dict = snap_dict.pop("hooks", None)
        if hooks_dict:
            for hook_name, hook_dict in hooks_dict.items():
                # This can happen, but should be moved into Hook.from_object().
                if hook_dict is None:
                    continue

                hook = Hook.from_dict(hook_dict=hook_dict, hook_name=hook_name)
                hooks[hook_name] = hook

        layout = snap_dict.pop("layout", None)
        license = snap_dict.pop("license", None)
        name = snap_dict.pop("name", None)

        raw_repositories = snap_dict.pop("package-repositories", None)
        if raw_repositories is None:
            package_repositories = None
        else:
            package_repositories = PackageRepository.unmarshal_package_repositories(
                raw_repositories
            )

        parts = PartsConfig(parts=snap_dict, project=project, validator=validator)

        passthrough = snap_dict.pop("passthrough", None)

        # Process plugs into Plugs.
        plugs: Dict[str, Plug] = dict()
        plugs_dict = snap_dict.pop("plugs", None)
        if plugs_dict:
            for plug_name, plug_object in plugs_dict.items():
                plug = Plug.from_object(plug_object=plug_object, plug_name=plug_name)
                plugs[plug_name] = plug

        # Process slots into Slots.
        slots: Dict[str, Slot] = dict()
        slots_dict = snap_dict.pop("slots", None)
        if slots_dict:
            for slot_name, slot_object in slots_dict.items():
                slot = Slot.from_object(slot_object=slot_object, slot_name=slot_name)
                slots[slot_name] = slot

        summary = snap_dict.pop("summary", None)

        # Process sytemusers into SystemUsers.
        system_usernames: Dict[str, SystemUser] = dict()
        system_usernames_dict = snap_dict.pop("system-usernames", None)
        if system_usernames_dict:
            for user_name, user_object in system_usernames_dict.items():
                system_username = SystemUser.from_object(
                    user_object=user_object, user_name=user_name
                )
                system_usernames[user_name] = system_username

        title = snap_dict.pop("title", None)
        type = snap_dict.pop("type", None)
        version = snap_dict.pop("version", None)

        # Report unhandled keys.
        for key, value in snap_dict.items():
            logger.debug(f"ignoring or passing through unknown {key}={value}")

        return Config(
            adopt_info=adopt_info,
            architectures=architectures,
            apps=apps,
            assumes=assumes,
            base=base,
            build_base=build_base,
            build_packages=build_packages,
            compression=compression,
            confinement=confinement,
            description=description,
            environment=environment,
            epoch=epoch,
            expanded_snapcraft_yaml=expanded_snapcraft_yaml,
            grade=grade,
            hooks=hooks,
            layout=layout,
            license=license,
            name=name,
            parts=parts,
            passthrough=passthrough,
            package_repositories=package_repositories,
            plugs=plugs,
            slots=slots,
            snap_dict=snap_dict,
            summary=summary,
            system_usernames=system_usernames,
            title=title,
            type=type,
            version=version,
        )

    @classmethod
    def from_file(cls, snap_yaml_path: str) -> "Config":
        with open(snap_yaml_path, "r") as f:
            snap_dict = yaml_utils.load(f)
            return cls.unmarshal(snap_dict=snap_dict)

    def marshal(self):  # noqa: C901
        snap_dict = OrderedDict()

        if self.name is not None:
            snap_dict["name"] = self.name

        if self.version is not None:
            snap_dict["version"] = self.version

        if self.summary is not None:
            snap_dict["summary"] = self.summary

        if self.description is not None:
            snap_dict["description"] = self.description

        if self.adopt_info is not None:
            snap_dict["adopt-info"] = self.adopt_info

        if self.apps:
            snap_dict["apps"] = OrderedDict()
            for name, app in sorted(self.apps.items()):
                snap_dict["apps"][name] = deepcopy(app.to_dict())

        if self.architectures:
            snap_dict["architectures"] = deepcopy(self.architectures)

        if self.assumes:
            snap_dict["assumes"] = sorted(set(deepcopy(self.assumes)))

        if self.base is not None:
            snap_dict["base"] = self.base

        if self.build_base is not None:
            snap_dict["build-base"] = self.build_base

        if self.compression is not None:
            snap_dict["compression"] = self.compression

        if self.confinement is not None:
            snap_dict["confinement"] = self.confinement

        if self.environment:
            snap_dict["environment"] = self.environment

        if self.epoch is not None:
            snap_dict["epoch"] = self.epoch

        if self.grade is not None:
            snap_dict["grade"] = self.grade

        if self.hooks:
            snap_dict["hooks"] = OrderedDict()
            for name, hook in sorted(self.hooks.items()):
                snap_dict["hooks"][name] = deepcopy(hook.to_dict())

        if self.layout:
            snap_dict["layout"] = deepcopy(self.layout)

        if self.license is not None:
            snap_dict["license"] = self.license

        package_repos = [repo.marshal() for repo in self.package_repositories]
        if package_repos:
            snap_dict["package-repositories"] = package_repos

        # TODO: parts

        if self.passthrough:
            snap_dict["passthrough"] = deepcopy(self.passthrough)

        if self.plugs:
            snap_dict["plugs"] = OrderedDict()
            for name, plug in sorted(self.plugs.items()):
                snap_dict["plugs"][name] = deepcopy(plug.to_yaml_object())

        if self.slots:
            snap_dict["slots"] = OrderedDict()
            for name, slot in sorted(self.slots.items()):
                snap_dict["slots"][name] = deepcopy(slot.to_yaml_object())

        if self.system_usernames:
            snap_dict["system-usernames"] = OrderedDict()
            for name, user in sorted(self.system_usernames.items()):
                snap_dict["system-usernames"][name] = deepcopy(user.to_dict())

        if self.title is not None:
            snap_dict["title"] = self.title

        if self.type is not None:
            snap_dict["type"] = self.type

        return snap_dict

    def __repr__(self) -> str:
        return repr(self.__dict__)

    def __str__(self) -> str:
        return str(self.__dict__)

    @property
    def part_names(self):
        return self.parts.part_names

    @property
    def all_parts(self):
        return self.parts.all_parts
