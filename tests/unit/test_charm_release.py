"""

Scenarios:

* First release for channel; no release run -> start release run
* No new release in candidate channel -> no action
* New release in candidate channel; release run in progress -> abort current testplan and start new release run

"""

from unittest.mock import MagicMock, patch

import charm_release
import pytest
from util import charmhub, sqa


@pytest.fixture
def mock_sqa():
    with patch("charm_release.sqa") as mock:
        yield mock


@pytest.fixture
def mock_charmhub():
    with patch("charm_release.charmhub") as mock:
        yield mock


def get_same_revision_matrix_side_effect(charm_name, channel):
    revision_matrix = charmhub.RevisionMatrix()
    if channel == "1.32/candidate":
        revision_matrix.set("amd64", "22.04", "741")
    else:
        revision_matrix.set("amd64", "22.04", "741")

    return revision_matrix


def get_revision_matrix_side_effect(charm_name, channel):
    revision_matrix = charmhub.RevisionMatrix()
    if channel == "1.32/candidate":
        revision_matrix.set("amd64", "22.04", "741")
    else:
        revision_matrix.set("amd64", "22.04", "548")

    return revision_matrix


def test_no_release_run(mock_sqa, mock_charmhub):
    """If there is no release run exists for the given track, a new one should be started."""
    mock_charmhub.get_revision_matrix.side_effect = get_revision_matrix_side_effect
    mock_charmhub.Bundle.return_value = charmhub.Bundle("k8s-operator")
    mock_sqa.TestPlanInstanceStatus = sqa.TestPlanInstanceStatus
    mock_sqa.current_test_plan_instance_status.return_value = None

    priority_generator = sqa.PriorityGenerator()
    mock_args = MagicMock()
    mock_args.from_risk = "candidate"
    mock_args.to_risk = "stable"
    mock_args.dry_run = False
    mock_args.charms = ["k8s"]
    charm_release.process_track("1.32", priority_generator, mock_args)

    mock_sqa.start_release_test.assert_called_once_with("1.32/candidate",
                                                        "22.04", "amd64",
                                                        {"k8s_revision": "741"},
                                                        "k8s-operator-k8s-741", 1)
    mock_charmhub.promote_charm.assert_not_called()


def test_no_new_release_no_action(mock_sqa, mock_charmhub):
    """No new release in candidate channel -> no action"""
    mock_charmhub.get_revision_matrix.side_effect = get_same_revision_matrix_side_effect
    mock_sqa.current_test_plan_instance_status.return_value = None

    priority_generator = sqa.PriorityGenerator()
    mock_args = MagicMock()
    mock_args.from_risk = "candidate"
    mock_args.to_risk = "stable"
    mock_args.dry_run = False
    mock_args.charms = ["k8s"]
    charm_release.process_track("1.32", priority_generator, mock_args)
    mock_sqa.start_release_test.assert_not_called()
    mock_charmhub.promote_charm.assert_not_called()
