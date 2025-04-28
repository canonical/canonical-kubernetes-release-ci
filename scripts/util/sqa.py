from dataclasses import dataclass
import datetime
from enum import Enum
import subprocess
from uuid import UUID
import json

from util import charmhub

# Currently this is tribal knowledge, eventually this should appear in the SQA docs:
# https://canonical-weebl-tools.readthedocs-hosted.com/en/latest/products/index.html
K8S_OPERATOR_PRODUCT_UUID = "3a8046a8-ef27-4ec7-a8a3-af6f470b96d7"

# TODO: Those are the test plan IDs for "Kuberentes release".
# Double check if those are really for the k8s-operator or charmed kubernetes.
K8S_OPERATOR_TEST_PLAN_IDS = [
    "a60b64e7-11c1-46ee-8926-217214bcdde5",
    "ba910113-f1dc-42c2-8e8a-3f5446b6dc09",
    "78865cd1-0f85-4d2c-8198-a383aecc4bf7"
]


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

    @classmethod
    def from_name(cls, name):
        for state in cls:
            if state.display_name.lower() == name.lower():
                return state
        raise ValueError(f"Invalid state name: {name}")


@dataclass
class TestPlanInstance:
    test_plan: str
    created_at: datetime
    updated_at: datetime
    id: str
    effective_priority: float
    status: TestPlanInstanceStatus
    uuid: UUID
    product_under_test: str

    @staticmethod
    def from_dict(data: dict) -> "TestPlanInstance":
        return TestPlanInstance(
            test_plan=data["test_plan"],
            created_at=datetime.fromisoformat(
                data["created_at"].replace("Z", "+00:00")
            ),
            updated_at=datetime.fromisoformat(
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

    @property
    def in_progress(self):
        return self.status == TestPlanInstanceStatus.IN_PROGRESS

    @property
    def succeeded(self):
        return self.status == TestPlanInstanceStatus.SUCCESS

    @property
    def failed(self):
        return self.status in [
            TestPlanInstanceStatus.ERROR,
            TestPlanInstanceStatus.ABORTED,
            TestPlanInstanceStatus.FAILURE,
        ]


def _create_product_version(revision: str) -> str:
    product_version_cmd = f"weebl-tools.sqalab productversion add --format json --product-uuid {K8S_OPERATOR_PRODUCT_UUID} --revision {revision}"

    print(f"Creating product version for revision {revision}...")
    print(product_version_cmd)

    product_version_response = subprocess.run(
        product_version_cmd.split(" "), check=True, capture_output=True, text=True
    )

    print(product_version_response.stdout)
    # TODO: Maybe make this a dataclass
    product_version = json.loads(product_version_response.stdout.strip())[0]["uuid"]
    print(f"Product version UUID: {product_version}")

    return product_version


def _create_test_plan_instance(product_version: str, channel: str) -> TestPlanInstance:
    test_plan_instance_cmd = f"weebl-tools.sqalab testplaninstance add --test-plan <your test plan> --product-under-test {product_version} --effective-priority <your priority>"

    print(f"Creating test plan instance for {channel}...")
    print(test_plan_instance_cmd)

    test_plan_instance_response = subprocess.run(
        test_plan_instance_cmd.split(" "), check=True, capture_output=True, text=True
    )

    print(test_plan_instance_response.stdout)
    return TestPlanInstance.from_dict(
        json.loads(test_plan_instance_response.stdout.strip())
    )


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


def current_release_run(channel, revision) -> TestPlanInstance:
    # TODO: Implement once SQA API is fixed.
    test_plan_instance_cmd = f"weebl-tools.sqalab testplaninstance list --product-under-test {channel} --revision {revision} --format json"

    print(f"Creating test plan instance for {channel}...")
    print(test_plan_instance_cmd)

    test_plan_instance_response = subprocess.run(
        test_plan_instance_cmd.split(" "), check=True, capture_output=True, text=True
    )

    print(test_plan_instance_response.stdout)
    return TestPlanInstance.from_dict(
        json.loads(test_plan_instance_response.stdout.strip())
    )


def start_release_test(channel):
    product_version = _create_product_version(channel)
    test_plan_instance = _create_test_plan_instance(channel, product_version)
    print(f"Started release test for {channel} with UUID: {test_plan_instance.uuid}")


def abort_release_test(channel):
    current_run = current_release_run(channel)
    _delete_test_plan_instance(current_run.uuid)
    print(f"Aborted release test for {channel} with UUID: {current_run.uuid}")
