from unittest.mock import MagicMock, patch
from uuid import UUID

import pytest
from util.sqa import (TestPlanInstanceStatus, _product_versions,
                      _test_plan_instances, create_test_plan_instance)


@pytest.fixture
def mock_subprocess():
    with patch("subprocess.run") as mock:
        yield mock


def test_product_versions(mock_subprocess):
    mock_product_versions = MagicMock()
    
    with open('tests/unit/util/testdata/productversions.json', 'r') as file:
        mock_product_versions.stdout = file.read()

    mock_subprocess.return_value = mock_product_versions
    product_versions = _product_versions("1.32/candidate", "179")

    assert len(product_versions)==2

def test_create_test_plan_instance(mock_subprocess):
    mock_test_plan_instances = MagicMock()

    with open('tests/unit/util/testdata/createtestplaninstance.txt', 'r') as file:
        mock_test_plan_instances.stdout = file.read()

    mock_subprocess.return_value = mock_test_plan_instances
    test_plan_instance = create_test_plan_instance("7c409d40-b2dd-44e2-b438-ef7c39b35cba")

    assert test_plan_instance.uuid == UUID("ccdcb402-78cf-4141-bc64-73f77d29d670")

def test_test_plan_instances(mock_subprocess):
    mock_test_plan_instances = MagicMock()

    with open('tests/unit/util/testdata/testplaninstances.txt', 'r') as file:
        mock_test_plan_instances.stdout = file.read()

    mock_subprocess.return_value = mock_test_plan_instances

    uuids = _test_plan_instances("7c409d40-b2dd-44e2-b438-ef7c39b35cba", TestPlanInstanceStatus.IN_PROGRESS)

    assert len(uuids) == 11
