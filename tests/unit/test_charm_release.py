"""

Scenarios:

* First release for channel; no release run -> start release run
* No new release in candidate channel -> no action
* New release in candidate channel; release run in progress -> abort current testplan and start new release run

"""
from unittest.mock import MagicMock, patch

import charm_release
import pytest


@pytest.fixture
def mock_sqa():
    with patch("util.sqa") as mock:
        yield mock

@pytest.fixture
def mock_charmhub():
    with patch("util.charmhub") as mock:
        yield mock

def test_no_release_run(mock_sqa, mock_charmhub):
    """If there is no release run exists for the given track, a new one should be started."""
    mock_sqa.current_release_run.return_value = None

    charm_release.process_track("1.32")

    mock_sqa.start_release_test.assert_called_once_with("1.32")
    mock_sqa.abort_release_test.assert_not_called()
    mock_charmhub.promote_charm_revisions.assert_not_called()

@pytest.mark.skip("Not implemented yet")
def test_no_new_release_no_action(mock_sqa, mock_charmhub):
    """No new release in candidate channel -> no action"""
    mock_release_run = MagicMock()
    mock_release_run.version = "1.32.0"
    mock_release_run.in_progress = False
    mock_release_run.succeeded = True
    mock_release_run.failed = False
    mock_sqa.current_release_run.return_value = mock_release_run
    mock_charmhub.get_channel_version_string.return_value = "1.32.0"

    charm_release.process_track("1.32")

    mock_sqa.start_release_test.assert_not_called()
    mock_sqa.abort_release_test.assert_not_called()
    mock_charmhub.promote_charm_revisions.assert_not_called()

@pytest.mark.skip("Not implemented yet")
def test_new_release_run_in_progress(mock_sqa, mock_charmhub):
    """New release in candidate channel; release run in progress -> abort current testplan and start new release run"""
    mock_release_run = MagicMock()
    mock_release_run.version = "1.31.0"
    mock_release_run.in_progress = True
    mock_release_run.succeeded = False
    mock_release_run.failed = False
    mock_sqa.current_release_run.return_value = mock_release_run
    mock_charmhub.get_channel_version_string.return_value = "1.32.0"

    charm_release.process_track("1.32")

    mock_sqa.abort_release_test.assert_called_once_with("1.32")
    mock_sqa.start_release_test.assert_called_once_with("1.32")
    mock_charmhub.promote_charm_revisions.assert_not_called()
