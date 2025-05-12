import datetime
import json
import os
import shlex
import subprocess
import tempfile
import threading
from enum import StrEnum
from typing import Optional
from uuid import UUID

from jinja2 import Environment, FileSystemLoader, select_autoescape
from pydantic import BaseModel, Field, TypeAdapter, field_validator

# Currently this is tribal knowledge, eventually this should appear in the SQA docs:
# https://canonical-weebl-tools.readthedocs-hosted.com/en/latest/products/index.html
K8S_OPERATOR_PRODUCT_UUID = "3a8046a8-ef27-4ec7-a8a3-af6f470b96d7"

K8S_OPERATOR_TEST_PLAN_ID = "394fb5b6-1698-4226-bd3e-23b471ee1bd4"
K8S_OPERATOR_TEST_PLAN_NAME = "CanonicalK8s"


class SQAFailure(Exception):
    pass

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


def _create_product_version(channel: str, base: str, arch: str, version: str) -> ProductVersion:
    product_version_cmd = f"productversion add --format json --product-uuid {K8S_OPERATOR_PRODUCT_UUID} --channel {channel} --version {version} --series {base}"

    print(f"Creating product version for channel {channel} vision {version}...")
    print(product_version_cmd)

    product_version_response = _weebl_run(*shlex.split(product_version_cmd))

    print(product_version_response)
    product_versions = parse_response_lists(ProductVersion, product_version_response)


    if not product_versions:
        raise SQAFailure("no product version returned from create command")
    
    if len(product_versions) > 1:
        raise SQAFailure("Too many product versions from create command")

    return product_versions[0]


def _create_test_plan_instance(product_version_uuid: str, addon_uuid: str, priority: int) -> TestPlanInstance:
    test_plan_instance_cmd = f"testplaninstance add --format json --test_plan {K8S_OPERATOR_TEST_PLAN_ID} --addon_id {addon_uuid} --status 'In Progress' --base_priority {priority} --product_under_test {product_version_uuid}"

    print(f"Creating test plan instance for product version {product_version_uuid}...")
    print(test_plan_instance_cmd)

    test_plan_instance_response = _weebl_run(*shlex.split(test_plan_instance_cmd))

    print(json_str := test_plan_instance_response)
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
    channel, version
) -> Optional[TestPlanInstanceStatus]:
    """
    First try to get any passed TPIs for the (channel, revision)
    If no passed TPI found, try to get in progress TPIs
    If no in progress TPI found, try to get failed/(in-)error TPIs
    If no failed TPI found, return None
    The aborted TPIs are ignored since they don't semantically hold
    any information about the state of a track
    """
    product_versions = _product_versions(channel, version)

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

    print(
        f"Getting test plan instances for product version {productversion_uuid} with status {status}..."
    )
    print(test_plan_instances_cmd)

    test_plan_instances_response = _weebl_run(*shlex.split(test_plan_instances_cmd))

    print(json_str := test_plan_instances_response)
    start_index = json_str.rfind("{")

    if start_index != -1:
        json_str = json_str[start_index:]

    if not (json_dict := json.loads(json_str.strip())):
        return []

    uuids = [UUID(item) for item in json_dict[K8S_OPERATOR_TEST_PLAN_NAME]]

    return uuids


def _product_versions(channel, version) -> list[ProductVersion]:
    product_versions_cmd = f"productversion list --channel {channel} --version {version} --format json"

    print(f"Getting product versions for channel {channel} version {version}")
    print(product_versions_cmd)

    product_versions_response = _weebl_run(*shlex.split(product_versions_cmd))
   
    print(product_versions_response)
    product_versions = parse_response_lists(ProductVersion, product_versions_response)

    return product_versions


def start_release_test(channel, base, arch, revisions, version, priority):
    product_versions = _product_versions(channel, version)
    if product_versions:
        if len(product_versions) > 1:
            raise SQAFailure(f"the ({channel, base, arch}) is supposed to have only one product version for version {version}")

        print(
            f"using already defined product version {product_versions[0].uuid} to create TPI"
        )

        product_version = product_versions[0]
    else:
        product_version = _create_product_version(channel, base, arch, version)

    variables = {
        "base": base,
        "arch": arch,
        "channel": channel,
        **revisions
    }

    addon = _create_addon(version, variables)

    test_plan_instance = _create_test_plan_instance(str(product_version.uuid), str(addon.uuid), priority)
    print(f"Started release test for {channel} with UUID: {test_plan_instance.uuid}")

def _get_addon(name: str) -> Optional[Addon]:
    show_addon_cmd = f"addon show {name} --format json"

    print(f"Getting the {name} addon")
    print(show_addon_cmd)

    # TODO: remove this when SQA bug has been fixed
    # The SQA returns StopIteration in case of no addons
    try:
        show_addon_response = _weebl_run(*shlex.split(show_addon_cmd))
    except SQAFailure:
        return None

    print(show_addon_response)
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
        print(f"Using the previously defined addon for {version}")
        return addon

    print(f"No previous addon found. Creating a new one for {version}...")
    home_dir = os.path.expanduser("~")
    with tempfile.TemporaryDirectory(dir=home_dir, delete=False) as temp_dir:
        # the name of the addon dir must be 'addon'
        addon_dir = os.path.join(temp_dir, "addon")
        os.makedirs(addon_dir)
        
        config_dir = os.path.join(addon_dir, "config")
        os.makedirs(config_dir)  

        print(f"addon directory created at: {addon_dir}")

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

        print(f"Creating an addon for version {version}")
        print(create_addon_cmd)

        create_addon_response = _weebl_run(*shlex.split(create_addon_cmd))

    print(create_addon_response)
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
