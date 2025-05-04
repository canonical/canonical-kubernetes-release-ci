import datetime
import json
import re
import subprocess
import shlex
from pydantic import BaseModel, Field, field_validator, TypeAdapter
from enum import StrEnum
from typing import Optional
from uuid import UUID

# Currently this is tribal knowledge, eventually this should appear in the SQA docs:
# https://canonical-weebl-tools.readthedocs-hosted.com/en/latest/products/index.html
K8S_OPERATOR_PRODUCT_UUID = "246d8ed3-b1dd-4875-a932-0cbc1b1c86b5"

K8S_OPERATOR_TEST_PLAN_ID = "394fb5b6-1698-4226-bd3e-23b471ee1bd4"
K8S_OPERATOR_TEST_PLAN_NAME = "CanonicalK8s"


class SQAFailure(Exception):
    pass

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
            TestPlanInstanceStatus.ERROR,
            TestPlanInstanceStatus.FAILURE,
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
    def parse_datetime(cls, v: str) -> datetime:
        return datetime.datetime.fromisoformat(v.replace("Z", "+00:00"))

    @field_validator("status", mode="before")
    @classmethod
    def parse_status(cls, v: str) -> TestPlanInstanceStatus:
        return TestPlanInstanceStatus.from_name(v)


def _create_product_version(channel: str, revision: str) -> ProductVersion:
    product_version_cmd = f"productversion add --format json --product-uuid {K8S_OPERATOR_PRODUCT_UUID} --channel {channel} --revision {revision}"

    print(f"Creating product version for channel {channel} revision {revision}...")
    print(product_version_cmd)

    product_version_response = _weebl_run(*shlex.split(product_version_cmd))

    print(product_version_response)
    adapter = TypeAdapter(list[ProductVersion])
    product_versions = adapter.validate_json(product_version_response.strip())

    if not product_versions:
        print("Creating product version failed:")
        print("empty response")
        raise SQAFailure

    return product_versions[0]


def _create_test_plan_instance(product_version_uuid: str) -> TestPlanInstance:
    test_plan_instance_cmd = f"testplaninstance add --format json --test_plan {K8S_OPERATOR_TEST_PLAN_ID} --status 'In Progress' --base_priority 3 --product_under_test {product_version_uuid}"

    print(f"Creating test plan instance for product version {product_version_uuid}...")
    print(test_plan_instance_cmd)

    test_plan_instance_response = _weebl_run(*shlex.split(test_plan_instance_cmd))

    print(json_str := test_plan_instance_response)
    end_index = json_str.rfind("]")

    if end_index != -1:
        json_str = json_str[: end_index + 1]

    adapter = TypeAdapter(list[TestPlanInstance])
    test_plan_instances = adapter.validate_json(json_str.strip())

    if not test_plan_instances:
        print("Creating test plan instance failed:")
        print("empty response")
        raise SQAFailure

    return test_plan_instances[0]


def current_test_plan_instance_status(
    charm_name, channel, revision
) -> Optional[TestPlanInstanceStatus]:
    """
    First try to get any passed TPIs for the (channel, revision)
    If no passed TPI found, try to get in progress TPIs
    If no in progress TPI found, try to get failed/(in-)error TPIs
    If no failed TPI found, return None
    The aborted TPIs are ignored since they don't semantically hold
    any information about the state of a track
    """
    product_versions = _product_versions(charm_name, channel, revision)

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

    in_error_test_plan_instances = _joined_test_plan_instances(
        product_versions, TestPlanInstanceStatus.ERROR
    )
    if in_error_test_plan_instances:
        return TestPlanInstanceStatus.ERROR

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


def _product_versions(charm_name, channel, revision) -> list[ProductVersion]:
    product_versions_cmd = f"productversion list --name {charm_name} --channel {channel} --revision {revision} --format json"

    print(f"Getting product versions for channel {channel} revision {revision}")
    print(product_versions_cmd)

    product_versions_response = _weebl_run(*shlex.split(product_versions_cmd))
   
    print(product_versions_response)
    adapter = TypeAdapter(list[ProductVersion])
    product_versions = adapter.validate_json(product_versions_response.strip())
    return product_versions


def start_release_test(charm_name, channel, revision):
    product_versions = _product_versions(charm_name, channel, revision)
    if product_versions:
        print(
            f"using already defined product version {product_versions[0].uuid} to create TPI"
        )
        product_version = product_versions[0]
    else:
        product_version = _create_product_version(channel, revision)

    test_plan_instance = _create_test_plan_instance(str(product_version.uuid))
    print(f"Started release test for {channel} with UUID: {test_plan_instance.uuid}")

def _weebl_run(*args, **kwds) -> str:
    kwds = {"text": True, "check": True, "capture_output": True, **kwds}
    try:
        response = subprocess.run(["weebl-tools.sqalab", *args], **kwds)
    except subprocess.CalledProcessError as e:
        print(f"{args[0]} failed:")
        print(e.stderr)
        raise SQAFailure
    return response.stdout
