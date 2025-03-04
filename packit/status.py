# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import logging
from datetime import datetime, timedelta
from typing import Dict, List, Set, Tuple

from koji import BUILD_STATES, ClientSession

from ogr.abstract import Release
from packit.config import Config
from packit.config.common_package_config import CommonPackageConfig
from packit.copr_helper import CoprHelper
from packit.distgit import DistGit
from packit.exceptions import PackitException
from packit.upstream import Upstream
from packit.utils.bodhi import get_bodhi_client
from packit.constants import KOJI_BASEURL

logger = logging.getLogger(__name__)


class Status:
    """
    This class provides methods to obtain status of the package
    """

    def __init__(
        self,
        config: Config,
        package_config: CommonPackageConfig,
        upstream: Upstream,
        distgit: DistGit,
    ):
        self.config = config
        self.package_config = package_config

        self.up = upstream
        self.dg = distgit

    def get_downstream_prs(self, number_of_prs: int = 5) -> List[Tuple[int, str, str]]:
        """
        Get specific number of latest downstream PRs
        :param number_of_prs: int
        :return: List of downstream PRs
        """
        table: List[Tuple[int, str, str]] = []
        pr_list = self.dg.local_project.git_project.get_pr_list()
        logger.debug("Downstream PRs fetched.")
        if len(pr_list) > 0:
            # take last `number_of_prs` PRs
            pr_list = (
                pr_list[:number_of_prs] if len(pr_list) > number_of_prs else pr_list
            )
            table = [(pr.id, pr.title, pr.url) for pr in pr_list]
        return table

    def get_dg_versions(self) -> Dict:
        """
        Get versions from all branches in Dist-git
        :return: Dict {"branch": "version"}
        """
        dg_versions = {}
        branches = self.dg.local_project.git_project.get_branches()
        logger.debug("Dist-git branches fetched.")
        for branch in branches:
            try:
                self.dg.create_branch(
                    branch, base=f"remotes/origin/{branch}", setup_tracking=False
                )
                self.dg.checkout_branch(branch)
                self.dg.specfile.reload()
            except Exception as ex:
                logger.debug(f"Branch {branch!r} is not present: {ex!r}.")
                continue
            try:
                dg_versions[branch] = self.dg.specfile.expanded_version
            except AttributeError:
                logger.debug(f"Can't figure out the version of branch: {branch}.")
        self.dg.checkout_branch()

        return dg_versions

    def get_up_releases(self, number_of_releases: int = 5) -> List:
        """
        Get specific number of latest upstream releases
        :param number_of_releases: int
        :return: List
        """
        if self.up.local_project.git_project is None:
            logger.info("We couldn't track any upstream releases.")
            return []

        latest_releases: List[Release] = []
        try:
            latest_releases = self.up.local_project.git_project.get_releases()
            logger.debug("Upstream releases fetched.")
        except PackitException as e:
            logger.debug(f"Failed to fetch upstream releases: {e}")

        return latest_releases[:number_of_releases]

    def get_koji_builds(
        self,
    ) -> Dict:
        """
        Get latest koji builds as a dict of branch: latest build in that branch.
        """
        session = ClientSession(baseurl=KOJI_BASEURL)
        package_id = session.getPackageID(
            self.dg.package_config.downstream_package_name
        )
        # This method returns only latest builds,
        # so we don't need to get whole build history from Koji,
        # get just recent year to speed things up.
        since = datetime.now() - timedelta(days=365)
        builds_l = session.listBuilds(
            packageID=package_id,
            state=BUILD_STATES["COMPLETE"],
            completeAfter=since.timestamp(),
        )
        logger.debug(f"Recent Koji builds fetched: {[b['nvr'] for b in builds_l]}")
        # Select latest build for each branch.
        # [{'nvr': 'python-ogr-0.5.0-1.fc29'}, {'nvr':'python-ogr-0.6.0-1.fc29'}]
        # -> {'fc29': 'python-ogr-0.6.0-1.fc29'}
        return {b["nvr"].rsplit(".", 1)[1]: b["nvr"] for b in reversed(builds_l)}

    def get_updates(self, number_of_updates: int = 3) -> List:
        """
        Get specific number of latest updates in bodhi
        :param number_of_updates: int
        :return: None
        """
        bodhi_client = get_bodhi_client()
        updates = []
        page = pages = 1
        while page <= pages:
            results = bodhi_client.query(
                packages=self.dg.package_config.downstream_package_name,
                page=page,
            )
            updates.extend(results["updates"])
            page += 1
            pages = results["pages"]
        logger.debug("Bodhi updates fetched.")

        stable_branches: Set[str] = set()
        all_updates = [
            [
                update["title"],
                update["karma"],
                update["status"],
                update["release"]["branch"],
            ]
            for update in updates
        ]
        updates = []
        for [update, karma, status, branch] in all_updates:
            # Don't return more than one stable update per branch
            if branch not in stable_branches or status != "stable":
                updates.append([update, karma, status])
                if status == "stable":
                    stable_branches.add(branch)
            if len(updates) == number_of_updates:
                break
        return updates

    def get_copr_builds(self, number_of_builds: int = 5) -> List:
        return CoprHelper(upstream_local_project=self.up.local_project).get_copr_builds(
            number_of_builds=number_of_builds
        )
