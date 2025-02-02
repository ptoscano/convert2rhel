# Copyright(C) 2016 Red Hat, Inc.
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

import itertools
import logging
import os
import os.path
import re
import shutil
import tempfile

import rpm

from convert2rhel import __version__ as installed_convert2rhel_version
from convert2rhel import grub, pkgmanager, utils
from convert2rhel.pkghandler import (
    call_yum_cmd,
    compare_package_versions,
    get_installed_pkg_information,
    get_total_packages_to_update,
    parse_pkg_string,
)
from convert2rhel.repo import get_hardcoded_repofiles_dir
from convert2rhel.systeminfo import system_info
from convert2rhel.toolopts import tool_opts
from convert2rhel.utils import ask_to_continue, get_file_content, run_subprocess, store_content_to_file


logger = logging.getLogger(__name__)

KERNEL_REPO_RE = re.compile(r"^.+:(?P<version>.+).el.+$")
KERNEL_REPO_VER_SPLIT_RE = re.compile(r"\W+")
BAD_KERNEL_RELEASE_SUBSTRINGS = ("uek", "rt", "linode")

RPM_GPG_KEY_PATH = os.path.join(utils.DATA_DIR, "gpg-keys", "RPM-GPG-KEY-redhat-release")
# The SSL certificate of the https://cdn.redhat.com/ server
SSL_CERT_PATH = os.path.join(utils.DATA_DIR, "redhat-uep.pem")
CDN_URL = "https://cdn.redhat.com/content/public/convert2rhel/$releasever/$basearch/os/"
CONVERT2RHEL_REPO_CONTENT = """\
[convert2rhel]
name=Convert2RHEL Repository
baseurl=%s
gpgcheck=1
enabled=1
sslcacert=%s
gpgkey=file://%s""" % (
    CDN_URL,
    SSL_CERT_PATH,
    RPM_GPG_KEY_PATH,
)

LINK_KMODS_RH_POLICY = "https://access.redhat.com/third-party-software-support"
LINK_PREVENT_KMODS_FROM_LOADING = "https://access.redhat.com/solutions/41278"
# The kernel version stays the same throughout a RHEL major version
COMPATIBLE_KERNELS_VERS = {
    7: "3.10.0",
    8: "4.18.0",
}

VMLINUZ_FILEPATH = "/boot/vmlinuz-%s"
"""The path to the vmlinuz file in a system."""

INITRAMFS_FILEPATH = "/boot/initramfs-%s.img"
"""The path to the initramfs image in a system."""

from functools import cmp_to_key


def perform_system_checks():
    """Early checks after system facts should be added here."""
    check_custom_repos_are_valid()
    check_convert2rhel_latest()
    check_efi()
    check_tainted_kmods()
    check_readonly_mounts()
    check_rhel_compatible_kernel_is_used()
    check_package_updates()
    is_loaded_kernel_latest()
    check_dbus_is_running()


def perform_pre_ponr_checks():
    """Late checks before ponr should be added here."""
    ensure_compatibility_of_kmods()
    validate_package_manager_transaction()


def check_convert2rhel_latest():
    """Make sure that we are running the latest downstream version of convert2rhel"""
    logger.task("Prepare: Check if this is the latest version of Convert2RHEL")

    if not system_info.has_internet_access:
        logger.warning("Skipping the check because no internet connection has been detected.")
        return

    repo_dir = tempfile.mkdtemp(prefix="convert2rhel_repo.", dir=utils.TMP_DIR)
    repo_path = os.path.join(repo_dir, "convert2rhel.repo")
    store_content_to_file(filename=repo_path, content=CONVERT2RHEL_REPO_CONTENT)

    cmd = [
        "repoquery",
        "--disablerepo=*",
        "--enablerepo=convert2rhel",
        "--releasever=%s" % system_info.version.major,
        "--setopt=reposdir=%s" % repo_dir,
        "--qf",
        "C2R %{NAME}-%{EPOCH}:%{VERSION}-%{RELEASE}.%{ARCH}",
        "convert2rhel",
    ]

    # Note: This is safe because we're creating in utils.TMP_DIR which is hardcoded to
    # /var/lib/convert2rhel which does not have any world-writable directory components.
    utils.mkdir_p(repo_dir)

    try:
        raw_output_convert2rhel_versions, return_code = run_subprocess(cmd, print_output=False)
    finally:
        shutil.rmtree(repo_dir)

    if return_code != 0:
        logger.warning(
            "Couldn't check if the current installed Convert2RHEL is the latest version.\n"
            "repoquery failed with the following output:\n%s" % (raw_output_convert2rhel_versions)
        )
        return

    # convert the raw output of convert2rhel version strings into a list
    raw_output_convert2rhel_versions = raw_output_convert2rhel_versions.splitlines()

    temp_raw_output = []

    # We are expecting an repoquery output to be similar to this:
    # C2R convert2rhel-0:0.17-1.el7.noarch
    # We need the `C2R` identifier to be present on the line so we can know for
    # sure that the line we are working with is the a line that contains
    # relevant repoquery information to our check, otherwise, we just log the
    # information as debug and do nothing with it.
    for raw_version in raw_output_convert2rhel_versions:
        if "C2R" in raw_version:
            temp_raw_output.append(raw_version.lstrip("C2R "))
        else:
            # Mainly for debugging purposes to see what is happening if we got
            # anything else that does not have the C2R identifier at the start
            # of the line.
            logger.debug("Got a line without the C2R identifier: %s" % raw_version)
    raw_output_convert2rhel_versions = temp_raw_output

    latest_available_version = ("0", "0.00", "0")
    convert2rhel_versions = []

    # add each tuple of fields obtained from parse_pkg_string() to convert2rhel_versions
    for raw_pkg in raw_output_convert2rhel_versions:
        try:
            parsed_pkg = parse_pkg_string(raw_pkg)

        except ValueError as exc:
            # Not a valid package string input
            logger.debug(exc)
            continue
        convert2rhel_versions.append(parsed_pkg)

    logger.debug("Found %s convert2rhel package(s)" % len(convert2rhel_versions))

    # This loop will determine the latest available convert2rhel version in the yum repo.
    # It assigns the epoch, version, and release ex: ("0", "0.26", "1.el7") to the latest_available_version variable.
    for package_version in convert2rhel_versions:
        logger.debug("...comparing version %s" % latest_available_version[1])
        # rpm.labelCompare(pkg1, pkg2) compare two package version strings and return
        # -1 if latest_version is greater than package_version, 0 if they are equal, 1 if package_version is greater than latest_version
        ver_compare = rpm.labelCompare(
            (package_version[1], package_version[2], package_version[3]), latest_available_version
        )

        if ver_compare > 0:
            logger.debug(
                "...found %s to be newer than %s, updating" % (package_version[2], latest_available_version[1])
            )
            latest_available_version = (package_version[1], package_version[2], package_version[3])

    logger.debug("Found %s to be latest available version" % (latest_available_version[1]))
    # After the for loop, the latest_available_version variable will gain the epoch, version, and release
    # (e.g. ("0" "0.26" "1.el7")) information from the Convert2RHEL yum repo
    # when the versions are the same the latest_available_version's release field will cause it to evaluate as a later version.
    # Therefore we need to hardcode "0" for both the epoch and release below for installed_convert2rhel_version
    # and latest_available_version respectively, to compare **just** the version field.
    ver_compare = rpm.labelCompare(("0", installed_convert2rhel_version, "0"), ("0", latest_available_version[1], "0"))
    if ver_compare < 0:
        # Current and deprecated env var names
        allow_older_envvar_names = ("CONVERT2RHEL_ALLOW_OLDER_VERSION", "CONVERT2RHEL_UNSUPPORTED_VERSION")
        if any(envvar in os.environ for envvar in allow_older_envvar_names):
            if "CONVERT2RHEL_ALLOW_OLDER_VERSION" not in os.environ:
                logger.warning(
                    "You are using the deprecated 'CONVERT2RHEL_UNSUPPORTED_VERSION'"
                    " environment variable.  Please switch to 'CONVERT2RHEL_ALLOW_OLDER_VERSION'"
                    " instead."
                )

            logger.warning(
                "You are currently running %s and the latest version of Convert2RHEL is %s.\n"
                "'CONVERT2RHEL_ALLOW_OLDER_VERSION' environment variable detected, continuing conversion"
                % (installed_convert2rhel_version, latest_available_version[1])
            )

        else:
            logger.critical(
                "You are currently running %s and the latest version of Convert2RHEL is %s.\n"
                "Only the latest version is supported for conversion. If you want to ignore"
                " this check, then set the environment variable 'CONVERT2RHEL_ALLOW_OLDER_VERSION=1' to continue."
                % (installed_convert2rhel_version, latest_available_version[1])
            )

    else:
        logger.info("Latest available Convert2RHEL version is installed.")


def check_efi():
    """Inhibit the conversion when we are not able to handle UEFI."""
    logger.task("Prepare: Check the firmware interface type (BIOS/UEFI)")
    if not grub.is_efi():
        logger.info("BIOS detected.")
        return
    logger.info("UEFI detected.")
    if not os.path.exists("/usr/sbin/efibootmgr"):
        logger.critical("Install efibootmgr to continue converting the UEFI-based system.")
    if system_info.arch != "x86_64":
        logger.critical("Only x86_64 systems are supported for UEFI conversions.")
    if grub.is_secure_boot():
        logger.info("Secure boot detected.")
        logger.critical(
            "The conversion with secure boot is currently not possible.\n"
            "To disable it, follow the instructions available in this article: https://access.redhat.com/solutions/6753681"
        )

    # Get information about the bootloader. Currently the data is not used, but it's
    # good to check that we can obtain all the required data before the PONR. Better to
    # stop now than after the PONR.
    try:
        efiboot_info = grub.EFIBootInfo()
    except grub.BootloaderError as e:
        logger.critical(e.message)

    if not efiboot_info.entries[efiboot_info.current_bootnum].is_referring_to_file():
        # NOTE(pstodulk): I am not sure what could be consequences after the conversion, as the
        # new UEFI bootloader entry is created referring to a RHEL UEFI binary.
        logger.warning(
            "The current UEFI bootloader '%s' is not referring to any binary UEFI"
            " file located on local EFI System Partition (ESP)." % efiboot_info.current_bootnum
        )
    # TODO(pstodulk): print warning when multiple orig. UEFI entries point
    # to the original system (e.g. into the centos/ directory..). The point is
    # that only the current UEFI bootloader entry is handled.
    # If e.g. on CentOS Linux, other entries with CentOS labels could be
    # invalid (or at least misleading) as the OS will be replaced by RHEL


def check_tainted_kmods():
    """Stop the conversion when a loaded tainted kernel module is detected.

    Tainted kmods ends with (...) in /proc/modules, for example:
        multipath 20480 0 - Live 0x0000000000000000
        linear 20480 0 - Live 0x0000000000000000
        system76_io 16384 0 - Live 0x0000000000000000 (OE)  <<<<<< Tainted
        system76_acpi 16384 0 - Live 0x0000000000000000 (OE) <<<<<< Tainted
    """
    logger.task("Prepare: Check if loaded kernel modules are not tainted")
    unsigned_modules, _ = run_subprocess(["grep", "(", "/proc/modules"])
    module_names = "\n  ".join([mod.split(" ")[0] for mod in unsigned_modules.splitlines()])
    if unsigned_modules:
        logger.critical(
            "Tainted kernel modules detected:\n  {0}\n"
            "Third-party components are not supported per our "
            "software support policy:\n {1}\n"
            "Prevent the modules from loading by following {2}"
            " and run convert2rhel again to continue with the conversion.".format(
                module_names, LINK_KMODS_RH_POLICY, LINK_PREVENT_KMODS_FROM_LOADING
            )
        )
    logger.info("No tainted kernel module is loaded.")


def check_readonly_mounts():
    """
    Mounting directly to /mnt/ is not in line with Unix FS (https://en.wikipedia.org/wiki/Unix_filesystem).
    Having /mnt/ and /sys/ read-only causes the installation of the filesystem package to
    fail (https://bugzilla.redhat.com/show_bug.cgi?id=1887513, https://github.com/oamg/convert2rhel/issues/123).
    """
    logger.task("Prepare: Check /mnt and /sys are read-write")

    mounts = get_file_content("/proc/mounts", as_list=True)
    for line in mounts:
        _, mount_point, _, flags, _, _ = line.split()
        flags = flags.split(",")
        if mount_point not in ("/mnt", "/sys"):
            continue
        if "ro" in flags:
            if mount_point == "/mnt":
                logger.critical(
                    "Stopping conversion due to read-only mount to /mnt directory.\n"
                    "Mount at a subdirectory of /mnt to have /mnt writeable."
                )
            else:  # /sys
                logger.critical(
                    "Stopping conversion due to read-only mount to /sys directory.\n"
                    "Ensure mount point is writable before executing convert2rhel."
                )
        logger.debug("%s mount point is not read-only." % mount_point)
    logger.info("Read-only /mnt or /sys mount points not detected.")


def check_custom_repos_are_valid():
    """To prevent failures past the PONR, make sure that the enabled custom repositories are valid.

    What is meant by valid:
    - YUM/DNF is able to find the repoids (to rule out a typo)
    - the repository "baseurl" is accessible and contains repository metadata
    """
    logger.task("Prepare: Check if --enablerepo repositories are accessible")

    if not tool_opts.no_rhsm:
        logger.info("Skipping the check of repositories due to the use of RHSM for the conversion.")
        return

    output, ret_code = call_yum_cmd(
        command="makecache",
        args=["-v", "--setopt=*.skip_if_unavailable=False"],
        print_output=False,
    )
    if ret_code != 0:
        logger.critical(
            "Unable to access the repositories passed through the --enablerepo option. "
            "For more details, see YUM/DNF output:\n{0}".format(output)
        )

    logger.debug("Output of the previous yum command:\n{0}".format(output))
    logger.info("The repositories passed through the --enablerepo option are all accessible.")


def ensure_compatibility_of_kmods():
    """Ensure that the host kernel modules are compatible with RHEL.

    :raises SystemExit: Interrupts the conversion because some kernel modules are not supported in RHEL.
    """
    host_kmods = get_loaded_kmods()
    rhel_supported_kmods = get_rhel_supported_kmods()
    unsupported_kmods = get_unsupported_kmods(host_kmods, rhel_supported_kmods)

    # Validate the best case first. If we don't have any unsupported_kmods, this means
    # that everything is compatible and good to go.
    if not unsupported_kmods:
        logger.debug("All loaded kernel modules are available in RHEL.")
    else:
        if "CONVERT2RHEL_ALLOW_UNAVAILABLE_KMODS" in os.environ:
            logger.warning(
                "Detected 'CONVERT2RHEL_ALLOW_UNAVAILABLE_KMODS' environment variable."
                " We will continue the conversion with the following kernel modules unavailable in RHEL:\n"
                "{kmods}\n".format(kmods="\n".join(unsupported_kmods))
            )
        else:
            logger.critical(
                "The following loaded kernel modules are not available in RHEL:\n{0}\n"
                "Ensure you have updated the kernel to the latest available version and rebooted the system.\nIf this "
                "message persists, you can prevent the modules from loading by following {1} and rerun convert2rhel.\n"
                "Keeping them loaded could cause the system to malfunction after the conversion as they might not work "
                "properly with the RHEL kernel.\n"
                "To circumvent this check and accept the risk you can set environment variable "
                "'CONVERT2RHEL_ALLOW_UNAVAILABLE_KMODS=1'.".format(
                    "\n".join(unsupported_kmods), LINK_PREVENT_KMODS_FROM_LOADING
                )
            )


def validate_package_manager_transaction():
    """Validate the package manager transaction is passing the tests."""
    logger.task("Prepare: Validate the %s transaction", pkgmanager.TYPE)
    transaction_handler = pkgmanager.create_transaction_handler()
    transaction_handler.run_transaction(validate_transaction=True)


def get_loaded_kmods():
    """Get a set of kernel modules loaded on host.

    Each module we cut part of the path until the kernel release
    (i.e. /lib/modules/5.8.0-7642-generic/kernel/lib/a.ko.xz ->
    kernel/lib/a.ko.xz) in order to be able to compare with RHEL
    kernel modules in case of different kernel release
    """
    logger.debug("Getting a list of loaded kernel modules.")
    lsmod_output, _ = run_subprocess(["lsmod"], print_output=False)
    modules = re.findall(r"^(\w+)\s.+$", lsmod_output, flags=re.MULTILINE)[1:]
    return set(
        _get_kmod_comparison_key(run_subprocess(["modinfo", "-F", "filename", module], print_output=False)[0])
        for module in modules
    )


def _get_kmod_comparison_key(path):
    """Create a comparison key from the kernel module abs path.

    Converts /lib/modules/5.8.0-7642-generic/kernel/lib/a.ko.xz ->
    kernel/lib/a.ko.xz

    Why:
        The standard kernel modules are located under
        /lib/modules/{some kernel release}/.
        If we want to make sure that the kernel package is present
        on RHEL, we need to compare the full path, but because kernel release
        might be different, we compare the relative paths after kernel release.
    """
    return "/".join(path.strip().split("/")[4:])


def get_rhel_supported_kmods():
    """Return set of target RHEL supported kernel modules."""
    basecmd = [
        "repoquery",
        "--releasever=%s" % system_info.releasever,
    ]
    if system_info.version.major == 8:
        basecmd.append("--setopt=module_platform_id=platform:el8")

    for repoid in system_info.get_enabled_rhel_repos():
        basecmd.extend(("--repoid", repoid))

    cmd = basecmd[:]
    cmd.append("-f")
    cmd.append("/lib/modules/*.ko*")
    # Without the release package installed, dnf can't determine the modularity
    #   platform ID.
    # get output of a command to get all packages which are the source
    # of kmods
    kmod_pkgs_str, _ = run_subprocess(cmd, print_output=False)

    # from these packages we select only the latest one
    kmod_pkgs = get_most_recent_unique_kernel_pkgs(kmod_pkgs_str.rstrip("\n").split())
    if not kmod_pkgs:
        logger.debug("Output of the previous repoquery command:\n{0}".format(kmod_pkgs_str))
        logger.critical(
            "No packages containing kernel modules available in the enabled repositories ({0}).".format(
                ", ".join(system_info.get_enabled_rhel_repos())
            )
        )
    else:
        logger.info(
            "Comparing the loaded kernel modules with the modules available in the following RHEL"
            " kernel packages available in the enabled repositories:\n {0}".format("\n ".join(kmod_pkgs))
        )

    # querying obtained packages for files they produces
    cmd = basecmd[:]
    cmd.append("-l")
    cmd.extend(kmod_pkgs)
    rhel_kmods_str, _ = run_subprocess(cmd, print_output=False)

    return get_rhel_kmods_keys(rhel_kmods_str)


def get_most_recent_unique_kernel_pkgs(pkgs):
    """Return the most recent versions of all kernel packages.

    When we scan kernel modules provided by kernel packages
    it is expensive to check each kernel pkg. Since each new
    kernel pkg do not deprecate kernel modules we only select
    the most recent ones.

    .. note::
        All RHEL kmods packages starts with kernel* or kmod*

    For example, consider the following list of packages::

        list_of_pkgs = [
            'kernel-core-0:4.18.0-240.10.1.el8_3.x86_64',
            'kernel-core-0:4.19.0-240.10.1.el8_3.x86_64',
            'kmod-debug-core-0:4.18.0-240.10.1.el8_3.x86_64',
            'kmod-debug-core-0:4.18.0-245.10.1.el8_3.x86_64
        ]

    And when this function gets called with that same list of packages,
    we have the following output::

        result = get_most_recent_unique_kernel_pkgs(pkgs=list_of_pkgs)
        print(result)
        # (
        #   'kernel-core-0:4.19.0-240.10.1.el8_3.x86_64',
        #   'kmod-debug-core-0:4.18.0-245.10.1.el8_3.x86_64'
        # )

    :param pkgs: A list of package names to be analyzed.
    :type pkgs: list[str]
    :return: A tuple of packages name sorted and normalized
    :rtype: tuple[str]
    """

    pkgs_groups = itertools.groupby(sorted(pkgs), lambda pkg_name: pkg_name.split(":")[0])
    list_of_sorted_pkgs = []
    for distinct_kernel_pkgs in pkgs_groups:
        if distinct_kernel_pkgs[0].startswith(("kernel", "kmod")):
            list_of_sorted_pkgs.append(
                max(
                    distinct_kernel_pkgs[1],
                    key=cmp_to_key(compare_package_versions),
                )
            )

    return tuple(list_of_sorted_pkgs)


def get_rhel_kmods_keys(rhel_kmods_str):
    return set(
        _get_kmod_comparison_key(kmod_path)
        for kmod_path in filter(
            lambda path: path.endswith(("ko.xz", "ko")),
            rhel_kmods_str.rstrip("\n").split(),
        )
    )


def get_unsupported_kmods(host_kmods, rhel_supported_kmods):
    """Return a set of full paths to those installed kernel modules that are
    not available in RHEL repositories.

    Ignore certain kmods mentioned in the system configs. These kernel modules
    moved to kernel core, meaning that the functionality is retained and we
    would be incorrectly saying that the modules are not supported in RHEL.
    """
    unsupported_kmods_subpaths = host_kmods - rhel_supported_kmods - set(system_info.kmods_to_ignore)
    unsupported_kmods_full_paths = [
        "/lib/modules/{kver}/{kmod}".format(kver=system_info.booted_kernel, kmod=kmod)
        for kmod in unsupported_kmods_subpaths
    ]
    return unsupported_kmods_full_paths


def check_rhel_compatible_kernel_is_used():
    """Ensure the booted kernel is signed, is standard (not UEK, realtime, ...), and has the same version as in RHEL.

    By requesting that, we can be confident that the RHEL kernel will provide the same capabilities as on the
    original system.
    """
    logger.task("Prepare: Check kernel compatibility with RHEL")
    if any(
        (
            _bad_kernel_version(system_info.booted_kernel),
            _bad_kernel_package_signature(system_info.booted_kernel),
            _bad_kernel_substring(system_info.booted_kernel),
        )
    ):
        logger.critical(
            "The booted kernel version is incompatible with the standard RHEL kernel. "
            "To proceed with the conversion, boot into a kernel that is available in the {0} {1} base repository"
            " by executing the following steps:\n\n"
            "1. Ensure that the {0} {1} base repository is enabled\n"
            "2. Run: yum install kernel\n"
            "3. (optional) Run: grubby --set-default "
            '/boot/vmlinuz-`rpm -q --qf "%{{BUILDTIME}}\\t%{{EVR}}.%{{ARCH}}\\n" kernel | sort -nr | head -1 | cut -f2`\n'
            "4. Reboot the machine and if step 3 was not applied choose the kernel"
            " installed in step 2 manually".format(system_info.name, system_info.version.major)
        )
    else:
        logger.info("The booted kernel %s is compatible with RHEL." % system_info.booted_kernel)


def _bad_kernel_version(kernel_release):
    """Return True if the booted kernel version does not correspond to the kernel version available in RHEL."""
    kernel_version = kernel_release.split("-")[0]
    try:
        incompatible_version = COMPATIBLE_KERNELS_VERS[system_info.version.major] != kernel_version
        if incompatible_version:
            logger.warning(
                "Booted kernel version '%s' does not correspond to the version "
                "'%s' available in RHEL %d"
                % (
                    kernel_version,
                    COMPATIBLE_KERNELS_VERS[system_info.version.major],
                    system_info.version.major,
                )
            )
        else:
            logger.debug(
                "Booted kernel version '%s' corresponds to the version available in RHEL %d"
                % (kernel_version, system_info.version.major)
            )
        return incompatible_version
    except KeyError:
        logger.debug("Unexpected OS major version. Expected: %r" % COMPATIBLE_KERNELS_VERS.keys())
        return True


def _bad_kernel_package_signature(kernel_release):
    """Return True if the booted kernel is not signed by the original OS vendor, i.e. it's a custom kernel."""
    vmlinuz_path = "/boot/vmlinuz-%s" % kernel_release

    kernel_pkg, return_code = run_subprocess(
        ["rpm", "-qf", "--qf", "%{VERSION}&%{RELEASE}&%{ARCH}&%{NAME}", vmlinuz_path], print_output=False
    )

    os_vendor = system_info.name.split()[0]
    if return_code == 1:
        logger.warning(
            "The booted kernel %s is not owned by any installed package."
            " It needs to be owned by a package signed by %s." % (vmlinuz_path, os_vendor)
        )

        return True

    version, release, arch, name = tuple(kernel_pkg.split("&"))
    logger.debug("Booted kernel package name: {0}".format(name))

    kernel_pkg = get_installed_pkg_information("%s-%s-%s.%s" % (name, version, release, arch))[0]
    bad_signature = system_info.cfg_content["gpg_fingerprints"] != kernel_pkg.fingerprint

    # e.g. Oracle Linux Server -> Oracle or
    #      Oracle Linux Server -> CentOS Linux
    if bad_signature:
        logger.warning("Custom kernel detected. The booted kernel needs to be signed by %s." % os_vendor)
        return True

    logger.debug("The booted kernel is signed by %s." % os_vendor)
    return False


def _bad_kernel_substring(kernel_release):
    """Return True if the booted kernel release contains one of the strings that identify it as non-standard kernel."""
    bad_substring = any(bad_substring in kernel_release for bad_substring in BAD_KERNEL_RELEASE_SUBSTRINGS)
    if bad_substring:
        logger.debug(
            "The booted kernel '{0}' contains one of the disallowed "
            "substrings: {1}".format(kernel_release, BAD_KERNEL_RELEASE_SUBSTRINGS)
        )
        return True
    return False


def check_package_updates():
    """Ensure that the system packages installed are up-to-date."""
    logger.task("Prepare: Check if the installed packages are up-to-date")

    if system_info.id == "oracle" and system_info.corresponds_to_rhel_eus_release():
        logger.info(
            "Skipping the check because there are no publicly available %s %d.%d repositories available."
            % (system_info.name, system_info.version.major, system_info.version.minor)
        )
        return

    reposdir = get_hardcoded_repofiles_dir()

    if reposdir and not system_info.has_internet_access:
        logger.warning("Skipping the check as no internet connection has been detected.")
        return

    try:
        packages_to_update = get_total_packages_to_update(reposdir=reposdir)
    except (utils.UnableToSerialize, pkgmanager.RepoError) as e:
        # As both yum and dnf have the same error class (RepoError), to identify any problems when interacting with the
        # repositories, we use this to catch exceptions when verifying if there is any packages to update on the system.
        # Beware that the `RepoError` exception is based on the `pkgmanager` module and the message sent to the output
        # can differ depending if the code is running in RHEL7 (yum) or RHEL8 (dnf).
        logger.warning(
            "There was an error while checking whether the installed packages are up-to-date. Having updated system is "
            "an important prerequisite for a successful conversion. Consider stopping the conversion to "
            "verify that manually."
        )
        logger.warning(str(e))
        ask_to_continue()
        return

    if len(packages_to_update) > 0:
        repos_message = (
            "on the enabled system repositories"
            if not reposdir
            else "on repositories defined in the %s folder" % reposdir
        )
        logger.warning(
            "The system has %s package(s) not updated based %s.\n"
            "List of packages to update: %s.\n\n"
            "Not updating the packages may cause the conversion to fail.\n"
            "Consider stopping the conversion and update the packages before re-running convert2rhel."
            % (len(packages_to_update), repos_message, " ".join(packages_to_update))
        )
        ask_to_continue()
    else:
        logger.info("System is up-to-date.")


def is_loaded_kernel_latest():
    """Check if the loaded kernel is behind or of the same version as in yum repos."""
    logger.task("Prepare: Check if the loaded kernel version is the most recent")

    if system_info.id == "oracle" and system_info.corresponds_to_rhel_eus_release():
        logger.info(
            "Skipping the check because there are no publicly available %s %d.%d repositories available."
            % (system_info.name, system_info.version.major, system_info.version.minor)
        )
        return

    cmd = [
        "repoquery",
        "--setopt=exclude=",
        "--quiet",
        "--qf",
        "C2R\\t%{BUILDTIME}\\t%{VERSION}-%{RELEASE}\\t%{REPOID}",
    ]

    reposdir = get_hardcoded_repofiles_dir()
    if reposdir and not system_info.has_internet_access:
        logger.warning("Skipping the check as no internet connection has been detected.")
        return

    # If the reposdir variable is not empty, meaning that it detected the
    # hardcoded repofiles, we should use that
    # instead of the system repositories located under /etc/yum.repos.d
    if reposdir:
        cmd.append("--setopt=reposdir=%s" % reposdir)

    # For Oracle/CentOS Linux 8 the `kernel` is just a meta package, instead,
    # we check for `kernel-core`. But 7 releases, the correct way to check is
    # using `kernel`.
    package_to_check = "kernel-core" if system_info.version.major >= 8 else "kernel"

    # Append the package name as the last item on the list
    cmd.append(package_to_check)

    # Look up for available kernel (or kernel-core) packages versions available
    # in different repositories using the `repoquery` command.  If convert2rhel
    # detects that it is running on a EUS system, then repoquery will use the
    # hardcoded repofiles available under `/usr/share/convert2rhel/repos`,
    # meaning that the tool will fetch only the latest kernels available for
    # that EUS version, and not the most updated version from other newer
    # versions.
    repoquery_output, return_code = run_subprocess(cmd, print_output=False)
    if return_code != 0:
        logger.debug("Got the following output: %s", repoquery_output)
        logger.warning(
            "Couldn't fetch the list of the most recent kernels available in "
            "the repositories. Skipping the loaded kernel check."
        )
        return

    packages = []
    # We are expecting a repoquery output to be similar to this:
    #   C2R     1671212820      3.10.0-1160.81.1.el7    updates
    # We need the `C2R` identifier to be present on the line so we can know for
    # sure that the line we are working with is a line that contains
    # relevant repoquery information to our check, otherwise, we just log the
    # information as debug and do nothing with it.
    for line in repoquery_output.split("\n"):
        if line.strip() and "C2R" in line:
            _, build_time, latest_kernel, repoid = tuple(str(line).split("\t"))
            packages.append((build_time, latest_kernel, repoid))
        else:
            # Mainly for debugging purposes to see what is happening if we got
            # anything else that does not have the C2R identifier at the start
            # of the line.
            logger.debug("Got a line without the C2R identifier: %s" % line)

    # If we don't have any packages, then something went wrong, we need to
    # decide wether to bail out or output a warning (only if the user used the
    # special environment variable for it.
    if not packages:
        unsupported_skip = os.environ.get("CONVERT2RHEL_UNSUPPORTED_SKIP_KERNEL_CURRENCY_CHECK", None)
        if not unsupported_skip:
            logger.critical(
                "Could not find any %s from repositories to compare against the loaded kernel.\n"
                "Please, check if you have any vendor repositories enabled to proceed with the conversion.\n"
                "If you wish to ignore this message, set the environment variable "
                "'CONVERT2RHEL_UNSUPPORTED_SKIP_KERNEL_CURRENCY_CHECK' to 1." % package_to_check
            )

        logger.warning(
            "Detected 'CONVERT2RHEL_UNSUPPORTED_SKIP_KERNEL_CURRENCY_CHECK' environment variable, we will skip "
            "the %s comparison.\n"
            "Beware, this could leave your system in a broken state. " % package_to_check
        )
        return

    packages.sort(key=lambda x: x[0], reverse=True)
    _, latest_kernel, repoid = packages[0]

    uname_output, _ = run_subprocess(["uname", "-r"], print_output=False)
    loaded_kernel = uname_output.rsplit(".", 1)[0]
    # append the package name to loaded_kernel and latest_kernel so they can be properly processed by
    # compare_package_versions()
    latest_kernel_pkg = "%s-%s" % (package_to_check, latest_kernel)
    loaded_kernel_pkg = "%s-%s" % (package_to_check, loaded_kernel)
    match = compare_package_versions(latest_kernel_pkg, loaded_kernel_pkg)

    if match != 0:
        repos_message = (
            "in the enabled system repositories"
            if not reposdir
            else "in repositories defined in the %s folder" % reposdir
        )
        logger.critical(
            "The version of the loaded kernel is different from the latest version %s.\n"
            " Latest kernel version available in %s: %s\n"
            " Loaded kernel version: %s\n\n"
            "To proceed with the conversion, update the kernel version by executing the following step:\n\n"
            "1. yum install %s-%s -y\n"
            "2. reboot" % (repos_message, repoid, latest_kernel, loaded_kernel, package_to_check, latest_kernel)
        )

    logger.info("The currently loaded kernel is at the latest version.")


def check_dbus_is_running():
    """Error out if we need to register with rhsm and the dbus daemon is not running."""
    logger.task("Prepare: Check that DBus Daemon is running")

    if tool_opts.no_rhsm:
        logger.info("Skipping the check because we have been asked not to subscribe this system to RHSM.")
        return

    if system_info.dbus_running:
        logger.info("DBus Daemon is running")
        return

    logger.critical(
        "Could not find a running DBus Daemon which is needed to register with subscription manager.\n"
        "Please start dbus using `systemctl start dbus`"
    )


def _is_initramfs_file_valid(filepath):
    """Internal function to verify if an initramfs file is corrupted.

    This method will rely on using lsinitrd to do the validation. If the
    lsinitrd returns other value that is not 0, then it means that the file is
    probably corrupted or may cause problems during the next reboot.

    :param filepath: The path to the initramfs file.
    :type filepath: str
    :return: A boolean to determine if the file is corrupted.
    :rtype: bool
    """
    logger.info("Checking if the '%s' file is valid.", filepath)

    if not os.path.exists(filepath):
        logger.info("The initramfs file is not present.")
        return False

    logger.debug("Checking if the '%s' file is not corrupted.", filepath)
    out, return_code = run_subprocess(
        cmd=["/usr/bin/lsinitrd", filepath],
        print_output=False,
    )

    if return_code != 0:
        logger.info("Couldn't verify initramfs file. It may be corrupted.")
        logger.debug("Output of lsinitrd: %s", out)
        return False

    return True


def check_kernel_boot_files():
    """Check if the required kernel files exist and are valid under the boot partition."""
    # For Oracle/CentOS Linux 8 the `kernel` is just a meta package, instead,
    # we check for `kernel-core`. This is not true regarding the 7.* releases.
    kernel_name = "kernel-core" if system_info.version.major >= 8 else "kernel"

    # Either the package is returned or not. The return_code will be 0 in
    # either case, so we don't care about checking for that here.
    output, _ = run_subprocess(["rpm", "-q", "--last", kernel_name], print_output=False)

    # We are parsing the latest kernel installed on the system, which at this
    # point, should be a RHEL kernel. Since we can't get the kernel version
    # from `uname -r`, as it requires a reboot in order to take place, we are
    # detecting the latest kernel by using `rpm` and figuring out which was the
    # latest kernel installed.
    latest_installed_kernel = output.split("\n")[0].split(" ")[0]
    latest_installed_kernel = latest_installed_kernel[len(kernel_name + "-") :]
    grub2_config_file = grub.get_grub_config_file()
    initramfs_file = INITRAMFS_FILEPATH % latest_installed_kernel
    vmlinuz_file = VMLINUZ_FILEPATH % latest_installed_kernel

    logger.info("Checking if the '%s' file exists.", vmlinuz_file)
    vmlinuz_exists = os.path.exists(vmlinuz_file)
    if not vmlinuz_exists:
        logger.info("The vmlinuz file is not present.")

    is_initramfs_valid = _is_initramfs_file_valid(initramfs_file)

    if not is_initramfs_valid or not vmlinuz_exists:
        logger.warning(
            "Couldn't verify the kernel boot files in the boot partition. This may cause problems during the next boot "
            "of your system.\nIn order to fix this problem you may need to free/increase space in your boot partition"
            " and then run the following commands in your terminal:\n"
            "1. yum reinstall %s-%s -y\n"
            "2. grub2-mkconfig -o %s\n"
            "3. reboot",
            kernel_name,
            latest_installed_kernel,
            grub2_config_file,
        )
    else:
        logger.info("The initramfs and vmlinuz files are valid.")
