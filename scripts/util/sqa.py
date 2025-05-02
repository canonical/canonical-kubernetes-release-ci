import datetime
import json
import re
import subprocess
from dataclasses import dataclass
from enum import Enum
from typing import Optional
from uuid import UUID

# Currently this is tribal knowledge, eventually this should appear in the SQA docs:
# https://canonical-weebl-tools.readthedocs-hosted.com/en/latest/products/index.html
K8S_OPERATOR_PRODUCT_UUID = "246d8ed3-b1dd-4875-a932-0cbc1b1c86b5"

# TODO: This is the test plan ID for "".
K8S_OPERATOR_TEST_PLAN_ID = "394fb5b6-1698-4226-bd3e-23b471ee1bd4"
K8S_OPERATOR_TEST_PLAN_NAME = "CanonicalK8s"


class SQAFailure(Exception):
    pass


@dataclass
class Product:
    name: str
    uuid: UUID

    @staticmethod
    def from_dict(data: dict) -> "Product":
        return Product(name=data["product.name"], uuid=UUID(data["product.uuid"]))


@dataclass
class ProductVersion:
    uuid: UUID
    product: Product
    version: str
    channel: str
    revision: str

    @staticmethod
    def from_dict(data: dict) -> "ProductVersion":
        product_data = {k: v for k, v in data.items() if k.startswith("product.")}

        return ProductVersion(
            uuid=UUID(data["uuid"]),
            product=Product.from_dict(product_data),
            version=data["version"],
            channel=data["channel"],
            revision=data["revision"],
        )


class TestPlanInstanceStatus(Enum):
    IN_PROGRESS = (1, "In Progress")
    SKIPPED = (2, "skipped")
    ERROR = (3, "error")
    ABORTED = (4, "aborted")
    FAILURE = (5, "failure")
    SUCCESS = (6, "success")
    UNKNOWN = (7, "unknown")
    PASSED = (8, "Passed")
    FAILED = (9, "Failed")
    RELEASED = (10, "Released")

    def __init__(self, state_id, name):
        self.state_id = state_id
        self.display_name = name

    def __str__(self):
        return f"{self.display_name}"

    @classmethod
    def from_name(cls, name):
        for state in cls:
            if state.display_name.lower() == name.lower():
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


@dataclass
class TestPlanInstance:
    test_plan: str
    created_at: datetime.datetime
    updated_at: datetime.datetime
    id: str
    effective_priority: float
    status: TestPlanInstanceStatus
    uuid: UUID
    product_under_test: str

    @staticmethod
    def from_dict(data: dict) -> "TestPlanInstance":
        return TestPlanInstance(
            test_plan=data["test_plan"],
            created_at=datetime.datetime.fromisoformat(
                data["created_at"].replace("Z", "+00:00")
            ),
            updated_at=datetime.datetime.fromisoformat(
                data["updated_at"].replace("Z", "+00:00")
            ),
            id=data["id"],
            effective_priority=float(data["effective_priority"]),
            status=TestPlanInstanceStatus.from_name(data["status"]),
            uuid=UUID(data["uuid"]),
            product_under_test=data["product_under_test"],
        )

    @property
    def version(self):
        # TODO: Version is only a subset of the product_under_test
        return self.product_under_test


def create_product_version(channel: str, revision: str) -> ProductVersion:
    product_version_cmd = f"weebl-tools.sqalab productversion add --format json --product-uuid {K8S_OPERATOR_PRODUCT_UUID} --channel {channel} --revision {revision}"

    print(f"Creating product version for channel {channel} revision {revision}...")
    print(product_version_cmd)

    try:
        product_version_response = subprocess.run(
            product_version_cmd.split(" "), check=True, capture_output=True, text=True
        )
    except subprocess.CalledProcessError as e:
        print("Creating product version failed:")
        print(e.stderr)
        raise SQAFailure

    print(product_version_response.stdout)
    product_versions = [
        ProductVersion.from_dict(item)
        for item in json.loads(product_version_response.stdout.strip())
    ]

    if not product_versions:
        print("Creating product version failed:")
        print("empty response")
        raise SQAFailure

    return product_versions[0]


def _create_test_plan_instance(product_version_uuid: str) -> TestPlanInstance:
    test_plan_instance_cmd = f"weebl-tools.sqalab testplaninstance add --format json --test_plan {K8S_OPERATOR_TEST_PLAN_ID} --status 'In Progress' --base_priority 3 --product_under_test {product_version_uuid}"
    matches = re.findall(r"'([^']*)'|(\S+)", test_plan_instance_cmd)
    refined_test_plan_instance_cmd = [m[0] if m[0] else m[1] for m in matches]

    print(f"Creating test plan instance for product version {product_version_uuid}...")
    print(refined_test_plan_instance_cmd)

    try:
        test_plan_instance_response = subprocess.run(
            refined_test_plan_instance_cmd, check=True, capture_output=True, text=True
        )
    except subprocess.CalledProcessError as e:
        print("Creating test plan instance failed:")
        print(e.stderr)
        raise SQAFailure

    print(json_str := test_plan_instance_response.stdout)
    end_index = json_str.rfind("]")

    if end_index != -1:
        json_str = json_str[: end_index + 1]

    test_plan_instances = [
        TestPlanInstance.from_dict(item) for item in json.loads(json_str.strip())
    ]

    if not test_plan_instances:
        print("Creating test plan instance failed:")
        print("empty response")
        raise SQAFailure

    return test_plan_instances[0]


def _delete_test_plan_instance(uuid: UUID) -> None:
    delete_test_plan_instance_cmd = f"weebl-tools.sqalab testplaninstance delete {uuid}"

    print(f"Deleting test plan instance {uuid}...")
    print(delete_test_plan_instance_cmd)

    test_plan_instance_response = subprocess.run(
        delete_test_plan_instance_cmd.split(" "),
        check=True,
        capture_output=True,
        text=True,
    )

    print(test_plan_instance_response.stdout)


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
    test_plan_instances_cmd = f"weebl-tools.sqalab testplaninstance list --format json --productversion-uuid {productversion_uuid} --status '{status.display_name.lower()}'"
    matches = re.findall(r"'([^']*)'|(\S+)", test_plan_instances_cmd)
    refined_test_plan_instances_cmd = [m[0] if m[0] else m[1] for m in matches]

    print(
        f"Getting test plan instances for product version {productversion_uuid} with status {status}..."
    )
    print(refined_test_plan_instances_cmd)

    try:
        test_plan_instances_response = subprocess.run(
            refined_test_plan_instances_cmd, check=True, capture_output=True, text=True
        )
    except subprocess.CalledProcessError as e:
        print("Getting test plan instances failed:")
        print(e.stderr)
        raise SQAFailure

    print(json_str := test_plan_instances_response.stdout)
    start_index = json_str.rfind("{")

    if start_index != -1:
        json_str = json_str[start_index:]

    if not (json_dict := json.loads(json_str.strip())):
        return []

    uuids = [UUID(item) for item in json_dict[K8S_OPERATOR_TEST_PLAN_NAME]]

    return uuids


def _product_versions(charm_name, channel, revision) -> list[ProductVersion]:
    product_versions_cmd = f"weebl-tools.sqalab productversion list --name {charm_name} --channel {channel} --revision {revision} --format json"

    print(f"Getting product versions for channel {channel} revision {revision}")
    print(product_versions_cmd)

    try:
        product_versions_response = subprocess.run(
            product_versions_cmd.split(" "), check=True, capture_output=True, text=True
        )
    except subprocess.CalledProcessError as e:
        print("Getting product versions failed:")
        print(e.stderr)
        raise SQAFailure

    print(product_versions_response.stdout)
    product_versions = [
        ProductVersion.from_dict(item)
        for item in json.loads(product_versions_response.stdout.strip())
    ]
    return product_versions


def start_release_test(charm_name, channel, revision):
    product_versions = _product_versions(charm_name, channel, revision)
    if product_versions:
        print(
            f"using already defined product version {product_versions[0].uuid} to create TPI"
        )
        product_version = product_versions[0]
    else:
        product_version = create_product_version(channel, revision)

    test_plan_instance = _create_test_plan_instance(str(product_version.uuid))
    print(f"Started release test for {channel} with UUID: {test_plan_instance.uuid}")
