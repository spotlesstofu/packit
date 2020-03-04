# MIT License
#
# Copyright (c) 2018-2019 Red Hat, Inc.

# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

import json
import logging
import os
from glob import glob
from pathlib import Path
from typing import Optional, List, Dict, Union

from yaml import safe_load

from ogr.abstract import GitProject
from packit.actions import ActionName
from packit.constants import CONFIG_FILE_NAMES, PROD_DISTGIT_URL
from packit.config.job_config import JobConfig, default_jobs
from packit.config.sync_files_config import SyncFilesConfig, SyncFilesItem
from packit.exceptions import PackitConfigException

logger = logging.getLogger(__name__)


class PullRequestNotificationsConfig:
    """ Configuration of commenting on pull requests. """

    def __init__(self, successful_build: bool = True):
        self.successful_build = successful_build


class NotificationsConfig:
    """ Configuration of notifications. """

    def __init__(self, pull_request: PullRequestNotificationsConfig):
        self.pull_request = pull_request


class PackageConfig:
    """
    Config class for upstream/downstream packages;
    this is the config people put in their repos
    """

    def __init__(
        self,
        config_file_path: Optional[str] = None,
        specfile_path: Optional[str] = None,
        synced_files: Optional[SyncFilesConfig] = None,
        jobs: Optional[List[JobConfig]] = None,
        dist_git_namespace: str = None,
        upstream_project_url: str = None,  # can be URL or path
        upstream_package_name: str = None,
        downstream_project_url: str = None,
        downstream_package_name: str = None,
        dist_git_base_url: str = None,
        create_tarball_command: List[str] = None,
        current_version_command: List[str] = None,
        actions: Dict[ActionName, Union[str, List[str]]] = None,
        upstream_ref: Optional[str] = None,
        allowed_gpg_keys: Optional[List[str]] = None,
        create_pr: bool = True,
        spec_source_id: str = "Source0",
        upstream_tag_template: str = "{version}",
        patch_generation_ignore_paths: List[str] = None,
        notifications: Optional[NotificationsConfig] = None,
        **kwargs,
    ):
        self.config_file_path: Optional[str] = config_file_path
        self.specfile_path: Optional[str] = specfile_path
        self.synced_files: SyncFilesConfig = synced_files or SyncFilesConfig([])
        self.patch_generation_ignore_paths = patch_generation_ignore_paths or []
        self.jobs: List[JobConfig] = jobs or []
        self.dist_git_namespace: str = dist_git_namespace or "rpms"
        self.upstream_project_url: Optional[str] = upstream_project_url
        self.upstream_package_name: Optional[str] = upstream_package_name
        # this is generated by us
        self.downstream_package_name: Optional[str] = downstream_package_name
        self.dist_git_base_url: str = dist_git_base_url or PROD_DISTGIT_URL
        self._downstream_project_url: str = downstream_project_url
        # path to a local git clone of the dist-git repo; None means to clone in a tmpdir
        self.dist_git_clone_path: Optional[str] = None
        self.actions = actions or {}
        self.upstream_ref: Optional[str] = upstream_ref
        self.allowed_gpg_keys = allowed_gpg_keys
        self.create_pr: bool = create_pr
        self.spec_source_id: str = spec_source_id
        self.notifications = notifications or NotificationsConfig(
            pull_request=PullRequestNotificationsConfig()
        )

        # command to generate a tarball from the upstream repo
        # uncommitted changes will not be present in the archive
        self.create_tarball_command: List[str] = create_tarball_command
        # command to get current version of the project
        self.current_version_command: List[str] = current_version_command or [
            "git",
            "describe",
            "--tags",
            "--match",
            "*",
        ]
        # template to create an upstream tag name (upstream may use different tagging scheme)
        self.upstream_tag_template = upstream_tag_template

        if kwargs:
            logger.warning(f"Following kwargs were not processed:" f"{kwargs}")

    @property
    def downstream_project_url(self) -> str:
        if not self._downstream_project_url:
            self._downstream_project_url = self.dist_git_package_url
        return self._downstream_project_url

    @property
    def dist_git_package_url(self):
        return (
            f"{self.dist_git_base_url}{self.dist_git_namespace}/"
            f"{self.downstream_package_name}.git"
        )

    @classmethod
    def get_from_dict(
        cls,
        raw_dict: dict,
        config_file_path: str = None,
        repo_name: str = None,
        spec_file_path: str = None,
    ) -> "PackageConfig":
        # required to avoid cyclical imports
        from packit.schema import PackageConfigSchema

        if config_file_path and not raw_dict.get("config_file_path", None):
            raw_dict.update(config_file_path=config_file_path)

        package_config = PackageConfigSchema(strict=True).load(raw_dict).data

        if not getattr(package_config, "specfile_path", None):
            if spec_file_path:
                package_config.specfile_path = spec_file_path
            else:
                raise PackitConfigException("Spec file was not found!")

        if not getattr(package_config, "upstream_package_name", None) and repo_name:
            package_config.upstream_package_name = repo_name

        if not getattr(package_config, "downstream_package_name", None) and repo_name:
            package_config.downstream_package_name = repo_name

        if "jobs" not in raw_dict:
            package_config.jobs = default_jobs

        return package_config

    def get_all_files_to_sync(self):
        """
        Adds the default files (config file, spec file) to synced files when doing propose-update.
        :return: SyncFilesConfig with default files
        """
        files = self.synced_files.files_to_sync

        if self.specfile_path not in (item.src for item in files):
            files.append(SyncFilesItem(src=self.specfile_path, dest=self.specfile_path))

        if self.config_file_path and self.config_file_path not in (
            item.src for item in files
        ):
            files.append(
                SyncFilesItem(src=self.config_file_path, dest=self.config_file_path)
            )

        return SyncFilesConfig(files)

    def __eq__(self, other: object):
        if not isinstance(other, self.__class__):
            return NotImplemented
        logger.debug(f"our configuration:\n{self.__dict__}")
        logger.debug(f"the other configuration:\n{other.__dict__}")
        return (
            self.specfile_path == other.specfile_path
            and self.synced_files == other.synced_files
            and self.jobs == other.jobs
            and self.dist_git_namespace == other.dist_git_namespace
            and self.upstream_project_url == other.upstream_project_url
            and self.upstream_package_name == other.upstream_package_name
            and self.downstream_project_url == other.downstream_project_url
            and self.downstream_package_name == other.downstream_package_name
            and self.dist_git_base_url == other.dist_git_base_url
            and self.current_version_command == other.current_version_command
            and self.create_tarball_command == other.create_tarball_command
            and self.actions == other.actions
            and self.allowed_gpg_keys == other.allowed_gpg_keys
            and self.create_pr == other.create_pr
            and self.spec_source_id == other.spec_source_id
            and self.upstream_tag_template == other.upstream_tag_template
        )


def get_local_package_config(
    *directory,
    repo_name: str = None,
    try_local_dir_first=False,
    try_local_dir_last=False,
) -> PackageConfig:
    """
    :return: local PackageConfig if present
    """
    directories = [Path(config_dir) for config_dir in directory]
    cwd = Path.cwd()

    if try_local_dir_first and try_local_dir_last:
        logger.error("Ambiguous usage of try_local_dir_first and try_local_dir_last")

    if try_local_dir_first:
        if cwd in directories:
            directories.remove(cwd)
        directories.insert(0, cwd)

    if try_local_dir_last:
        if cwd in directories:
            directories.remove(cwd)
        directories.append(cwd)

    for config_dir in directories:
        for config_file_name in CONFIG_FILE_NAMES:
            config_file_name_full = config_dir / config_file_name
            if config_file_name_full.is_file():
                logger.debug(f"Local package config found: {config_file_name_full}")
                try:
                    loaded_config = safe_load(open(config_file_name_full))
                except Exception as ex:
                    logger.error(
                        f"Cannot load package config '{config_file_name_full}'."
                    )
                    raise PackitConfigException(f"Cannot load package config: {ex}.")
                return parse_loaded_config(
                    loaded_config=loaded_config,
                    config_file_path=str(config_file_name),
                    repo_name=repo_name,
                    spec_file_path=get_local_specfile_path(directories),
                )

            logger.debug(f"The local config file '{config_file_name_full}' not found.")
    raise PackitConfigException("No packit config found.")


def get_package_config_from_repo(
    sourcegit_project: GitProject, ref: str
) -> Optional[PackageConfig]:
    for config_file_name in CONFIG_FILE_NAMES:
        try:
            config_file_content = sourcegit_project.get_file_content(
                path=config_file_name, ref=ref
            )
        except FileNotFoundError:
            # do nothing
            pass
        else:
            logger.debug(
                f"Found a config file '{config_file_name}' "
                f"on ref '{ref}' "
                f"of the {sourcegit_project.full_repo_name} repository."
            )
            break
    else:
        logger.warning(
            f"No config file ({CONFIG_FILE_NAMES}) found on ref '{ref}' "
            f"of the {sourcegit_project.full_repo_name} repository."
        )
        return None

    try:
        loaded_config = safe_load(config_file_content)
    except Exception as ex:
        logger.error(f"Cannot load package config {config_file_name!r}. {ex}")
        raise PackitConfigException(
            f"Cannot load package config {config_file_name!r}. {ex}"
        )
    return parse_loaded_config(
        loaded_config=loaded_config,
        config_file_path=config_file_name,
        repo_name=sourcegit_project.repo,
        spec_file_path=get_specfile_path_from_repo(sourcegit_project),
    )


def parse_loaded_config(
    loaded_config: dict,
    config_file_path: str = None,
    repo_name: str = None,
    spec_file_path: str = None,
) -> PackageConfig:
    """Tries to parse the config to PackageConfig."""
    logger.debug(f"Package config:\n{json.dumps(loaded_config, indent=4)}")

    try:
        package_config = PackageConfig.get_from_dict(
            raw_dict=loaded_config,
            config_file_path=config_file_path,
            repo_name=repo_name,
            spec_file_path=spec_file_path,
        )
        return package_config
    except Exception as ex:
        logger.error(f"Cannot parse package config. {ex}.")
        raise PackitConfigException(f"Cannot parse package config: {ex}.")


def get_local_specfile_path(directories: Union[List[str], List[Path]]) -> Optional[str]:
    """
    Get the relative path of the local spec file if present.
    :param directories: dirs to find the spec file
    :return: str relative path of the spec file
    """
    for dir in directories:
        files = [
            os.path.relpath(path, dir) for path in glob(os.path.join(dir, "*.spec"))
        ]
        if len(files) > 0:
            return files[0]

    return None


def get_specfile_path_from_repo(project: GitProject) -> Optional[str]:
    """
    Get the path of the spec file in the given repo if present.
    :param project: GitProject
    :return: str path of the spec file or None
    """
    spec_files = project.get_files(filter_regex=r".+\.spec$")
    if not spec_files:
        logger.debug(f"No spec file found in {project.full_repo_name}")
        return None
    return spec_files[0]
