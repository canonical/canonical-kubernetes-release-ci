from unittest.mock import patch
from uuid import UUID

import pytest
from util.sqa import (TestPlanInstanceStatus, _create_addon,
                      _create_test_plan_instance, _product_versions,
                      _test_plan_instances)


@pytest.fixture
def mock_weebl_run():
    with patch("util.sqa._weebl_run") as mock:
        yield mock


def test_product_versions(mock_weebl_run):
    mock_product_versions: str

    with open("tests/unit/util/testdata/productversions.json", "r") as file:
        mock_product_versions = file.read()

    mock_weebl_run.return_value = mock_product_versions
    product_versions = _product_versions("1.32/candidate", "179")

    assert len(product_versions) == 2


def test_create_test_plan_instance(mock_weebl_run):
    mock_test_plan_instances: str

    with open("tests/unit/util/testdata/createtestplaninstance.txt", "r") as file:
        mock_test_plan_instances = file.read()

    mock_weebl_run.return_value = mock_test_plan_instances
    test_plan_instance = _create_test_plan_instance(
        "7c409d40-b2dd-44e2-b438-ef7c39b35cba",
        "b6d399db-f188-4de0-8870-1756f2de2e2c"
    )

    assert test_plan_instance.uuid == UUID("ccdcb402-78cf-4141-bc64-73f77d29d670")


def test_create_addon(mock_weebl_run):
    mock_test_plan_instances: str

    with open("tests/unit/util/testdata/createaddon.json", "r") as file:
        mock_test_plan_instances = file.read()

    mock_weebl_run.return_value = mock_test_plan_instances
    addon = _create_addon(
        "k8s-operator-k8s-741-k8s_worker-739",
        {
            "base": "22.04",
            "arch": "amd64",
            "channel": "1.32/candidate",
            "k8s_revision": "741",
            "k8s_worker_revision": "739"
        }
    )

    assert addon.uuid == UUID("b6d399db-f188-4de0-8870-1756f2de2e2c")


def test_test_plan_instances(mock_weebl_run):
    mock_test_plan_instances: str

    with open("tests/unit/util/testdata/testplaninstances.txt", "r") as file:
        mock_test_plan_instances = file.read()

    mock_weebl_run.return_value = mock_test_plan_instances

    uuids = _test_plan_instances(
        "7c409d40-b2dd-44e2-b438-ef7c39b35cba", TestPlanInstanceStatus.IN_PROGRESS
    )

    assert len(uuids) == 11
