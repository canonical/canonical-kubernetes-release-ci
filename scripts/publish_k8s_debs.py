#!/usr/bin/env python3

import argparse
import logging
import os
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Union

from jinja2 import Environment, FileSystemLoader, select_autoescape
from packaging.version import InvalidVersion, Version
from pydantic import BaseModel, SecretStr
from util.repo import clone
from util.util import execute, setup_arguments

LOG = logging.getLogger(__name__)

PRE_TO_RISK = {
    "a": "alpha",
    "b": "beta",
    "rc": "rc",
}


def _get_ubuntu_codename() -> str:
    """Get the Ubuntu codename from /etc/os-release."""
    os_release_path = Path("/etc/os-release")
    with open(os_release_path, "r") as f:
        for line in f:
            if line.startswith("VERSION_CODENAME="):
                return line.strip().split("=")[1].strip('"')
    raise RuntimeError(f"Unable to find VERSION_CODENAME in {os_release_path}")


class Credentials(BaseModel):
    """Credentials for the builder."""

    debs_gpg_key: SecretStr
    debs_full_name: str
    debs_email: str
    debs_lp_account: str

    @classmethod
    def get_creds_from_env(cls) -> "Credentials":
        """Get credentials from environment variables."""
        debs_gpg_key = os.getenv("DEBS_GPG_KEY")
        if not debs_gpg_key:
            raise ValueError("DEBS_GPG_KEY environment variable is not set")
        debs_full_name = os.getenv("DEBS_FULL_NAME")
        if not debs_full_name:
            raise ValueError("DEBS_FULL_NAME environment variable is not set")
        debs_email = os.getenv("DEBS_EMAIL")
        if not debs_email:
            raise ValueError("DEBS_EMAIL environment variable is not set")
        debs_lp_account = os.getenv("DEBS_LP_ACCOUNT")
        if not debs_lp_account:
            raise ValueError("DEBS_LP_ACCOUNT environment variable is not set")
        return cls(
            debs_gpg_key=SecretStr(debs_gpg_key),
            debs_full_name=debs_full_name,
            debs_email=debs_email,
            debs_lp_account=debs_lp_account,
        )


class K8sDebManager:
    """K8sDebManager is responsible for building and publishing Debian packages for Kubernetes components."""

    def __init__(
        self,
        repo_tag: str,
        component: str,
        version_postfix: str,
        creds: Credentials,
        dry_run: bool,
        stable_ppa: bool = True,
    ):
        """
        Initialize the K8sDebManager.

        Args:
            repo_tag (str): The git tag of the Kubernetes repository (e.g. v1.33.0).
            component (str): The name of the Kubernetes component to build (e.g. kubeadm).
            version_postfix (str): The version postfix for the Debian package (e.g. 1).
            creds (Credentials): The credentials for building and publishing.
            dry_run (bool): If True, do not publish the package.
            stable_ppa (bool): If True, use the stable PPA for publishing instead of the test PPA.
                Test PPA is going to be the same as stable PPA with a `-test` suffix. Example:
                    Stable PPA:  canonical-kubernetes/v1.33
                    Test PPA:    canonical-kubernetes/v1.33-test
        """
        self._jinja_env = Environment(
            # NOTE(Hue): We need to start the path with `scripts` because
            # the script is executed from the root of the repo.
            loader=FileSystemLoader("scripts/templates/publish_k8s_debs/"),
            autoescape=select_autoescape(),
        )

        self._repo_tag = repo_tag
        self._component = component
        self._version_postfix = version_postfix
        self._debs_gpg_key = creds.debs_gpg_key
        self._debs_full_name = creds.debs_full_name
        self._debs_email = creds.debs_email
        self._dry_run = dry_run
        self._debs_lp_account = creds.debs_lp_account
        self._stable_ppa = stable_ppa

    @property
    def _debian_dir(self) -> Path:
        debian_dir = self._repo_dir / "debian"
        os.makedirs(debian_dir, exist_ok=True)
        return debian_dir

    @property
    def _source_dir(self) -> Path:
        source_dir = self._debian_dir / "source"
        os.makedirs(source_dir, exist_ok=True)
        return source_dir

    @property
    def _deb_version(self) -> str:
        v = f"{self._k8s_version.major}.{self._k8s_version.minor}.{self._k8s_version.micro}"
        if self._k8s_version.is_prerelease and len(self._k8s_version.pre) > 2:  # type: ignore
            pre = f"{PRE_TO_RISK.get(self._k8s_version.pre[0], self._k8s_version.pre[0])}.{self._k8s_version.pre[1]}"  # type: ignore
            v = f"{v}-{pre}"
        return f"{v}-{self._version_postfix}"

    @property
    def _k8s_version(self) -> Version:
        try:
            k8s_version = Version(self._repo_tag)
        except InvalidVersion as e:
            raise ValueError(f"Invalid version tag: {self._repo_tag}") from e
        return k8s_version

    @property
    def _ppa_name(self) -> str:
        maj_min = f"{self._k8s_version.major}.{self._k8s_version.minor}"
        stable_ppa = f"{self._debs_lp_account}/v{maj_min}"
        if self._stable_ppa:
            LOG.info("Using stable PPA: %s", stable_ppa)
            return stable_ppa
        LOG.info("Using test PPA: %s", stable_ppa)
        return f"{stable_ppa}-test"

    def _create_changelog(self, ubuntu_codename: str) -> None:
        """Create the changelog file."""
        changelog_path = self._debian_dir / "changelog"
        changelog_tmpl = self._jinja_env.get_template("changelog.j2")
        context = {
            "component": self._component,
            "deb_version": self._deb_version,
            "ubuntu_codename": ubuntu_codename,
            "full_name": self._debs_full_name,
            "email": self._debs_email,
            "date": datetime.now().astimezone().strftime("%a, %d %b %Y %H:%M:%S %z"),
        }
        with open(changelog_path, "w") as dst:
            dst.write(changelog_tmpl.render(context))

    def _create_control(self) -> None:
        """Create the control file."""
        control_path = self._debian_dir / "control"
        control_tmpl = self._jinja_env.get_template("control.j2")
        context = {
            "component": self._component,
            "section": "utils",
            "priority": "optional",
            "maintainer_name": self._debs_full_name,
            "maintainer_email": self._debs_email,
            "description_short": f"Debian package for {self._component}",
            "description_long": f"Debian package for {self._component} component of Kubernetes. Published and maintained by Canonical.",
        }
        with open(control_path, "w") as dst:
            dst.write(control_tmpl.render(context))

    def _create_copyright(self) -> None:
        """Create the copyright file."""
        copyright_path = self._debian_dir / "copyright"
        copyright_tmpl = self._jinja_env.get_template("copyright.j2")
        context = {
            "component": self._component,
            "full_name": self._debs_full_name,
            "email": self._debs_email,
            "year": datetime.now().strftime("%Y"),
        }
        with open(copyright_path, "w") as dst:
            dst.write(copyright_tmpl.render(context))

    def _create_docs(self) -> None:
        """Create documentation files."""
        readme_filename = "README"
        readme_path = self._debian_dir / readme_filename
        readme_tmpl = self._jinja_env.get_template("README.j2")
        context = {
            "component": self._component,
            "full_name": self._debs_full_name,
            "email": self._debs_email,
            "date": datetime.now().astimezone().strftime("%a, %d %b %Y %H:%M:%S %z"),
        }
        with open(readme_path, "w") as dst:
            dst.write(readme_tmpl.render(context))

        docs_path = self._debian_dir / f"{self._component}-docs.docs"
        with open(docs_path, "w") as dst:
            dst.write("README\n")

    def _create_rules(self) -> None:
        """Create the rules file."""
        rules_path = self._debian_dir / "rules"
        rules_tmpl = self._jinja_env.get_template("rules.j2")
        with open(rules_path, "w") as dst:
            dst.write(rules_tmpl.render())

    def _create_source_format(self) -> None:
        """Create the source/format file."""
        format_path = self._source_dir / "format"
        format_tmpl = self._jinja_env.get_template("source_format.j2")
        with open(format_path, "w") as dst:
            dst.write(format_tmpl.render())

    def _create_source_options(self) -> None:
        """Create the source/options file."""
        options_path = self._source_dir / "options"
        options_tmpl = self._jinja_env.get_template("source_options.j2")
        with open(options_path, "w") as dst:
            dst.write(options_tmpl.render())

    def _replace_makefile(self) -> None:
        """Replace the Makefile with a custom one."""
        makefile = self._repo_dir / "Makefile"
        orig_makefile = self._repo_dir / "Makefile.original"

        if os.path.exists(orig_makefile):
            raise FileExistsError(f"Original Makefile already exists: {orig_makefile}")

        os.rename(makefile, orig_makefile)

        context = {
            "component": self._component,
        }
        makefile_tmpl = self._jinja_env.get_template("Makefile.j2")
        with open(makefile, "w") as f:
            f.write(makefile_tmpl.render(context))

    def _create_debian_package_structure(self, ubuntu_codename: str):
        """Create the Debian package structure."""
        LOG.info("Creating changelog file")
        self._create_changelog(ubuntu_codename=ubuntu_codename)
        LOG.info("Creating control file")
        self._create_control()
        LOG.info("Creating copyright file")
        self._create_copyright()
        LOG.info("Creating docs file")
        self._create_docs()
        LOG.info("Creating rules file")
        self._create_rules()
        LOG.info("Creating source/format file")
        self._create_source_format()
        LOG.info("Creating source/options file")
        self._create_source_options()
        LOG.info("Replacing Makefile")
        self._replace_makefile()

    def _extract_go_version(self) -> Version:
        """Extract the Go version from the go.mod file."""
        go_ver_path = self._repo_dir / ".go-version"
        if not os.path.exists(go_ver_path):
            raise FileNotFoundError(f".go-version file not found in {self._repo_dir}")

        with open(go_ver_path, "r") as f:
            return Version(f.read().strip())

    def _download_go_tar(self, go_version: Version, to: Union[str, Path]) -> str:
        """Download the Go tarball for the specified version."""
        tarball = f"go{go_version}.linux-amd64.tar.gz"
        url = f"https://go.dev/dl/{tarball}"

        try:
            execute(["wget", "--progress=dot:giga", url], cwd=to, timeout=None)
        except subprocess.CalledProcessError as e:
            raise RuntimeError(
                f"Failed to wget url: CODE: {e.returncode}\nSTDERR: {e.stderr}\nSTDOUT: {e.stdout}"
            )

        return tarball

    def _extract_tar(self, path: Union[str, Path], wd: Union[str, Path]) -> None:
        """Extract the tarball to the specified directory."""
        try:
            execute(["tar", "xf", str(path)], cwd=wd, timeout=None)
        except subprocess.CalledProcessError as e:
            raise RuntimeError(
                f"Failed to extract tar file: CODE: {e.returncode}\nSTDERR: {e.stderr}\nSTDOUT: {e.stdout}"
            )

    def _vendor_go_runtime(self):
        """Vendor the Go runtime into the Debian package."""
        go_version = self._extract_go_version()
        LOG.info("Downloading Go runtime version %s", go_version)
        tarball = self._download_go_tar(go_version, to=self._debian_dir)
        self._extract_tar(tarball, wd=self._debian_dir)
        LOG.info("Extracted Go runtime tarball %s", tarball)
        os.remove(self._debian_dir / tarball)

    def _build_source_package(self):
        """Build the source package using debuild."""
        try:
            out, _ = execute(
                ["debuild", "-S"],
                cwd=self._repo_dir,
                timeout=None,
            )
            LOG.info("debuild output: \n%s", out)
        except subprocess.CalledProcessError as e:
            raise RuntimeError(
                f"Failed to build source package: CODE: {e.returncode}\nSTDERR: {e.stderr}\nSTDOUT: {e.stdout}"
            )

    def _upload_to_ppa(self):
        """Upload the source package to the PPA using dput."""
        changes_file = f"{self._component}_{self._deb_version}_source.changes"
        changes_path = self._repo_dir.parent / changes_file
        if not os.path.exists(changes_path):
            raise FileNotFoundError(f"Changes file not found: {changes_file}")

        LOG.info("Uploading changes file %s to %s", changes_file, self._ppa_name)
        try:
            out, _ = execute(
                ["dput", f"ppa:{self._ppa_name}", changes_file],
                cwd=self._repo_dir.parent,
                timeout=None,
            )
            LOG.info("dput output: \n%s", out)
        except subprocess.CalledProcessError as e:
            raise RuntimeError(
                f"Failed to publish source package: CODE: {e.returncode}\nSTDERR: {e.stderr}\nSTDOUT: {e.stdout}"
            )

    def _configure_debuild(self):
        """Configure debuild with credentials and options"""
        devscripts_path = os.path.join(os.path.expanduser("~"), ".devscripts")
        devscripts_tmpl = self._jinja_env.get_template("devscripts.j2")
        context = {
            "gpg_key": self._debs_gpg_key.get_secret_value(),
        }
        LOG.info("Creating devscripts file %s", devscripts_path)
        with open(devscripts_path, "w") as dst:
            dst.write(devscripts_tmpl.render(context))

    def _build_deb(self, build_dir: str):
        """Build the Debian package."""
        ubuntu_codename = _get_ubuntu_codename()
        LOG.info("Got Ubuntu codename: %s", ubuntu_codename)
        LOG.info("Cloning Kubernetes repo at branch %s", self._repo_tag)
        with clone(
            repo_url="https://github.com/kubernetes/kubernetes.git",
            repo_tag=self._repo_tag,
            shallow=True,
            base_dir=build_dir,
        ) as dir:
            self._repo_dir = dir
            LOG.info("Cloned Kubernetes repo in %s", self._repo_dir)
            LOG.info("Creating Debian package structure...")
            self._create_debian_package_structure(ubuntu_codename=ubuntu_codename)
            LOG.info("Vendoring Go runtime...")
            self._vendor_go_runtime()
            LOG.info("Configuring debuild...")
            self._configure_debuild()
            LOG.info("Building source package...")
            self._build_source_package()
            LOG.info(
                "Successfully built source package %s_%s",
                self._component,
                self._deb_version,
            )

    def _publish_deb(self):
        """Publish the Debian package to the PPA."""
        LOG.info("Uploading source package to PPA...")
        self._upload_to_ppa()

    def run(self):
        """Run the main workflow."""
        with tempfile.TemporaryDirectory() as tmpdir:
            LOG.info(
                "Building %s_%s deb source package in %s...",
                self._component,
                self._deb_version,
                tmpdir,
            )
            self._build_deb(build_dir=tmpdir)
            if self._dry_run:
                LOG.info("Dry run mode enabled. Will not publish.")
                return

            LOG.info(
                "Publishing %s_%s deb source package...",
                self._component,
                self._deb_version,
            )
            self._publish_deb()
            LOG.info("Package published successfully.")


def main():
    arg_parser = argparse.ArgumentParser(
        Path(__file__).name,
        description="Build and publish Debian package for a Kubernetes component.",
    )
    arg_parser.add_argument("component", help="Component name, e.g., kubeadm")
    arg_parser.add_argument(
        "--tag", required=True, help="Git tag of Kubernetes (e.g., v1.32.3)"
    )
    arg_parser.add_argument("--version-postfix", required=True, help="Version postfix")
    arg_parser.add_argument(
        "--stable-ppa",
        default=False,
        action="store_true",
        help="Use stable PPA instead of test PPA",
    )
    args = setup_arguments(arg_parser)

    LOG.info(
        "Building %s from tag %s with version postfix %s",
        args.component,
        args.tag,
        args.version_postfix,
    )

    deb_manager = K8sDebManager(
        repo_tag=args.tag,
        component=args.component,
        version_postfix=args.version_postfix,
        creds=Credentials.get_creds_from_env(),
        dry_run=args.dry_run,
        stable_ppa=args.stable_ppa,
    )

    deb_manager.run()


if __name__ == "__main__":
    main()
