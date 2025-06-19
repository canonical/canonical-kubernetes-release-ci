import datetime
import json
import logging
import os
import re
import shlex
import subprocess
import tempfile
import threading
from enum import StrEnum
from typing import Optional
from uuid import UUID

from jinja2 import Environment, FileSystemLoader, select_autoescape
from pydantic import BaseModel, Field, TypeAdapter, field_validator

log = logging.getLogger(__name__)

# Currently this is tribal knowledge, eventually this should appear in the SQA docs:
# https://canonical-weebl-tools.readthedocs-hosted.com/en/latest/products/index.html
K8S_OPERATOR_PRODUCT_UUID = "432252b9-2041-4a9a-aece-37c2dbd54201"

K8S_OPERATOR_TEST_PLAN_ID = "394fb5b6-1698-4226-bd3e-23b471ee1bd4"
K8S_OPERATOR_TEST_PLAN_NAME = "CanonicalK8s"

class InvalidSQAInput(Exception):
    pass

class SQAFailure(Exception):
    pass


def get_series(base: str) -> str | None:
    base_series_map = {
        "24.04": "noble",
        "22.04": "jammy",
        "20.04": "focal",
    }

    return base_series_map.get(base)

class PriorityGenerator:
    """
    PriorityGenerator is an atomic counter to create atomic priorities for new TPIs we create.
    """
    def __init__(self, initial=0):
        self.value = initial
        self._lock = threading.Lock()

    @property
    def next_priority(self):
        with self._lock:
            self.value += 1
            return self.value

class Addon(BaseModel):
    id: str
    name: str
    file: str
    created_at: datetime.datetime
    updated_at: datetime.datetime
    uuid: UUID

    @field_validator("created_at", "updated_at", mode="before")
    @classmethod
    def parse_datetime(cls, v: str) -> datetime.datetime:
        return datetime.datetime.fromisoformat(v.replace("Z", "+00:00"))


class ProductVersion(BaseModel):
    uuid: UUID
    version: str
    channel: str
    revision: str
    product_name: str = Field(alias="product.name")
    product_uuid: str = Field(alias="product.uuid")

class TestPlanInstanceStatus(StrEnum):
    IN_PROGRESS = "In Progress"
    SKIPPED = "skipped"
    ERROR = "error"
    ABORTED = "aborted"
    FAILURE = "failure"
    SUCCESS = "success"
    UNKNOWN = "unknown"
    PASSED = "Passed"
    FAILED = "Failed"
    RELEASED = "Released"

    @classmethod
    def from_name(cls, name):
        for state in cls:
            if state.value.lower() == name.lower():
                return state
        raise ValueError(f"Invalid state name: {name}")

    @property
    def in_progress(self):
        return self == TestPlanInstanceStatus.IN_PROGRESS

    @property
    def succeeded(self):
        return self == TestPlanInstanceStatus.PASSED

    @property
    def failed(self):
        return self in [
            TestPlanInstanceStatus.FAILED,
        ]


class TestPlanInstance(BaseModel):
    test_plan: str
    created_at: datetime.datetime
    updated_at: datetime.datetime
    id: str
    effective_priority: float
    status: TestPlanInstanceStatus
    uuid: UUID
    product_under_test: str

    @field_validator("created_at", "updated_at", mode="before")
    @classmethod
    def parse_datetime(cls, v: str) -> datetime.datetime:
        return datetime.datetime.fromisoformat(v.replace("Z", "+00:00"))

    @field_validator("status", mode="before")
    @classmethod
    def parse_status(cls, v: str) -> TestPlanInstanceStatus:
        return TestPlanInstanceStatus.from_name(v)


def _create_product_version(channel: str, base: str, version: str) -> ProductVersion:
    if not (series := get_series(base)):
        raise InvalidSQAInput("invalid base provided")

    # NOTE(Reza): SQA only supports revision and not an arbitrary version, so we are providing only
    # the revision of the k8s charm as the identifier. 
    k8s_revision_match = re.search(r'k8s-(\d+)', version)

    if not k8s_revision_match:
        raise InvalidSQAInput("could not extract revision from version")

    k8s_revision = k8s_revision_match.group(1)

    product_version_cmd = f"productversion add --format json --product-uuid {K8S_OPERATOR_PRODUCT_UUID} --channel {channel} --revision {k8s_revision} --series {series}"

    log.info("Creating product version for channel %s vision %s...\n %s", channel, version, product_version_cmd)

    product_version_response = _weebl_run(*shlex.split(product_version_cmd))

    log.info(product_version_response)
    product_versions = parse_response_lists(ProductVersion, product_version_response)


    if not product_versions:
        raise SQAFailure("no product version returned from create command")
    
    if len(product_versions) > 1:
        raise SQAFailure("Too many product versions from create command")

    return product_versions[0]


def _create_test_plan_instance(product_version_uuid: str, addon_uuid: str, priority: int) -> TestPlanInstance:
    test_plan_instance_cmd = f"testplaninstance add --format json --test_plan {K8S_OPERATOR_TEST_PLAN_ID} --addon_id {addon_uuid} --status 'In Progress' --base_priority {priority} --product_under_test {product_version_uuid}"

    log.info("Creating test plan instance for product version %s...\n %s", product_version_uuid, test_plan_instance_cmd)

    test_plan_instance_response = _weebl_run(*shlex.split(test_plan_instance_cmd))

    log.info(json_str := test_plan_instance_response)
    end_index = json_str.rfind("]")

    if end_index != -1:
        json_str = json_str[: end_index + 1]

    test_plan_instances = parse_response_lists(TestPlanInstance, json_str)


    if not test_plan_instances:
        raise SQAFailure("no test plan instance returned from create command")
    
    if len(test_plan_instances) > 1:
        raise SQAFailure("Too many test plan instance from create command")

    return test_plan_instances[0]


def current_test_plan_instance_status(
    channel, base, version
) -> Optional[TestPlanInstanceStatus]:
    """
    First try to get any passed TPIs for the (channel, base, version)
    If no passed TPI found, try to get in progress TPIs
    If no in progress TPI found, try to get failed/(in-)error TPIs
    If no failed TPI found, return None
    The aborted TPIs are ignored since they don't semantically hold
    any information about the state of a track
    """
    product_versions = _product_versions(channel, base, version)

    if not product_versions:
        return None

    passed_test_plan_instances = _joined_test_plan_instances(
        product_versions, TestPlanInstanceStatus.PASSED
    )
    if passed_test_plan_instances:
        return TestPlanInstanceStatus.PASSED

    in_progress_test_plan_instances = _joined_test_plan_instances(
        product_versions, TestPlanInstanceStatus.IN_PROGRESS
    )
    if in_progress_test_plan_instances:
        return TestPlanInstanceStatus.IN_PROGRESS

    failed_test_plan_instances = _joined_test_plan_instances(
        product_versions, TestPlanInstanceStatus.FAILED
    )
    if failed_test_plan_instances:
        return TestPlanInstanceStatus.FAILED


    return None


def _joined_test_plan_instances(
    product_versions: list[ProductVersion], status: TestPlanInstanceStatus
) -> list[UUID]:
    return [
        ins
        for product_version in product_versions
        for ins in _test_plan_instances(str(product_version.uuid), status)
    ]


def _test_plan_instances(
    productversion_uuid, status: TestPlanInstanceStatus
) -> list[UUID]:
    test_plan_instances_cmd = f"testplaninstance list --format json --productversion-uuid {productversion_uuid} --status '{status.value.lower()}'"

    log.info(
        "Getting test plan instances for product version %s with status %s...\n %s", 
        productversion_uuid, status, test_plan_instances_cmd
    )

    test_plan_instances_response = _weebl_run(*shlex.split(test_plan_instances_cmd))

    log.info(json_str := test_plan_instances_response)
    start_index = json_str.rfind("{")

    if start_index != -1:
        json_str = json_str[start_index:]

    if not (json_dict := json.loads(json_str.strip())):
        return []

    uuids = [UUID(item) for item in json_dict[K8S_OPERATOR_TEST_PLAN_NAME]]

    return uuids


def _product_versions(channel, base, version) -> list[ProductVersion]:
    if not (series := get_series(base)):
        raise InvalidSQAInput("invalid base provided")
    
    # NOTE(Reza): SQA only supports revision and not an arbitrary version, so we are providing only
    # the revision of the k8s charm as the identifier. 
    k8s_revision_match = re.search(r'k8s-(\d+)', version)

    if not k8s_revision_match:
        raise InvalidSQAInput

    k8s_revision = k8s_revision_match.group(1)
    
    product_versions_cmd = f"productversion list --channel {channel} --revision {k8s_revision} --series {series} --format json"

    log.info("Getting product versions for channel %s version %s\n %s", channel, version, product_versions_cmd)

    product_versions_response = _weebl_run(*shlex.split(product_versions_cmd))
   
    log.info(product_versions_response)
    product_versions = parse_response_lists(ProductVersion, product_versions_response)

    return product_versions


def start_release_test(channel, base, arch, revisions, version, priority):
    product_versions = _product_versions(channel, base, version)
    if product_versions:
        if len(product_versions) > 1:
            raise SQAFailure(f"the ({channel, base, arch}) is supposed to have only one product version for version {version}")

        log.info(
            f"using already defined product version {product_versions[0].uuid} to create TPI"
        )

        product_version = product_versions[0]
    else:
        product_version = _create_product_version(channel, base, version)

    variables = {
        "base": base,
        "arch": arch,
        "channel": channel,
        **revisions
    }

    addon = _create_addon(version, variables)

    test_plan_instance = _create_test_plan_instance(str(product_version.uuid), str(addon.uuid), priority)
    log.info(f"Started release test for {channel} with UUID: {test_plan_instance.uuid}")

def _get_addon(name: str) -> Optional[Addon]:
    show_addon_cmd = f"addon show {name} --format json"

    log.info("Getting the %s addon\n %s", name, show_addon_cmd)

    # TODO: remove this when SQA bug has been fixed
    # The SQA returns StopIteration in case of no addons
    try:
        show_addon_response = _weebl_run(*shlex.split(show_addon_cmd))
    except SQAFailure:
        return None

    log.info(show_addon_response)
    addons = parse_response_lists(Addon, show_addon_response)

    # there can be no addons for the provided name
    if not addons:
        return None
    
    if len(addons) > 1:
        raise SQAFailure("Too many addons from cshow command")

    return addons[0]

def _create_addon(version, variables) -> Addon:

    # return the addon if it's already defined before
    addon = _get_addon(version)
    if addon:
        log.info(f"Using the previously defined addon for {version}")
        return addon

    log.info(f"No previous addon found. Creating a new one for {version}...")
    home_dir = os.path.expanduser("~")
    with tempfile.TemporaryDirectory(dir=home_dir, delete=False) as temp_dir:
        # the name of the addon dir must be 'addon'
        addon_dir = os.path.join(temp_dir, "addon")
        os.makedirs(addon_dir)
        
        config_dir = os.path.join(addon_dir, "config")
        os.makedirs(config_dir)  

        log.info(f"addon directory created at: {addon_dir}")

        env = Environment(
            loader=FileSystemLoader("scripts/templates/canonical_k8s_sqa_addon"),
            autoescape=select_autoescape()
            )
        template_files = env.list_templates(extensions="j2")

        for template_name in template_files:
            template = env.get_template(template_name)
            rendered = template.render(variables)
            
            output_filename = os.path.splitext(template_name)[0]
            output_path = os.path.join(config_dir, output_filename)

            with open(output_path, "w") as f:
                f.write(rendered)
        
        create_addon_cmd = f"addon add --addon {addon_dir} --name {version} --format json"

        log.info("Creating an addon for version %s\n %s", version, create_addon_cmd)

        create_addon_response = _weebl_run(*shlex.split(create_addon_cmd))

    log.info(create_addon_response)
    addons = parse_response_lists(Addon, create_addon_response)

    if not addons:
        raise SQAFailure("no addon returned from create command")
    
    if len(addons) > 1:
        raise SQAFailure("Too many addons from create command")

    return addons[0]

def _weebl_run(*args, **kwds) -> str:
    kwds = {"text": True, "check": True, "capture_output": True, **kwds}
    try:
        response = subprocess.run(["/snap/bin/weebl-tools.sqalab", *args], **kwds)
    except subprocess.CalledProcessError as e:
        raise SQAFailure(f"{args[0]} failed: {e.stderr}")
    return response.stdout

def parse_response_lists(model, response_str: str) -> list:
    adapter = TypeAdapter(list[model])
    parsed_response = adapter.validate_json(response_str.strip())
    return parsed_response
