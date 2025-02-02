# -*- coding: utf-8 -*-
#
# Copyright(C) 2018 Red Hat, Inc.
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
import os
import re
import unittest

from collections import namedtuple

import pytest
import six

from convert2rhel import checks, grub, pkgmanager, unit_tests, utils
from convert2rhel.checks import (
    _bad_kernel_package_signature,
    _bad_kernel_substring,
    _bad_kernel_version,
    check_package_updates,
    check_rhel_compatible_kernel_is_used,
    get_loaded_kmods,
    is_loaded_kernel_latest,
)
from convert2rhel.systeminfo import system_info
from convert2rhel.toolopts import tool_opts
from convert2rhel.unit_tests import (
    GetFileContentMocked,
    GetLoggerMocked,
    create_pkg_information,
    run_subprocess_side_effect,
)
from convert2rhel.unit_tests.conftest import centos7, centos8, oracle8
from convert2rhel.utils import run_subprocess


six.add_move(six.MovedModule("mock", "mock", "unittest.mock"))
from six.moves import mock


MODINFO_STUB = (
    "/lib/modules/5.8.0-7642-generic/kernel/lib/a.ko.xz\n"
    "/lib/modules/5.8.0-7642-generic/kernel/lib/b.ko.xz\n"
    "/lib/modules/5.8.0-7642-generic/kernel/lib/c.ko.xz\n"
)

HOST_MODULES_STUB_GOOD = frozenset(
    (
        "kernel/lib/a.ko.xz",
        "kernel/lib/b.ko.xz",
        "kernel/lib/c.ko.xz",
    )
)
HOST_MODULES_STUB_BAD = frozenset(
    (
        "kernel/lib/d.ko.xz",
        "kernel/lib/e.ko.xz",
        "kernel/lib/f.ko.xz",
    )
)

REPOQUERY_F_STUB_GOOD = (
    "kernel-core-0:4.18.0-240.10.1.el8_3.x86_64\n"
    "kernel-core-0:4.18.0-240.15.1.el8_3.x86_64\n"
    "kernel-debug-core-0:4.18.0-240.10.1.el8_3.x86_64\n"
    "kernel-debug-core-0:4.18.0-240.15.1.el8_3.x86_64\n"
)
REPOQUERY_F_STUB_BAD = (
    "kernel-idontexpectyou-core-sdsdsd.ell8_3.x86_64\n"
    "kernel-core-0:4.18.0-240.15.1.el8_3.x86_64\n"
    "kernel-debug-core-0:4.18.0-240.10.1.el8_3.x86_64\n"
    "kernel-debug-core-0:4.18.0-240.15.1.el8_3.x86_64\n"
)
REPOQUERY_L_STUB_GOOD = (
    "/lib/modules/5.8.0-7642-generic/kernel/lib/a.ko.xz\n"
    "/lib/modules/5.8.0-7642-generic/kernel/lib/a.ko\n"
    "/lib/modules/5.8.0-7642-generic/kernel/lib/b.ko.xz\n"
    "/lib/modules/5.8.0-7642-generic/kernel/lib/c.ko.xz\n"
    "/lib/modules/5.8.0-7642-generic/kernel/lib/c.ko\n"
)
REPOQUERY_L_STUB_BAD = (
    "/lib/modules/5.8.0-7642-generic/kernel/lib/d.ko.xz\n"
    "/lib/modules/5.8.0-7642-generic/kernel/lib/d.ko\n"
    "/lib/modules/5.8.0-7642-generic/kernel/lib/e.ko.xz\n"
    "/lib/modules/5.8.0-7642-generic/kernel/lib/f.ko.xz\n"
    "/lib/modules/5.8.0-7642-generic/kernel/lib/f.ko\n"
)


@pytest.fixture
def convert2rhel_latest_version_test(monkeypatch, tmpdir, request, global_system_info):
    monkeypatch.setattr(checks, "system_info", global_system_info)
    global_system_info.has_internet_access = True

    marker = request.param
    monkeypatch.setattr(checks, "installed_convert2rhel_version", marker["local_version"])

    run_subprocess_mocked = mock.Mock(spec=run_subprocess, return_value=(marker["package_version"], 0))

    monkeypatch.setattr(checks, "run_subprocess", run_subprocess_mocked)
    monkeypatch.setattr(global_system_info, "version", namedtuple("Version", ["major", "minor"])(marker["pmajor"], 0))
    monkeypatch.setattr(utils, "TMP_DIR", str(tmpdir))

    return marker["local_version"], marker["package_version"]


def test_perform_pre_checks(monkeypatch):
    check_convert2rhel_latest_mock = mock.Mock()
    check_thirdparty_kmods_mock = mock.Mock()
    check_efi_mock = mock.Mock()
    check_readonly_mounts_mock = mock.Mock()
    check_custom_repos_are_valid_mock = mock.Mock()
    check_rhel_compatible_kernel_is_used_mock = mock.Mock()
    check_package_updates_mock = mock.Mock()
    is_loaded_kernel_latest_mock = mock.Mock()
    check_dbus_is_running_mock = mock.Mock()

    monkeypatch.setattr(
        checks,
        "check_convert2rhel_latest",
        value=check_convert2rhel_latest_mock,
    )
    monkeypatch.setattr(
        checks,
        "check_efi",
        value=check_efi_mock,
    )
    monkeypatch.setattr(
        checks,
        "check_tainted_kmods",
        value=check_thirdparty_kmods_mock,
    )
    monkeypatch.setattr(
        checks,
        "check_readonly_mounts",
        value=check_readonly_mounts_mock,
    )
    monkeypatch.setattr(
        checks,
        "check_rhel_compatible_kernel_is_used",
        value=check_rhel_compatible_kernel_is_used_mock,
    )
    monkeypatch.setattr(
        checks,
        "check_custom_repos_are_valid",
        value=check_custom_repos_are_valid_mock,
    )
    monkeypatch.setattr(
        checks,
        "check_custom_repos_are_valid",
        value=check_custom_repos_are_valid_mock,
    )
    monkeypatch.setattr(checks, "check_package_updates", value=check_package_updates_mock)
    monkeypatch.setattr(checks, "is_loaded_kernel_latest", value=is_loaded_kernel_latest_mock)
    monkeypatch.setattr(checks, "check_dbus_is_running", value=check_dbus_is_running_mock)

    checks.perform_system_checks()

    check_convert2rhel_latest_mock.assert_called_once()
    check_thirdparty_kmods_mock.assert_called_once()
    check_efi_mock.assert_called_once()
    check_readonly_mounts_mock.assert_called_once()
    check_rhel_compatible_kernel_is_used_mock.assert_called_once()
    check_package_updates_mock.assert_called_once()
    is_loaded_kernel_latest_mock.assert_called_once()
    check_dbus_is_running_mock.assert_called_once()


def test_perform_pre_ponr_checks(monkeypatch):
    ensure_compatibility_of_kmods_mock = mock.Mock()
    create_transaction_handler_mock = mock.Mock()
    monkeypatch.setattr(
        checks,
        "ensure_compatibility_of_kmods",
        value=ensure_compatibility_of_kmods_mock,
    )
    monkeypatch.setattr(
        checks.pkgmanager,
        "create_transaction_handler",
        value=create_transaction_handler_mock,
    )
    checks.perform_pre_ponr_checks()
    ensure_compatibility_of_kmods_mock.assert_called_once()
    create_transaction_handler_mock.assert_called_once()


class TestCheckConvert2rhelLatest(object):
    @pytest.mark.parametrize(
        ("convert2rhel_latest_version_test",),
        (
            [{"local_version": "0.20.0", "package_version": "C2R convert2rhel-0:0.22.0-1.el7.noarch", "pmajor": "7"}],
            [{"local_version": "0.20", "package_version": "C2R convert2rhel-0:0.22.0-1.el7.noarch", "pmajor": "7"}],
            [{"local_version": "0.20.0", "package_version": "C2R convert2rhel-0:0.22-1.el7.noarch", "pmajor": "7"}],
        ),
        indirect=True,
    )
    def test_convert2rhel_latest_offline(self, caplog, convert2rhel_latest_version_test, global_system_info):
        global_system_info.has_internet_access = False
        checks.check_convert2rhel_latest()

        log_msg = "Skipping the check because no internet connection has been detected."
        assert log_msg in caplog.text

    @pytest.mark.parametrize(
        ("convert2rhel_latest_version_test",),
        (
            [{"local_version": "0.21.1", "package_version": "C2R convert2rhel-0:0.22.0-1.el7.noarch", "pmajor": "7"}],
            [{"local_version": "0.21.2", "package_version": "C2R convert2rhel-0:1.10.0-1.el7.noarch", "pmajor": "7"}],
            [{"local_version": "1.21.0", "package_version": "C2R convert2rhel-0:1.21.1-1.el7.noarch", "pmajor": "7"}],
            [{"local_version": "1.21", "package_version": "C2R convert2rhel-0:1.21.1-1.el7.noarch", "pmajor": "7"}],
            [{"local_version": "1.21.1", "package_version": "C2R convert2rhel-0:1.22-1.el7.noarch", "pmajor": "7"}],
        ),
        indirect=True,
    )
    def test_convert2rhel_latest_log_check_exit(self, caplog, convert2rhel_latest_version_test):
        with pytest.raises(SystemExit):
            checks.check_convert2rhel_latest()
        local_version, package_version = convert2rhel_latest_version_test
        if len(package_version) > 36:
            package_version = package_version[19:25]
        else:
            package_version = package_version[19:23]
        log_msg = (
            "You are currently running %s and the latest version of Convert2RHEL is %s.\n"
            "Only the latest version is supported for conversion. If you want to ignore"
            " this check, then set the environment variable 'CONVERT2RHEL_ALLOW_OLDER_VERSION=1' to continue."
            % (local_version, package_version)
        )
        assert log_msg in caplog.text

    @pytest.mark.parametrize(
        ("convert2rhel_latest_version_test",),
        (
            [
                {
                    "local_version": "0.18.0",
                    "package_version": "C2R convert2rhel-0:0.22.1-1.el7.noarch",
                    "pmajor": "6",
                    "enset": "1",
                }
            ],
            [
                {
                    "local_version": "0.18.1",
                    "package_version": "C2R convert2rhel-0:0.22.0-1.el7.noarch",
                    "pmajor": "7",
                    "enset": "1",
                }
            ],
            [
                {
                    "local_version": "0.18.3",
                    "package_version": "C2R convert2rhel-0:0.22.1-1.el7.noarch",
                    "pmajor": "8",
                    "enset": "1",
                }
            ],
            [
                {
                    "local_version": "0.18.0",
                    "package_version": "C2R convert2rhel-0:1.10.2-1.el7.noarch",
                    "pmajor": "8",
                    "enset": "1",
                }
            ],
            [
                {
                    "local_version": "0.18",
                    "package_version": "C2R convert2rhel-0:1.10.2-1.el7.noarch",
                    "pmajor": "8",
                    "enset": "1",
                }
            ],
            [
                {
                    "local_version": "0.18.0",
                    "package_version": "C2R convert2rhel-0:1.10-1.el7.noarch",
                    "pmajor": "8",
                    "enset": "1",
                }
            ],
        ),
        indirect=True,
    )
    def test_convert2rhel_latest_log_check_env(self, caplog, monkeypatch, convert2rhel_latest_version_test):
        monkeypatch.setattr(os, "environ", {"CONVERT2RHEL_ALLOW_OLDER_VERSION": "1"})
        checks.check_convert2rhel_latest()

        local_version, package_version = convert2rhel_latest_version_test
        if len(package_version) > 36:
            package_version = package_version[19:25]
        else:
            package_version = package_version[19:23]
        log_msg = (
            "You are currently running %s and the latest version of Convert2RHEL is %s.\n"
            "'CONVERT2RHEL_ALLOW_OLDER_VERSION' environment variable detected, continuing conversion"
            % (local_version, package_version)
        )
        assert log_msg in caplog.text

        deprecated_var_name = "CONVERT2RHEL_UNSUPPORTED_VERSION"
        assert deprecated_var_name not in caplog.text

    @pytest.mark.parametrize(
        ("convert2rhel_latest_version_test",),
        (
            [{"local_version": "0.17.0", "package_version": "C2R convert2rhel-0:0.17.0-1.el7.noarch", "pmajor": "6"}],
            [{"local_version": "0.17.0", "package_version": "C2R convert2rhel-0:0.17.0-1.el7.noarch", "pmajor": "7"}],
            [{"local_version": "0.17.0", "package_version": "C2R convert2rhel-0:0.17.0-1.el7.noarch", "pmajor": "8"}],
            [{"local_version": "0.25.0", "package_version": "C2R convert2rhel-0:0.17.0-1.el7.noarch", "pmajor": "6"}],
            [{"local_version": "0.25.0", "package_version": "C2R convert2rhel-0:0.17.0-1.el7.noarch", "pmajor": "7"}],
            [{"local_version": "0.25.0", "package_version": "C2R convert2rhel-0:0.17.0-1.el7.noarch", "pmajor": "8"}],
            [{"local_version": "1.10.0", "package_version": "C2R convert2rhel-0:0.18.0-1.el7.noarch", "pmajor": "8"}],
            [{"local_version": "1.10.1", "package_version": "C2R convert2rhel-0:1.10.0-1.el7.noarch", "pmajor": "8"}],
        ),
        indirect=True,
    )
    def test_c2r_up_to_date(self, caplog, monkeypatch, convert2rhel_latest_version_test):
        checks.check_convert2rhel_latest()

        local_version, dummy_ = convert2rhel_latest_version_test
        log_msg = "Latest available Convert2RHEL version is installed."
        assert log_msg in caplog.text

    @pytest.mark.parametrize(
        ("convert2rhel_latest_version_test",),
        (
            [{"local_version": "1.10.0", "package_version": "C2R convert2rhel-0:0.18.0-1.el7.noarch", "pmajor": "8"}],
            [{"local_version": "1.10", "package_version": "C2R convert2rhel-0:0.18.0-1.el7.noarch", "pmajor": "8"}],
            [{"local_version": "1.10.0", "package_version": "C2R convert2rhel-0:0.18-1.el7.noarch", "pmajor": "8"}],
        ),
        indirect=True,
    )
    def test_c2r_up_to_date_repoquery_error(self, caplog, convert2rhel_latest_version_test, monkeypatch):
        monkeypatch.setattr(checks, "run_subprocess", mock.Mock(return_value=("Repoquery did not run", 1)))

        checks.check_convert2rhel_latest()

        log_msg = (
            "Couldn't check if the current installed Convert2RHEL is the latest version.\n"
            "repoquery failed with the following output:\nRepoquery did not run"
        )
        assert log_msg in caplog.text

    @pytest.mark.parametrize(
        ("convert2rhel_latest_version_test",),
        (
            [
                {
                    "local_version": "0.19.0",
                    "package_version": "C2R convert2rhel-0:0.18.0-1.el7.noarch\nC2R convert2rhel-0:0.17.0-1.el7.noarch\nC2R convert2rhel-0:0.20.0-1.el7.noarch",
                    "pmajor": "8",
                }
            ],
            [
                {
                    "local_version": "0.19",
                    "package_version": "C2R convert2rhel-0:0.18.0-1.el7.noarch\nC2R convert2rhel-0:0.17.0-1.el7.noarch\nC2R convert2rhel-0:0.20.0-1.el7.noarch",
                    "pmajor": "8",
                }
            ],
            [
                {
                    "local_version": "0.19.0",
                    "package_version": "C2R convert2rhel-0:0.18-1.el7.noarch\nC2R convert2rhel-0:0.17-1.el7.noarch\nC2R convert2rhel-0:0.20-1.el7.noarch",
                    "pmajor": "8",
                }
            ],
        ),
        indirect=True,
    )
    def test_c2r_up_to_date_multiple_packages(self, caplog, convert2rhel_latest_version_test):

        with pytest.raises(SystemExit):
            checks.check_convert2rhel_latest()

        local_version, package_version = convert2rhel_latest_version_test

        if len(package_version) > 110:
            package_version = package_version[97:103]
        else:
            package_version = package_version[93:97]

        log_msg = (
            "You are currently running %s and the latest version of Convert2RHEL is %s.\n"
            "Only the latest version is supported for conversion. If you want to ignore"
            " this check, then set the environment variable 'CONVERT2RHEL_ALLOW_OLDER_VERSION=1' to continue."
            % (local_version, package_version)
        )
        assert log_msg in caplog.text

    @pytest.mark.parametrize(
        ("convert2rhel_latest_version_test",),
        (
            [{"local_version": "0.17.0", "package_version": "C2R convert2rhel-0:0.18.0-1.el7.noarch", "pmajor": "8"}],
            [{"local_version": "0.17", "package_version": "C2R convert2rhel-0:0.18.0-1.el7.noarch", "pmajor": "8"}],
            [{"local_version": "0.17.0", "package_version": "C2R convert2rhel-0:0.18-1.el7.noarch", "pmajor": "8"}],
        ),
        indirect=True,
    )
    def test_c2r_up_to_date_deprecated_env_var(self, caplog, convert2rhel_latest_version_test, monkeypatch):
        env = {"CONVERT2RHEL_UNSUPPORTED_VERSION": 1}
        monkeypatch.setattr(os, "environ", env)

        checks.check_convert2rhel_latest()

        log_msg = (
            "You are using the deprecated 'CONVERT2RHEL_UNSUPPORTED_VERSION'"
            " environment variable.  Please switch to 'CONVERT2RHEL_ALLOW_OLDER_VERSION'"
            " instead."
        )

        assert log_msg in caplog.text


@pytest.mark.parametrize(
    (
        "host_kmods",
        "exception",
        "should_be_in_logs",
        "shouldnt_be_in_logs",
    ),
    (
        (
            HOST_MODULES_STUB_GOOD,
            None,
            "loaded kernel modules are available in RHEL",
            None,
        ),
        (
            HOST_MODULES_STUB_BAD,
            SystemExit,
            None,
            "loaded kernel modules are available in RHEL",
        ),
    ),
)
@centos8
def test_ensure_compatibility_of_kmods(
    monkeypatch,
    pretend_os,
    caplog,
    host_kmods,
    exception,
    should_be_in_logs,
    shouldnt_be_in_logs,
):
    monkeypatch.setattr(checks, "get_loaded_kmods", mock.Mock(return_value=host_kmods))
    run_subprocess_mock = mock.Mock(
        side_effect=run_subprocess_side_effect(
            (("uname",), ("5.8.0-7642-generic\n", 0)),
            (("repoquery", "-f"), (REPOQUERY_F_STUB_GOOD, 0)),
            (("repoquery", "-l"), (REPOQUERY_L_STUB_GOOD, 0)),
        )
    )
    monkeypatch.setattr(
        checks,
        "run_subprocess",
        value=run_subprocess_mock,
    )

    if exception:
        with pytest.raises(exception):
            checks.ensure_compatibility_of_kmods()
    else:
        checks.ensure_compatibility_of_kmods()

    if should_be_in_logs:
        assert should_be_in_logs in caplog.records[-1].message
    if shouldnt_be_in_logs:
        assert shouldnt_be_in_logs not in caplog.records[-1].message


@centos8
def test_ensure_compatibility_of_kmods_check_env(
    monkeypatch,
    pretend_os,
    caplog,
):

    monkeypatch.setattr(os, "environ", {"CONVERT2RHEL_ALLOW_UNAVAILABLE_KMODS": "1"})
    monkeypatch.setattr(checks, "get_loaded_kmods", mock.Mock(return_value=HOST_MODULES_STUB_BAD))
    run_subprocess_mock = mock.Mock(
        side_effect=run_subprocess_side_effect(
            (("uname",), ("5.8.0-7642-generic\n", 0)),
            (("repoquery", "-f"), (REPOQUERY_F_STUB_GOOD, 0)),
            (("repoquery", "-l"), (REPOQUERY_L_STUB_GOOD, 0)),
        )
    )
    monkeypatch.setattr(
        checks,
        "run_subprocess",
        value=run_subprocess_mock,
    )

    checks.ensure_compatibility_of_kmods()
    should_be_in_logs = (
        ".*Detected 'CONVERT2RHEL_ALLOW_UNAVAILABLE_KMODS' environment variable."
        " We will continue the conversion with the following kernel modules unavailable in RHEL:.*"
    )
    assert re.match(pattern=should_be_in_logs, string=caplog.records[-1].message, flags=re.MULTILINE | re.DOTALL)


@pytest.mark.parametrize(
    (
        "unsupported_pkg",
        "msg_in_logs",
        "msg_not_in_logs",
        "exception",
    ),
    (
        # ff-memless specified to be ignored in the config, so no exception raised
        (
            "kernel/drivers/input/ff-memless.ko.xz",
            "loaded kernel modules are available in RHEL",
            "The following loaded kernel modules are not available in RHEL",
            None,
        ),
        (
            "kernel/drivers/input/other.ko.xz",
            "The following loaded kernel modules are not available in RHEL",
            None,
            SystemExit,
        ),
    ),
)
@centos7
def test_ensure_compatibility_of_kmods_excluded(
    monkeypatch,
    pretend_os,
    caplog,
    unsupported_pkg,
    msg_in_logs,
    msg_not_in_logs,
    exception,
):
    monkeypatch.setattr(
        checks,
        "get_loaded_kmods",
        mock.Mock(
            return_value=HOST_MODULES_STUB_GOOD | frozenset((unsupported_pkg,)),
        ),
    )
    get_unsupported_kmods_mocked = mock.Mock(wraps=checks.get_unsupported_kmods)
    run_subprocess_mock = mock.Mock(
        side_effect=run_subprocess_side_effect(
            (("uname",), ("5.8.0-7642-generic\n", 0)),
            (("repoquery", "-f"), (REPOQUERY_F_STUB_GOOD, 0)),
            (("repoquery", "-l"), (REPOQUERY_L_STUB_GOOD, 0)),
        )
    )
    monkeypatch.setattr(
        checks,
        "run_subprocess",
        value=run_subprocess_mock,
    )
    monkeypatch.setattr(
        checks,
        "get_unsupported_kmods",
        value=get_unsupported_kmods_mocked,
    )
    if exception:
        with pytest.raises(exception):
            checks.ensure_compatibility_of_kmods()
    else:
        checks.ensure_compatibility_of_kmods()

    get_unsupported_kmods_mocked.assert_called_with(
        # host kmods
        set(
            (
                unsupported_pkg,
                "kernel/lib/c.ko.xz",
                "kernel/lib/a.ko.xz",
                "kernel/lib/b.ko.xz",
            )
        ),
        # rhel supported kmods
        set(
            (
                "kernel/lib/c.ko",
                "kernel/lib/b.ko.xz",
                "kernel/lib/c.ko.xz",
                "kernel/lib/a.ko.xz",
                "kernel/lib/a.ko",
            )
        ),
    )
    if msg_in_logs:
        assert any(msg_in_logs in record.message for record in caplog.records)
    if msg_not_in_logs:
        assert all(msg_not_in_logs not in record.message for record in caplog.records)


def test_get_loaded_kmods(monkeypatch):
    run_subprocess_mocked = mock.Mock(
        spec=run_subprocess,
        side_effect=run_subprocess_side_effect(
            (
                ("lsmod",),
                (
                    "Module                  Size  Used by\n"
                    "a                 81920  4\n"
                    "b    49152  0\n"
                    "c              40960  1\n",
                    0,
                ),
            ),
            (
                ("modinfo", "-F", "filename", "a"),
                (MODINFO_STUB.split()[0] + "\n", 0),
            ),
            (
                ("modinfo", "-F", "filename", "b"),
                (MODINFO_STUB.split()[1] + "\n", 0),
            ),
            (
                ("modinfo", "-F", "filename", "c"),
                (MODINFO_STUB.split()[2] + "\n", 0),
            ),
        ),
    )
    monkeypatch.setattr(
        checks,
        "run_subprocess",
        value=run_subprocess_mocked,
    )
    assert get_loaded_kmods() == frozenset(("kernel/lib/c.ko.xz", "kernel/lib/a.ko.xz", "kernel/lib/b.ko.xz"))


@pytest.mark.parametrize(
    ("repoquery_f_stub", "repoquery_l_stub"),
    (
        (REPOQUERY_F_STUB_GOOD, REPOQUERY_L_STUB_GOOD),
        (REPOQUERY_F_STUB_BAD, REPOQUERY_L_STUB_GOOD),
    ),
)
@centos8
def test_get_rhel_supported_kmods(
    monkeypatch,
    pretend_os,
    repoquery_f_stub,
    repoquery_l_stub,
):
    run_subprocess_mock = mock.Mock(
        side_effect=run_subprocess_side_effect(
            (
                ("repoquery", "-f"),
                (repoquery_f_stub, 0),
            ),
            (
                ("repoquery", "-l"),
                (repoquery_l_stub, 0),
            ),
        )
    )
    monkeypatch.setattr(
        checks,
        "run_subprocess",
        value=run_subprocess_mock,
    )

    res = checks.get_rhel_supported_kmods()
    assert res == set(
        (
            "kernel/lib/a.ko",
            "kernel/lib/a.ko.xz",
            "kernel/lib/b.ko.xz",
            "kernel/lib/c.ko.xz",
            "kernel/lib/c.ko",
        )
    )


@pytest.mark.parametrize(
    ("pkgs", "exp_res"),
    (
        (
            (
                "kernel-core-0:4.18.0-240.10.1.el8_3.x86_64",
                "kernel-debug-core-0:4.18.0-240.10.1.el8_3.x86_64",
                "kernel-core-0:4.18.0-240.15.1.el8_3.x86_64",
                "kernel-debug-core-0:4.18.0-240.15.1.el8_3.x86_64",
            ),
            (
                "kernel-core-0:4.18.0-240.15.1.el8_3.x86_64",
                "kernel-debug-core-0:4.18.0-240.15.1.el8_3.x86_64",
            ),
        ),
        (
            (
                "kmod-core-0:4.18.0-240.10.1.el8_3.x86_64",
                "kmod-core-0:4.18.0-240.15.1.el8_3.x86_64",
            ),
            ("kmod-core-0:4.18.0-240.15.1.el8_3.x86_64",),
        ),
        (
            (
                "kmod-core-0:10.18.0-240.10.1.el8_3.x86_64",
                "kmod-core-0:9.18.0-240.15.1.el8_3.x86_64",
            ),
            ("kmod-core-0:10.18.0-240.10.1.el8_3.x86_64",),
        ),
        (
            (
                "not-expected-core-0:4.18.0-240.10.1.el8_3.x86_64",
                "kmod-core-0:4.18.0-240.15.1.el8_3.x86_64",
            ),
            ("kmod-core-0:4.18.0-240.15.1.el8_3.x86_64",),
        ),
        (
            (
                "kernel-core-0:4.18.0-240.beta5.1.el8_3.x86_64",
                "kernel-core-0:4.18.0-240.15.1.el8_3.x86_64",
            ),
            ("kernel-core-0:4.18.0-240.15.1.el8_3.x86_64",),
        ),
        (
            (
                "kernel-core-0:4.18.0-240.15.beta5.1.el8_3.x86_64",
                "kernel-core-0:4.18.0-240.15.1.el8_3.x86_64",
            ),
            ("kernel-core-0:4.18.0-240.15.1.el8_3.x86_64",),
        ),
        (
            (
                "kernel-core-0:4.18.0-240.16.beta5.1.el8_3.x86_64",
                "kernel-core-0:4.18.0-240.15.1.el8_3.x86_64",
            ),
            ("kernel-core-0:4.18.0-240.16.beta5.1.el8_3.x86_64",),
        ),
        (("kernel_bad_package:111111",), ("kernel_bad_package:111111",)),
        (
            (
                "kernel-core-0:4.18.0-240.15.1.el8_3.x86_64",
                "kernel_bad_package:111111",
                "kernel-core-0:4.18.0-240.15.1.el8_3.x86_64",
            ),
            (
                "kernel-core-0:4.18.0-240.15.1.el8_3.x86_64",
                "kernel_bad_package:111111",
            ),
        ),
    ),
)
def test_get_most_recent_unique_kernel_pkgs(pkgs, exp_res):
    assert tuple(checks.get_most_recent_unique_kernel_pkgs(pkgs)) == exp_res


@pytest.mark.parametrize(
    ("command_return", "expected_exception"),
    (
        (
            ("", 0),
            None,
        ),
        (
            (
                (
                    "system76_io 16384 0 - Live 0x0000000000000000 (OE)\n"
                    "system76_acpi 16384 0 - Live 0x0000000000000000 (OE)"
                ),
                0,
            ),
            SystemExit,
        ),
    ),
)
def test_check_tainted_kmods(monkeypatch, command_return, expected_exception):
    run_subprocess_mock = mock.Mock(return_value=command_return)
    monkeypatch.setattr(
        checks,
        "run_subprocess",
        value=run_subprocess_mock,
    )
    if expected_exception:
        with pytest.raises(expected_exception):
            checks.check_tainted_kmods()
    else:
        checks.check_tainted_kmods()


class EFIBootInfoMocked:
    def __init__(
        self,
        current_bootnum="0001",
        next_boot=None,
        boot_order=("0001", "0002"),
        entries=None,
        exception=None,
    ):
        self.current_bootnum = current_bootnum
        self.next_boot = next_boot
        self.boot_order = boot_order
        self.entries = entries
        self.set_default_efi_entries()
        self._exception = exception

    def __call__(self):
        """Tested functions call existing object instead of creating one.
        The object is expected to be instantiated already when mocking
        so tested functions are not creating new object but are calling already
        the created one. From the point of the tested code, the behaviour is
        same now.
        """
        if not self._exception:
            return self
        raise self._exception  # pylint: disable=raising-bad-type

    def set_default_efi_entries(self):
        if not self.entries:
            self.entries = {
                "0001": grub.EFIBootLoader(
                    boot_number="0001",
                    label="Centos Linux",
                    active=True,
                    efi_bin_source=r"HD(1,GPT,28c77f6b-3cd0-4b22-985f-c99903835d79,0x800,0x12c000)/File(\EFI\centos\shimx64.efi)",
                ),
                "0002": grub.EFIBootLoader(
                    boot_number="0002",
                    label="Foo label",
                    active=True,
                    efi_bin_source="FvVol(7cb8bdc9-f8eb-4f34-aaea-3ee4af6516a1)/FvFile(462caa21-7614-4503-836e-8ab6f4662331)",
                ),
            }


def _gen_version(major, minor):
    return namedtuple("Version", ["major", "minor"])(major, minor)


class TestEFIChecks(unittest.TestCase):
    def _check_efi_detection_log(self, efi_detected=True):
        if efi_detected:
            self.assertNotIn("BIOS detected.", checks.logger.info_msgs)
            self.assertIn("UEFI detected.", checks.logger.info_msgs)
        else:
            self.assertIn("BIOS detected.", checks.logger.info_msgs)
            self.assertNotIn("UEFI detected.", checks.logger.info_msgs)

    def _check_efi_critical(self, critical_msg):
        self.assertRaises(SystemExit, checks.check_efi)
        self.assertEqual(len(checks.logger.critical_msgs), 1)
        self.assertIn(critical_msg, checks.logger.critical_msgs)
        self._check_efi_detection_log(True)

    @unit_tests.mock(grub, "is_efi", lambda: True)
    @unit_tests.mock(grub, "is_secure_boot", lambda: False)
    @unit_tests.mock(checks.system_info, "arch", "x86_64")
    @unit_tests.mock(checks.system_info, "version", _gen_version(7, 9))
    @unit_tests.mock(checks, "logger", GetLoggerMocked())
    @unit_tests.mock(os.path, "exists", lambda x: not x == "/usr/sbin/efibootmgr")
    @unit_tests.mock(
        grub,
        "EFIBootInfo",
        EFIBootInfoMocked(exception=grub.BootloaderError("errmsg")),
    )
    def test_check_efi_efi_detected_without_efibootmgr(self):
        self._check_efi_critical("Install efibootmgr to continue converting the UEFI-based system.")

    @unit_tests.mock(grub, "is_efi", lambda: True)
    @unit_tests.mock(grub, "is_secure_boot", lambda: False)
    @unit_tests.mock(checks.system_info, "arch", "aarch64")
    @unit_tests.mock(checks.system_info, "version", _gen_version(7, 9))
    @unit_tests.mock(checks, "logger", GetLoggerMocked())
    @unit_tests.mock(os.path, "exists", lambda x: x == "/usr/sbin/efibootmgr")
    @unit_tests.mock(
        grub,
        "EFIBootInfo",
        EFIBootInfoMocked(exception=grub.BootloaderError("errmsg")),
    )
    def test_check_efi_efi_detected_non_intel(self):
        self._check_efi_critical("Only x86_64 systems are supported for UEFI conversions.")

    @unit_tests.mock(grub, "is_efi", lambda: True)
    @unit_tests.mock(grub, "is_secure_boot", lambda: True)
    @unit_tests.mock(checks.system_info, "arch", "x86_64")
    @unit_tests.mock(checks.system_info, "version", _gen_version(7, 9))
    @unit_tests.mock(checks, "logger", GetLoggerMocked())
    @unit_tests.mock(os.path, "exists", lambda x: x == "/usr/sbin/efibootmgr")
    @unit_tests.mock(
        grub,
        "EFIBootInfo",
        EFIBootInfoMocked(exception=grub.BootloaderError("errmsg")),
    )
    def test_check_efi_efi_detected_secure_boot(self):
        self._check_efi_critical(
            "The conversion with secure boot is currently not possible.\n"
            "To disable it, follow the instructions available in this article: https://access.redhat.com/solutions/6753681"
        )
        self.assertIn("Secure boot detected.", checks.logger.info_msgs)

    @unit_tests.mock(grub, "is_efi", lambda: True)
    @unit_tests.mock(grub, "is_secure_boot", lambda: False)
    @unit_tests.mock(checks.system_info, "arch", "x86_64")
    @unit_tests.mock(checks.system_info, "version", _gen_version(7, 9))
    @unit_tests.mock(checks, "logger", GetLoggerMocked())
    @unit_tests.mock(os.path, "exists", lambda x: x == "/usr/sbin/efibootmgr")
    @unit_tests.mock(
        grub,
        "EFIBootInfo",
        EFIBootInfoMocked(exception=grub.BootloaderError("errmsg")),
    )
    def test_check_efi_efi_detected_bootloader_error(self):
        self._check_efi_critical("errmsg")

    @unit_tests.mock(grub, "is_efi", lambda: True)
    @unit_tests.mock(grub, "is_secure_boot", lambda: False)
    @unit_tests.mock(checks.system_info, "arch", "x86_64")
    @unit_tests.mock(checks.system_info, "version", _gen_version(7, 9))
    @unit_tests.mock(checks, "logger", GetLoggerMocked())
    @unit_tests.mock(os.path, "exists", lambda x: x == "/usr/sbin/efibootmgr")
    @unit_tests.mock(grub, "EFIBootInfo", EFIBootInfoMocked(current_bootnum="0002"))
    def test_check_efi_efi_detected_nofile_entry(self):
        checks.check_efi()
        self._check_efi_detection_log()
        warn_msg = (
            "The current UEFI bootloader '0002' is not referring to any binary UEFI file located on local"
            " EFI System Partition (ESP)."
        )
        self.assertIn(warn_msg, checks.logger.warning_msgs)

    @unit_tests.mock(grub, "is_efi", lambda: True)
    @unit_tests.mock(grub, "is_secure_boot", lambda: False)
    @unit_tests.mock(checks.system_info, "arch", "x86_64")
    @unit_tests.mock(checks.system_info, "version", _gen_version(7, 9))
    @unit_tests.mock(checks, "logger", GetLoggerMocked())
    @unit_tests.mock(os.path, "exists", lambda x: x == "/usr/sbin/efibootmgr")
    @unit_tests.mock(grub, "EFIBootInfo", EFIBootInfoMocked())
    def test_check_efi_efi_detected_ok(self):
        checks.check_efi()
        self._check_efi_detection_log()
        self.assertEqual(len(checks.logger.warning_msgs), 0)


@pytest.mark.parametrize(
    # i.e. _bad_kernel_version...
    ("any_of_the_subchecks_is_true",),
    (
        (True,),
        (False,),
    ),
)
def test_check_rhel_compatible_kernel_is_used(
    any_of_the_subchecks_is_true,
    monkeypatch,
    caplog,
):
    monkeypatch.setattr(
        checks,
        "_bad_kernel_version",
        value=mock.Mock(return_value=any_of_the_subchecks_is_true),
    )
    monkeypatch.setattr(
        checks,
        "_bad_kernel_substring",
        value=mock.Mock(return_value=False),
    )
    monkeypatch.setattr(
        checks,
        "_bad_kernel_package_signature",
        value=mock.Mock(return_value=False),
    )
    if any_of_the_subchecks_is_true:
        with pytest.raises(SystemExit):
            check_rhel_compatible_kernel_is_used()
    else:
        check_rhel_compatible_kernel_is_used()
        assert "is compatible with RHEL" in caplog.records[-1].message


@pytest.mark.parametrize(
    ("kernel_release", "major_ver", "exp_return"),
    (
        ("5.11.0-7614-generic", None, True),
        ("3.10.0-1160.24.1.el7.x86_64", 7, False),
        ("5.4.17-2102.200.13.el8uek.x86_64", 8, True),
        ("4.18.0-240.22.1.el8_3.x86_64", 8, False),
    ),
)
def test_bad_kernel_version(kernel_release, major_ver, exp_return, monkeypatch):
    Version = namedtuple("Version", ("major", "minor"))
    monkeypatch.setattr(
        checks.system_info,
        "version",
        value=Version(major=major_ver, minor=0),
    )
    assert _bad_kernel_version(kernel_release) == exp_return


@pytest.mark.parametrize(
    ("kernel_release", "exp_return"),
    (
        ("3.10.0-1160.24.1.el7.x86_64", False),
        ("5.4.17-2102.200.13.el8uek.x86_64", True),
        ("3.10.0-514.2.2.rt56.424.el7.x86_64", True),
    ),
)
def test_bad_kernel_substring(kernel_release, exp_return, monkeypatch):
    assert _bad_kernel_substring(kernel_release) == exp_return


@pytest.mark.parametrize(
    ("kernel_release", "kernel_pkg", "kernel_pkg_information", "exp_return"),
    (
        (
            "4.18.0-240.22.1.el8_3.x86_64",
            "4.18.0&240.22.1.el8_3&x86_64&kernel-core",
            create_pkg_information(
                name="kernel-core",
                epoch="0",
                version="4.18.0",
                release="240.22.1.el8_3",
                arch="x86_64",
                fingerprint="05b555b38483c65d",
            ),
            False,
        ),
        (
            "4.18.0-240.22.1.el8_3.x86_64",
            "4.18.0&240.22.1.el8_3&x86_64&kernel-core",
            create_pkg_information(
                name="kernel-core",
                epoch="0",
                version="4.18.0",
                release="240.22.1.el8_3",
                arch="x86_64",
                fingerprint="somebadsig",
            ),
            True,
        ),
    ),
)
@centos8
def test_bad_kernel_package_signature(
    kernel_release,
    kernel_pkg,
    kernel_pkg_information,
    exp_return,
    monkeypatch,
    pretend_os,
):
    run_subprocess_mocked = mock.Mock(spec=run_subprocess, return_value=(kernel_pkg, 0))
    monkeypatch.setattr(checks, "run_subprocess", run_subprocess_mocked)
    get_installed_pkg_information_mocked = mock.Mock(return_value=[kernel_pkg_information])
    monkeypatch.setattr(checks, "get_installed_pkg_information", get_installed_pkg_information_mocked)
    assert _bad_kernel_package_signature(kernel_release) == exp_return
    run_subprocess_mocked.assert_called_with(
        ["rpm", "-qf", "--qf", "%{VERSION}&%{RELEASE}&%{ARCH}&%{NAME}", "/boot/vmlinuz-%s" % kernel_release],
        print_output=False,
    )


@centos8
def test_kernel_not_installed(pretend_os, caplog, monkeypatch):
    run_subprocess_mocked = mock.Mock(spec=run_subprocess, return_value=(" ", 1))
    monkeypatch.setattr(checks, "run_subprocess", run_subprocess_mocked)
    assert _bad_kernel_package_signature("4.18.0-240.22.1.el8_3.x86_64")
    log_message = (
        "The booted kernel /boot/vmlinuz-4.18.0-240.22.1.el8_3.x86_64 is not owned by any installed package."
        " It needs to be owned by a package signed by CentOS."
    )
    assert log_message in caplog.text


class TestReadOnlyMountsChecks(unittest.TestCase):
    @unit_tests.mock(checks, "logger", GetLoggerMocked())
    @unit_tests.mock(
        checks,
        "get_file_content",
        GetFileContentMocked(
            data=[
                "sysfs /sys sysfs rw,seclabel,nosuid,nodev,noexec,relatime 0 0",
                "mnt /mnt sysfs rw,seclabel,nosuid,nodev,noexec,relatime 0 0",
                "cgroup /sys/fs/cgroup/cpuset cgroup rw,seclabel,nosuid,nodev,noexec,relatime,cpuset 0 0",
            ]
        ),
    )
    def test_mounted_are_readwrite(self):
        checks.check_readonly_mounts()
        self.assertEqual(len(checks.logger.critical_msgs), 0)
        self.assertEqual(len(checks.logger.debug_msgs), 2)
        self.assertIn("/mnt mount point is not read-only.", checks.logger.debug_msgs)
        self.assertIn("/sys mount point is not read-only.", checks.logger.debug_msgs)

    @unit_tests.mock(checks, "logger", GetLoggerMocked())
    @unit_tests.mock(
        checks,
        "get_file_content",
        GetFileContentMocked(
            data=[
                "sysfs /sys sysfs rw,seclabel,nosuid,nodev,noexec,relatime 0 0",
                "mnt /mnt sysfs ro,seclabel,nosuid,nodev,noexec,relatime 0 0",
                "cgroup /sys/fs/cgroup/cpuset cgroup rw,seclabel,nosuid,nodev,noexec,relatime,cpuset 0 0",
            ]
        ),
    )
    def test_mounted_are_readonly(self):
        self.assertRaises(SystemExit, checks.check_readonly_mounts)
        self.assertEqual(len(checks.logger.critical_msgs), 1)
        self.assertIn("Stopping conversion due to read-only mount to /mnt directory", checks.logger.critical_msgs[0])
        self.assertNotIn("Stopping conversion due to read-only mount to /sys directory", checks.logger.critical_msgs[0])
        self.assertIn("/sys mount point is not read-only.", checks.logger.debug_msgs[0])

    class CallYumCmdMocked(unit_tests.MockFunction):
        def __init__(self, ret_code, ret_string):
            self.called = 0
            self.return_code = ret_code
            self.return_string = ret_string
            self.fail_once = False
            self.command = None

        def __call__(self, command, *args, **kwargs):
            if self.fail_once and self.called == 0:
                self.return_code = 1
            if self.fail_once and self.called > 0:
                self.return_code = 0
            self.called += 1
            self.command = command
            return self.return_string, self.return_code

    @unit_tests.mock(system_info, "version", namedtuple("Version", ["major", "minor"])(7, 0))
    @unit_tests.mock(
        checks,
        "call_yum_cmd",
        CallYumCmdMocked(ret_code=0, ret_string="Abcdef"),
    )
    @unit_tests.mock(checks, "logger", GetLoggerMocked())
    @unit_tests.mock(tool_opts, "no_rhsm", True)
    def test_custom_repos_are_valid(self):
        checks.check_custom_repos_are_valid()
        self.assertEqual(len(checks.logger.info_msgs), 1)
        self.assertEqual(len(checks.logger.debug_msgs), 1)
        self.assertIn(
            "The repositories passed through the --enablerepo option are all accessible.", checks.logger.info_msgs
        )

    @unit_tests.mock(system_info, "version", namedtuple("Version", ["major", "minor"])(7, 0))
    @unit_tests.mock(
        checks,
        "call_yum_cmd",
        CallYumCmdMocked(ret_code=1, ret_string="Abcdef"),
    )
    @unit_tests.mock(checks, "logger", GetLoggerMocked())
    @unit_tests.mock(tool_opts, "no_rhsm", True)
    def test_custom_repos_are_invalid(self):
        self.assertRaises(SystemExit, checks.check_custom_repos_are_valid)
        self.assertEqual(len(checks.logger.critical_msgs), 1)
        self.assertEqual(len(checks.logger.info_msgs), 0)
        self.assertIn("Unable to access the repositories passed through ", checks.logger.critical_msgs[0])


@oracle8
def test_check_package_updates_skip_on_not_latest_ol(pretend_os, caplog):
    message = (
        "Skipping the check because there are no publicly available Oracle Linux Server 8.4 repositories available."
    )

    check_package_updates()

    assert message in caplog.records[-1].message


@pytest.mark.parametrize(
    ("packages", "exception", "expected"),
    (
        (["package-1", "package-2"], True, "The system has {0} package(s) not updated"),
        ([], False, "System is up-to-date."),
    ),
)
@centos8
def test_check_package_updates(pretend_os, packages, exception, expected, monkeypatch, caplog):
    monkeypatch.setattr(checks, "get_total_packages_to_update", value=lambda reposdir: packages)
    monkeypatch.setattr(checks, "ask_to_continue", value=lambda: mock.Mock())

    check_package_updates()
    if exception:
        expected = expected.format(len(packages))

    assert expected in caplog.records[-1].message


def test_check_package_updates_with_repoerror(monkeypatch, caplog):
    get_total_packages_to_update_mock = mock.Mock(side_effect=pkgmanager.RepoError)
    monkeypatch.setattr(checks, "get_total_packages_to_update", value=get_total_packages_to_update_mock)
    monkeypatch.setattr(checks, "ask_to_continue", value=lambda: mock.Mock())

    check_package_updates()
    # This is -2 because the last message is the error from the RepoError class.
    assert (
        "There was an error while checking whether the installed packages are up-to-date." in caplog.records[-2].message
    )


@centos8
def test_check_package_updates_without_internet(pretend_os, tmpdir, monkeypatch, caplog):
    monkeypatch.setattr(checks, "get_hardcoded_repofiles_dir", value=lambda: str(tmpdir))
    system_info.has_internet_access = False
    check_package_updates()

    assert "Skipping the check as no internet connection has been detected." in caplog.records[-1].message


class TestIsLoadedKernelLatest:
    @oracle8
    def test_is_loaded_kernel_latest_skip_on_not_latest_ol(
        self,
        pretend_os,
        caplog,
    ):
        message = (
            "Skipping the check because there are no publicly available Oracle Linux Server 8.4 repositories available."
        )

        is_loaded_kernel_latest()

        assert message in caplog.records[-1].message

    @pytest.mark.parametrize(
        (
            "repoquery_version",
            "uname_version",
            "return_code",
            "package_name",
        ),
        (
            (
                "C2R\t1634146676\t3.10.0-1160.45.1.el7\tbaseos",
                "3.10.0-1160.42.2.el7.x86_64",
                0,
                "kernel-core",
            ),
        ),
    )
    @centos8
    def test_is_loaded_kernel_latest_eus_system_system_exit(
        self,
        pretend_os,
        repoquery_version,
        uname_version,
        return_code,
        package_name,
        tmpdir,
        monkeypatch,
        caplog,
    ):
        fake_reposdir_path = str(tmpdir)
        monkeypatch.setattr(
            checks,
            "get_hardcoded_repofiles_dir",
            value=lambda: fake_reposdir_path,
        )

        monkeypatch.setattr(checks.system_info, "has_internet_access", True)

        run_subprocess_mocked = mock.Mock(
            spec=run_subprocess,
            side_effect=run_subprocess_side_effect(
                (
                    (
                        "repoquery",
                        "--setopt=exclude=",
                        "--quiet",
                        "--qf",
                        "C2R\\t%{BUILDTIME}\\t%{VERSION}-%{RELEASE}\\t%{REPOID}",
                        "--setopt=reposdir=%s" % fake_reposdir_path,
                        package_name,
                    ),
                    (
                        repoquery_version,
                        return_code,
                    ),
                ),
                (("uname", "-r"), (uname_version, return_code)),
            ),
        )
        monkeypatch.setattr(
            checks,
            "run_subprocess",
            value=run_subprocess_mocked,
        )

        with pytest.raises(SystemExit):
            is_loaded_kernel_latest()

        repoquery_kernel_version = repoquery_version.split("\t")[2]
        uname_kernel_version = uname_version.rsplit(".", 1)[0]
        assert (
            "The version of the loaded kernel is different from the latest version in repositories defined in the %s folder"
            % fake_reposdir_path
        )
        assert (
            "Latest kernel version available in baseos: %s\n" % repoquery_kernel_version in caplog.records[-1].message
        )
        assert "Loaded kernel version: %s\n" % uname_kernel_version in caplog.records[-1].message

    @centos8
    def test_is_loaded_kernel_latest_eus_system(self, pretend_os, tmpdir, monkeypatch, caplog):
        fake_reposdir_path = str(tmpdir)
        monkeypatch.setattr(
            checks,
            "get_hardcoded_repofiles_dir",
            value=lambda: fake_reposdir_path,
        )

        monkeypatch.setattr(checks.system_info, "has_internet_access", True)

        run_subprocess_mocked = mock.Mock(
            spec=run_subprocess,
            side_effect=run_subprocess_side_effect(
                (
                    (
                        "repoquery",
                        "--setopt=exclude=",
                        "--quiet",
                        "--qf",
                        "C2R\\t%{BUILDTIME}\\t%{VERSION}-%{RELEASE}\\t%{REPOID}",
                        "--setopt=reposdir=%s" % fake_reposdir_path,
                        "kernel-core",
                    ),
                    (
                        "C2R\t1634146676\t3.10.0-1160.45.1.el7\tbaseos",
                        0,
                    ),
                ),
                (("uname", "-r"), ("3.10.0-1160.45.1.el7.x86_64", 0)),
            ),
        )
        monkeypatch.setattr(
            checks,
            "run_subprocess",
            value=run_subprocess_mocked,
        )

        is_loaded_kernel_latest()
        assert "The currently loaded kernel is at the latest version." in caplog.records[-1].message

    @centos8
    def test_is_loaded_kernel_latest_eus_system_no_connection(self, pretend_os, monkeypatch, tmpdir, caplog):
        monkeypatch.setattr(checks, "get_hardcoded_repofiles_dir", value=lambda: str(tmpdir))
        system_info.has_internet_access = False

        is_loaded_kernel_latest()
        assert "Skipping the check as no internet connection has been detected." in caplog.records[-1].message

    @centos8
    @pytest.mark.parametrize(
        (
            "repoquery_version",
            "return_code",
            "package_name",
            "unsupported_skip",
            "expected",
        ),
        (
            pytest.param(
                "",
                0,
                "kernel-core",
                "1",
                "Detected 'CONVERT2RHEL_UNSUPPORTED_SKIP_KERNEL_CURRENCY_CHECK' environment variable",
                id="Unsupported skip with environment var set to 1",
            ),
            pytest.param(
                "",
                0,
                "kernel-core",
                "0",
                "Detected 'CONVERT2RHEL_UNSUPPORTED_SKIP_KERNEL_CURRENCY_CHECK' environment variable",
                id="Unsupported skip with environment var set to 0",
            ),
            pytest.param(
                "",
                0,
                "kernel-core",
                None,
                "Could not find any {0} from repositories to compare against the loaded kernel.",
                id="Repoquery failure without environment var",
            ),
        ),
    )
    def test_is_loaded_kernel_latest_unsupported_skip(
        self,
        pretend_os,
        repoquery_version,
        return_code,
        package_name,
        unsupported_skip,
        expected,
        monkeypatch,
        caplog,
    ):
        run_subprocess_mocked = mock.Mock(
            spec=run_subprocess,
            side_effect=run_subprocess_side_effect(
                (
                    (
                        "repoquery",
                        "--setopt=exclude=",
                        "--quiet",
                        "--qf",
                        "C2R\\t%{BUILDTIME}\\t%{VERSION}-%{RELEASE}\\t%{REPOID}",
                        package_name,
                    ),
                    (
                        repoquery_version,
                        return_code,
                    ),
                ),
            ),
        )
        monkeypatch.setattr(
            checks,
            "run_subprocess",
            value=run_subprocess_mocked,
        )
        if unsupported_skip:
            monkeypatch.setattr(
                os,
                "environ",
                {"CONVERT2RHEL_UNSUPPORTED_SKIP_KERNEL_CURRENCY_CHECK": unsupported_skip},
            )

        if not unsupported_skip:
            with pytest.raises(SystemExit):
                is_loaded_kernel_latest()
            expected = expected.format(package_name)
        else:
            is_loaded_kernel_latest()

        assert expected in caplog.records[-1].message

    @pytest.mark.parametrize(
        (
            "repoquery_version",
            "uname_version",
            "return_code",
            "major_ver",
            "package_name",
            "expected_message",
        ),
        (
            (
                "C2R\t1634146676\t3.10.0-1160.45.1.el7\tbaseos",
                "3.10.0-1160.42.2.el7.x86_64",
                1,
                8,
                "kernel-core",
                "Couldn't fetch the list of the most recent kernels available in the repositories.",
            ),
            (
                "C2R\t1634146676\t3.10.0-1160.45.1.el7\tbaseos",
                "3.10.0-1160.45.1.el7.x86_64",
                0,
                7,
                "kernel",
                "The currently loaded kernel is at the latest version.",
            ),
            (
                """
                Repository base is listed more than once in the configuration\n
                Repository updates is listed more than once in the configuration\n
                Repository extras is listed more than once in the configuration\n
                Repository centosplus is listed more than once in the configuration\n
                C2R\t1634146676\t3.10.0-1160.45.1.el7\tbaseos\n
                Could not retrieve mirrorlist http://mirorlist.centos.org/?release=7&arch=x86_64&repo=os&infra=stock error was\n
                14: curl#6 - "Could not resolve host: mirorlist.centos.org; Unknown error"\n
                Repo convert2rhel-for-rhel-7-rpms forced skip_if_unavailable=True due to: /etc/rhsm/ca/redhat-uep.pem\n
                """,
                "3.10.0-1160.45.1.el7.x86_64",
                0,
                8,
                "kernel-core",
                "The currently loaded kernel is at the latest version.",
            ),
            (
                """
                gargabe-output before the good line\n
                C2R\t1634146676\t3.10.0-1160.45.1.el7\tbaseos\n
                more garbage\n
                """,
                "3.10.0-1160.45.1.el7.x86_64",
                0,
                8,
                "kernel-core",
                "The currently loaded kernel is at the latest version.",
            ),
        ),
    )
    def test_is_loaded_kernel_latest(
        self,
        repoquery_version,
        uname_version,
        return_code,
        major_ver,
        package_name,
        expected_message,
        monkeypatch,
        caplog,
    ):
        # Using the minor version as 99, so the tests should never fail because of a
        # constraint in the code, since we don't mind the minor version number (for
        # now), and require only that the major version to be in the range of 6 to
        # 8, we can set the minor version to 99 to avoid hardcoded checks in the
        # code.
        Version = namedtuple("Version", ("major", "minor"))
        monkeypatch.setattr(
            checks.system_info,
            "version",
            value=Version(major=major_ver, minor=99),
        )
        system_info.id = "centos"
        run_subprocess_mocked = mock.Mock(
            spec=run_subprocess,
            side_effect=run_subprocess_side_effect(
                (
                    (
                        "repoquery",
                        "--setopt=exclude=",
                        "--quiet",
                        "--qf",
                        "C2R\\t%{BUILDTIME}\\t%{VERSION}-%{RELEASE}\\t%{REPOID}",
                        package_name,
                    ),
                    (
                        repoquery_version,
                        return_code,
                    ),
                ),
                (("uname", "-r"), (uname_version, return_code)),
            ),
        )
        monkeypatch.setattr(
            checks,
            "run_subprocess",
            value=run_subprocess_mocked,
        )

        is_loaded_kernel_latest()
        assert expected_message in caplog.records[-1].message

    def test_is_loaded_kernel_latest_system_exit(self, monkeypatch, caplog):
        repoquery_version = "C2R\t1634146676\t3.10.0-1160.45.1.el7\tbaseos"
        uname_version = "3.10.0-1160.42.2.el7.x86_64"

        # Using the minor version as 99, so the tests should never fail because of a
        # constraint in the code, since we don't mind the minor version number (for
        # now), and require only that the major version to be in the range of 6 to
        # 8, we can set the minor version to 99 to avoid hardcoded checks in the
        # code.
        Version = namedtuple("Version", ("major", "minor"))
        monkeypatch.setattr(
            checks.system_info,
            "version",
            value=Version(major=8, minor=99),
        )
        system_info.id = "centos"
        run_subprocess_mocked = mock.Mock(
            spec=run_subprocess,
            side_effect=run_subprocess_side_effect(
                (
                    (
                        "repoquery",
                        "--setopt=exclude=",
                        "--quiet",
                        "--qf",
                        "C2R\\t%{BUILDTIME}\\t%{VERSION}-%{RELEASE}\\t%{REPOID}",
                        "kernel-core",
                    ),
                    (
                        repoquery_version,
                        0,
                    ),
                ),
                (("uname", "-r"), (uname_version, 0)),
            ),
        )
        monkeypatch.setattr(
            checks,
            "run_subprocess",
            value=run_subprocess_mocked,
        )

        with pytest.raises(SystemExit):
            is_loaded_kernel_latest()

        repoquery_kernel_version = repoquery_version.split("\t")[2]
        uname_kernel_version = uname_version.rsplit(".", 1)[0]
        assert "Latest kernel version available in baseos: %s" % repoquery_kernel_version in caplog.records[-1].message
        assert "Loaded kernel version: %s\n\n" % uname_kernel_version in caplog.records[-1].message


@pytest.mark.parametrize(
    ("no_rhsm", "dbus_running", "log_msg"),
    (
        (True, True, "Skipping the check because we have been asked not to subscribe this system to RHSM."),
        (True, False, "Skipping the check because we have been asked not to subscribe this system to RHSM."),
        (False, True, "DBus Daemon is running"),
    ),
)
def test_check_dbus_is_running(
    caplog, monkeypatch, global_tool_opts, global_system_info, no_rhsm, dbus_running, log_msg
):
    monkeypatch.setattr(checks, "tool_opts", global_tool_opts)
    global_tool_opts.no_rhsm = no_rhsm
    monkeypatch.setattr(checks, "system_info", global_system_info)
    global_system_info.dbus_running = dbus_running

    assert checks.check_dbus_is_running() is None
    assert caplog.records[-1].message == log_msg

    assert log_msg == caplog.records[-1].message


def test_check_dbus_is_running_not_running(caplog, monkeypatch, global_tool_opts, global_system_info):
    monkeypatch.setattr(checks, "tool_opts", global_tool_opts)
    global_tool_opts.no_rhsm = False
    monkeypatch.setattr(checks, "system_info", global_system_info)
    global_system_info.dbus_running = False

    with pytest.raises(SystemExit):
        checks.check_dbus_is_running()

    log_msg = (
        "Could not find a running DBus Daemon which is needed to"
        " register with subscription manager.\nPlease start dbus using `systemctl"
        " start dbus`"
    )
    assert log_msg == caplog.records[-1].message
    assert caplog.records[-1].levelname == "CRITICAL"


@pytest.mark.parametrize(
    ("latest_installed_kernel", "subprocess_output", "expected"),
    (
        ("6.1.7-200.fc37.x86_64", ("test", 0), True),
        ("6.1.7-200.fc37.x86_64", ("error", 1), False),
    ),
)
def test_is_initramfs_file_valid(latest_installed_kernel, subprocess_output, expected, tmpdir, caplog, monkeypatch):
    initramfs_file = tmpdir.mkdir("/boot").join("initramfs-%s.img")
    initramfs_file = str(initramfs_file)
    initramfs_file = initramfs_file % latest_installed_kernel
    with open(initramfs_file, mode="w") as _:
        pass

    monkeypatch.setattr(checks, "INITRAMFS_FILEPATH", initramfs_file)
    monkeypatch.setattr(checks, "run_subprocess", mock.Mock(return_value=subprocess_output))
    result = checks._is_initramfs_file_valid(initramfs_file)
    assert result == expected

    if not expected:
        assert "Couldn't verify initramfs file. It may be corrupted." in caplog.records[-2].message
        assert "Output of lsinitrd: %s" % subprocess_output[0] in caplog.records[-1].message


@centos8
def test_check_kernel_boot_files(pretend_os, tmpdir, caplog, monkeypatch):
    rpm_last_kernel_output = ("kernel-core-6.1.8-200.fc37.x86_64 Wed 01 Feb 2023 14:01:01 -03", 0)
    latest_installed_kernel = "6.1.8-200.fc37.x86_64"

    boot_folder = tmpdir.mkdir("/boot")
    initramfs_file = boot_folder.join("initramfs-%s.img")
    vmlinuz_file = boot_folder.join("vmlinuz-%s")
    initramfs_file = str(initramfs_file)
    vmlinuz_file = str(vmlinuz_file)

    with open(initramfs_file % latest_installed_kernel, mode="w") as _:
        pass

    with open(vmlinuz_file % latest_installed_kernel, mode="w") as _:
        pass

    monkeypatch.setattr(checks, "VMLINUZ_FILEPATH", vmlinuz_file)
    monkeypatch.setattr(checks, "INITRAMFS_FILEPATH", initramfs_file)
    monkeypatch.setattr(checks, "run_subprocess", mock.Mock(side_effect=[rpm_last_kernel_output, ("test", 0)]))

    checks.check_kernel_boot_files()
    assert "The initramfs and vmlinuz files are valid." in caplog.records[-1].message


@pytest.mark.parametrize(
    ("create_initramfs", "create_vmlinuz", "run_piped_subprocess", "rpm_last_kernel_output", "latest_installed_kernel"),
    (
        pytest.param(
            False,
            False,
            ("", 0),
            ("kernel-core-6.1.8-200.fc37.x86_64 Wed 01 Feb 2023 14:01:01 -03", 0),
            "6.1.8-200.fc37.x86_64",
            id="both-files-missing",
        ),
        pytest.param(
            True,
            False,
            ("test", 0),
            ("kernel-core-6.1.8-200.fc37.x86_64 Wed 01 Feb 2023 14:01:01 -03", 0),
            "6.1.8-200.fc37.x86_64",
            id="vmlinuz-missing",
        ),
        pytest.param(
            False,
            True,
            ("test", 0),
            ("kernel-core-6.1.8-200.fc37.x86_64 Wed 01 Feb 2023 14:01:01 -03", 0),
            "6.1.8-200.fc37.x86_64",
            id="initramfs-missing",
        ),
        pytest.param(
            True,
            True,
            ("error", 1),
            ("kernel-core-6.1.8-200.fc37.x86_64 Wed 01 Feb 2023 14:01:01 -03", 0),
            "6.1.8-200.fc37.x86_64",
            id="initramfs-corrupted",
        ),
    ),
)
@centos8
def test_check_kernel_boot_files_missing(
    pretend_os,
    create_initramfs,
    create_vmlinuz,
    run_piped_subprocess,
    rpm_last_kernel_output,
    latest_installed_kernel,
    tmpdir,
    caplog,
    monkeypatch,
):
    """
    This test will check if we output the warning message correctly if either
    initramfs or vmlinuz are missing.
    """
    # We are mocking both subprocess calls here in order to make it easier for
    # testing any type of parametrization we may include in the future. Note
    # that the second iteration may not run sometimes, as this is specific for
    # when we want to check if a file is corrupted or not.
    monkeypatch.setattr(
        checks,
        "run_subprocess",
        mock.Mock(
            side_effect=[
                rpm_last_kernel_output,
                run_piped_subprocess,
            ]
        ),
    )
    boot_folder = tmpdir.mkdir("/boot")
    if create_initramfs:
        initramfs_file = boot_folder.join("initramfs-%s.img")
        initramfs_file = str(initramfs_file)
        with open(initramfs_file % latest_installed_kernel, mode="w") as _:
            pass

        monkeypatch.setattr(checks, "INITRAMFS_FILEPATH", initramfs_file)
    else:
        monkeypatch.setattr(checks, "INITRAMFS_FILEPATH", "/non-existing-%s.img")

    if create_vmlinuz:
        vmlinuz_file = boot_folder.join("vmlinuz-%s")
        vmlinuz_file = str(vmlinuz_file)
        with open(vmlinuz_file % latest_installed_kernel, mode="w") as _:
            pass

        monkeypatch.setattr(checks, "VMLINUZ_FILEPATH", vmlinuz_file)
    else:
        monkeypatch.setattr(checks, "VMLINUZ_FILEPATH", "/non-existing-%s")

    checks.check_kernel_boot_files()
    assert "Couldn't verify the kernel boot files in the boot partition." in caplog.records[-1].message
