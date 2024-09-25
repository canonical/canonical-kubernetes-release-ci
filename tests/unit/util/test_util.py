import argparse
import unittest.mock as mock

import semver
import util.util as util


@mock.patch("util.repo.ls_tree")
def test_flavors(mock_ls_tree):
    mock_ls_tree.return_value = [
        "build-scripts/patches/flavor1/patch1",
        "build-scripts/patches/flavor2/patch2",
    ]
    expected = ["classic", "flavor1", "flavor2"]
    result = util.flavors("some_dir")
    assert result == expected


def test_recipe_name_tip():
    ver = semver.Version.parse("1.2.3")
    flavor = "flavor1"
    tip = True
    expected = "k8s-snap-tip-flavor1"
    result = util.recipe_name(flavor, ver, tip)
    assert result == expected


def test_recipe_name_non_tip():
    ver = semver.Version.parse("1.2.3")
    flavor = "flavor1"
    tip = False
    expected = "k8s-snap-1.2-flavor1"
    result = util.recipe_name(flavor, ver, tip)
    assert result == expected


@mock.patch("argparse.ArgumentParser.parse_args")
@mock.patch("util.util.setup_logging")
def test_setup_arguments(mock_setup_logging, mock_parse_args):
    mock_args = argparse.Namespace(dry_run=False, loglevel="INFO")
    mock_parse_args.return_value = mock_args
    parser = argparse.ArgumentParser()
    args = util.setup_arguments(parser)
    mock_setup_logging.assert_called_once_with(mock_args)
    assert args == mock_args


@mock.patch("util.util.LOG")
def test_setup_logging(mock_logger):
    mock_logger.root.level = 30  # WARNING
    mock_args = argparse.Namespace(dry_run=False, loglevel="INFO")
    util.setup_logging(mock_args)
    mock_logger.root.setLevel.assert_called_once_with(level="INFO")
